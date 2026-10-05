"""A draft's shopping list (spec/requirements.md "Shopping list"): stage 1 scales
each picked meal's ingredients to its servings (here, deterministic); stage 2 is
the agent combining them into one list (a `build_list` job) through the
shopping-list tools, which check each change, and the worker checks and saves.
The order's list is its newest build's items."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from castle_chef_agent.shopping import (
    Aisle,
    IngredientLine,
    ListJob,
    Staple,
    list_problems,
    load_draft,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from castle_chef import prompts
from castle_chef.jobs import newest_job
from castle_chef.models import (
    GroceryOrderMeal,
    Job,
    JobKind,
    JobStatus,
    ListPick,
    ListSource,
    Meal,
    ShoppingListItem,
)

# What each aisle holds, for the agent (in the store's order, as the list shows them).
AISLE_MEANINGS: dict[Aisle, str] = {
    Aisle.PRODUCE: "fresh fruit, vegetables, and herbs",
    Aisle.MEAT_SEAFOOD: "fresh or chilled meat, poultry, and seafood",
    Aisle.DAIRY_EGGS: "milk, cheese, yogurt, butter, cream, and eggs",
    Aisle.FROZEN: "anything bought frozen",
    Aisle.PANTRY: "bread, grains, pasta, canned and dried foods, spices, oils, sauces, "
    "and other shelf-stable groceries",
    Aisle.OTHER: "anything none of the others fits, such as tofu or prepared hummus",
}


class ListEntry(BaseModel):
    """An item or staple of a finished list, with the numbers of the meals it's for."""

    name: str
    quantity: float | None = None
    unit: str | None = None
    aisle: Aisle
    meals: list[int] = Field(min_length=1)
    on_hand: bool = False


class InvalidList(Exception):
    """The agent's list breaks a rule; the message says which."""


# Stage 1.


@dataclass(frozen=True)
class ScaledMeal:
    """A picked meal's ingredients, scaled to the servings picked."""

    number: int  # how the prompt and the agent refer to it
    meal: Meal
    servings: int
    lines: list[IngredientLine]


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


def _letters(index: int) -> str:
    """a, b, ..., z, aa, ab, ...: a line's letter within its meal."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters


def scaled_meals(session: Session, source: ListSource) -> list[ScaledMeal]:
    """Stage 1: each meal of the source (numbered from 1, in its order) with its
    ingredients scaled by servings picked / servings the recipe makes."""
    scaled = []
    for number, pick in enumerate(source.picks, start=1):
        meal = session.get_one(Meal, pick.meal_id)
        factor = pick.servings / meal.servings
        lines = []
        for index, ingredient in enumerate(meal.ingredients):
            amount = (
                []
                if ingredient.quantity is None
                else [_amount(ingredient.quantity * factor), *filter(None, [ingredient.unit])]
            )
            text = " ".join([*amount, ingredient.name])
            lines.append(
                IngredientLine(
                    id=f"{number}{_letters(index)}",
                    meal=number,
                    text=f"{text}, {ingredient.preparation}" if ingredient.preparation else text,
                    has_amount=ingredient.quantity is not None,
                )
            )
        scaled.append(ScaledMeal(number=number, meal=meal, servings=pick.servings, lines=lines))
    return scaled


def build_list_prompt(scaled: list[ScaledMeal]) -> str:
    """The agent's instructions for a build_list job: stage 1's lines to combine."""
    meals = "\n\n".join(
        f"Meal {meal.number}: {meal.meal.name} (for {meal.servings})\n"
        + "\n".join(f"- {line.id}: {line.text}" for line in meal.lines)
        for meal in scaled
    )
    return prompts.fill(
        "build_list",
        meals=meals,
        staples=", ".join(Staple),
        aisles="\n".join(
            f"- {aisle.value}: {meaning}" for aisle, meaning in AISLE_MEANINGS.items()
        ),
    )


def list_job(scaled: list[ScaledMeal]) -> ListJob:
    return ListJob(lines=[line for meal in scaled for line in meal.lines])


def finished_list(job_dir: Path, job: ListJob) -> list[ListEntry]:
    """The agent's finished list for the job's lines (the worker's, not the
    agent-writable job.json), its lines turned into meal numbers; raises
    InvalidList if it isn't done."""
    draft = load_draft(job_dir)
    if problems := list_problems(job, draft):
        raise InvalidList(" ".join(problems))
    meal_of = {line.id: line.meal for line in job.lines}

    def meals(lines: list[str]) -> list[int]:
        return sorted({meal_of[line] for line in lines})

    return [
        ListEntry(
            name=item.name,
            quantity=item.quantity,
            unit=item.unit,
            aisle=item.aisle,
            meals=meals(item.lines),
        )
        for item in draft.items
    ] + [
        ListEntry(
            name=staple.staple.value,
            aisle=Aisle.PANTRY,
            meals=meals(staple.lines),
            on_hand=True,
        )
        for staple in draft.staples
    ]


def add_items(session: Session, job_id: UUID, entries: list[ListEntry], source: ListSource) -> None:
    """Stage the finished list's items and staples for their job; the caller commits."""
    for position, entry in enumerate(entries):
        session.add(
            ShoppingListItem(
                job_id=job_id,
                position=position,
                name=entry.name,
                quantity=None if entry.on_hand else entry.quantity,
                unit=None if entry.on_hand else entry.unit,
                aisle=entry.aisle,
                meal_ids=sorted({source.picks[number - 1].meal_id for number in entry.meals}),
                on_hand=entry.on_hand,
            )
        )


# The order's list.


def newest_build(session: Session, order_id: UUID) -> Job | None:
    return newest_job(session, order_id, JobKind.BUILD_LIST)


def newest_built(session: Session, order_id: UUID) -> Job | None:
    """The newest build that succeeded: its items are the list shown."""
    return newest_job(session, order_id, JobKind.BUILD_LIST, JobStatus.SUCCEEDED)


def ordered_quantity(item: ShoppingListItem) -> float | None:
    """How much of the item is ordered: the member's amount, or else the recipes'."""
    return item.quantity if item.changed_quantity is None else item.changed_quantity


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


def current_build(session: Session, order_id: UUID) -> Job | None:
    """The order's list for its picks: its newest build, if that succeeded from
    the picks and servings the order has now; None if there's no such list."""
    newest = newest_build(session, order_id)
    return newest if is_current(session, order_id, newest) else None


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
