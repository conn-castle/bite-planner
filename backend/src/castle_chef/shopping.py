"""A draft's shopping list (spec/requirements.md "Shopping list"): stage 1 scales
each picked meal's ingredients to its servings (here, deterministic); stage 2 is
the agent combining them into one list (a `build_list` job), which the worker
checks and saves. The order's list is its newest build's items."""

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from castle_chef.models import (
    Aisle,
    GroceryOrderMeal,
    Job,
    JobKind,
    JobStatus,
    ListPick,
    ListSource,
    Meal,
    ShoppingListItem,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Assumed to be in every kitchen: never on a list (app-wide, not editable).
ASSUMED_ON_HAND = ("salt", "pepper", "olive oil", "vegetable oil", "water")


class ListItemInput(BaseModel):
    """One item of the combined list, as the agent adds it (`castle-chef list add`)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="What to buy, as a shopper would look for it, e.g. 'yellow onions', "
        "'boneless chicken thighs'. Never include the quantity or unit.",
    )
    quantity: float | None = Field(
        default=None,
        gt=0,
        description="The total needed across the meals, in `unit`, or the count when `unit` "
        "is null. Null only when the recipes give no amount (e.g. 'to taste').",
    )
    unit: str | None = Field(
        default=None,
        description="One unit for the whole item, e.g. 'lb', 'oz', 'cup', 'tbsp'. Null for "
        "items counted whole (onions, eggs, tortillas).",
    )
    aisle: Aisle
    meals: list[int] = Field(
        min_length=1, description="The numbers of the meals (from the list above) that use it."
    )
    on_hand: bool = Field(
        default=False,
        description="True only for the items assumed on hand, named exactly as listed above.",
    )


class InvalidList(Exception):
    """The agent's list breaks a rule; the message says which."""


# Stage 1.


@dataclass(frozen=True)
class ScaledMeal:
    """A picked meal's ingredients, scaled to the servings picked."""

    number: int  # how the prompt and the agent refer to it
    meal: Meal
    servings: int
    lines: list[str]


def current_source(session: Session, order_id: UUID) -> ListSource:
    """The draft's picks and servings now, in a fixed order."""
    picks = session.exec(
        select(GroceryOrderMeal)
        .where(GroceryOrderMeal.grocery_order_id == order_id)
        .order_by(col(GroceryOrderMeal.meal_id))
    ).all()
    return ListSource(
        picks=[ListPick(meal_id=pick.meal_id, servings=pick.servings) for pick in picks]
    )


def _amount(quantity: float) -> str:
    return f"{round(quantity, 2):g}"


def scaled_meals(session: Session, source: ListSource) -> list[ScaledMeal]:
    """Stage 1: each meal of the source (numbered from 1, in its order) with its
    ingredients scaled by servings picked / servings the recipe makes."""
    scaled = []
    for number, pick in enumerate(source.picks, start=1):
        meal = session.get_one(Meal, pick.meal_id)
        factor = pick.servings / meal.servings
        lines = []
        for ingredient in meal.ingredients:
            amount = (
                []
                if ingredient.quantity is None
                else [_amount(ingredient.quantity * factor), *filter(None, [ingredient.unit])]
            )
            line = " ".join([*amount, ingredient.name])
            lines.append(f"{line}, {ingredient.preparation}" if ingredient.preparation else line)
        scaled.append(ScaledMeal(number=number, meal=meal, servings=pick.servings, lines=lines))
    return scaled


def build_list_prompt(session: Session, job: Job) -> str:
    """The agent's instructions for a build_list job: stage 1's lines to combine."""
    if job.list_source is None:
        raise ValueError(f"Job {job.id} has no list source.")
    meals = "\n\n".join(
        f"{scaled.number}. {scaled.meal.name} (for {scaled.servings}):\n"
        + "\n".join(f"- {line}" for line in scaled.lines)
        for scaled in scaled_meals(session, job.list_source)
    )
    return Template(PROMPTS_DIR.joinpath("build_list.md").read_text()).substitute(
        meals=meals,
        on_hand=", ".join(ASSUMED_ON_HAND),
        aisles=", ".join(aisle.value for aisle in Aisle),
        item_schema=json.dumps(ListItemInput.model_json_schema(), indent=2),
    )


# The agent's items, as `castle-chef list add` saves them for the worker.


def items_dir(job_dir: Path) -> Path:
    return job_dir / "items"


def save_item(job_dir: Path, item: ListItemInput) -> Path:
    """Save a validated item as the next numbered JSON file (the agent may add
    several at once, so each claims its number with an exclusive create)."""
    directory = items_dir(job_dir)
    directory.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = directory / f"{number:03d}.json"
        try:
            with path.open("x") as file:
                file.write(item.model_dump_json(indent=2))
            return path
        except FileExistsError:
            number += 1


def load_items(job_dir: Path) -> list[ListItemInput]:
    return [
        ListItemInput.model_validate_json(path.read_text())
        for path in sorted(items_dir(job_dir).glob("*.json"))
    ]


def check_items(items: list[ListItemInput], source: ListSource) -> None:
    """Raise InvalidList unless the items keep the list's rules: valid meal
    numbers; the assumed-on-hand items only as themselves, each at most once;
    something to buy."""
    meals = len(source.picks)
    on_hand_seen: set[str] = set()
    for item in items:
        name = item.name.strip().lower()
        if bad := [number for number in item.meals if not 1 <= number <= meals]:
            raise InvalidList(f"'{item.name}' is for meals {bad}; there are {meals}.")
        if item.on_hand:
            if name not in ASSUMED_ON_HAND:
                raise InvalidList(f"'{item.name}' isn't assumed on hand; buy it.")
            if name in on_hand_seen:
                raise InvalidList(f"'{item.name}' is on hand twice.")
            on_hand_seen.add(name)
        elif name in ASSUMED_ON_HAND:
            raise InvalidList(f"'{item.name}' is assumed on hand; mark it on_hand.")
    if all(item.on_hand for item in items):
        raise InvalidList("The list has nothing to buy.")


def add_items(
    session: Session, job_id: UUID, items: list[ListItemInput], source: ListSource
) -> None:
    """Stage the checked items for their job; the caller commits."""
    for position, item in enumerate(items):
        session.add(
            ShoppingListItem(
                job_id=job_id,
                position=position,
                name=item.name.strip().lower() if item.on_hand else item.name.strip(),
                quantity=None if item.on_hand else item.quantity,
                unit=None if item.on_hand else item.unit,
                aisle=item.aisle,
                meal_ids=sorted({source.picks[number - 1].meal_id for number in item.meals}),
                on_hand=item.on_hand,
            )
        )


# The order's list.


def newest_build(session: Session, order_id: UUID) -> Job | None:
    return session.exec(
        select(Job)
        .where(Job.grocery_order_id == order_id, Job.kind == JobKind.BUILD_LIST)
        .order_by(col(Job.created_at).desc(), col(Job.id).desc())
        .limit(1)
    ).first()


def newest_built(session: Session, order_id: UUID) -> Job | None:
    """The newest build that succeeded: its items are the list shown."""
    return session.exec(
        select(Job)
        .where(
            Job.grocery_order_id == order_id,
            Job.kind == JobKind.BUILD_LIST,
            Job.status == JobStatus.SUCCEEDED,
        )
        .order_by(col(Job.created_at).desc(), col(Job.id).desc())
        .limit(1)
    ).first()


def items_of(session: Session, job_id: UUID) -> list[ShoppingListItem]:
    """A build's items, by aisle (in the store's order), then the agent's order."""
    aisles = list(Aisle)
    items = session.exec(
        select(ShoppingListItem)
        .where(ShoppingListItem.job_id == job_id)
        .order_by(col(ShoppingListItem.position))
    ).all()
    return sorted(items, key=lambda item: aisles.index(item.aisle))


def is_current(session: Session, order_id: UUID, newest: Job | None) -> bool:
    """Whether the order's list is ready for its picks: its newest build
    succeeded, from the picks and servings the order has now."""
    return (
        newest is not None
        and newest.status == JobStatus.SUCCEEDED
        and newest.list_source == current_source(session, order_id)
    )


def placed_lists(session: Session, order_ids: list[UUID]) -> dict[UUID, list[ShoppingListItem]]:
    """Each placed order's list (its newest succeeded build's items), in two queries."""
    lists: dict[UUID, list[ShoppingListItem]] = {order_id: [] for order_id in order_ids}
    if not order_ids:
        return lists
    builds = session.exec(
        select(Job.grocery_order_id, Job.id)
        .where(
            col(Job.grocery_order_id).in_(order_ids),
            Job.kind == JobKind.BUILD_LIST,
            Job.status == JobStatus.SUCCEEDED,
        )
        .order_by(col(Job.grocery_order_id), col(Job.created_at).desc(), col(Job.id).desc())
        .distinct(col(Job.grocery_order_id))
    ).all()
    order_of = {job_id: order_id for order_id, job_id in builds}
    aisles = list(Aisle)
    items = session.exec(
        select(ShoppingListItem)
        .where(col(ShoppingListItem.job_id).in_(list(order_of)))
        .order_by(col(ShoppingListItem.position))
    ).all()
    for item in sorted(items, key=lambda item: aisles.index(item.aisle)):
        lists[order_of[item.job_id]].append(item)
    return lists
