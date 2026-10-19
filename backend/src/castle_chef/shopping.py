"""One list-building agent confirms each recipe's cooking amounts and steps,
then combines confirmed lines into a shopping list. The build owns the immutable
prepared recipes and list items, saved atomically on success."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from castle_chef_agent.shopping import (
    Aisle,
    IngredientLine,
    ListJob,
    PreparedRecipe,
    PreparedRecipes,
    RecipeContext,
    RecipeIngredient,
    RecipeStep,
    Staple,
    confirmed_recipes,
    list_problems,
    load_draft,
    recipe_lines,
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
    more_alternatives_query: str | None = None
    quantity: float | None = None
    unit: str | None = None
    aisle: Aisle
    meals: list[int] = Field(min_length=1)
    on_hand: bool = False


class InvalidList(Exception):
    """The agent's list breaks a rule; the message says which."""


# Stage 1.


@dataclass(frozen=True)
class RecipeMeal:
    """A picked original recipe and its requested yield."""

    number: int  # how the prompt and the agent refer to it
    meal: Meal
    servings: int
    context: RecipeContext

    @property
    def lines(self) -> list[IngredientLine]:
        return recipe_lines(self.context, [i.quantity for i in self.context.ingredients])


def current_source(session: Session, order_id: UUID) -> ListSource:
    """The draft's picks and servings now, in a fixed order."""
    picks = session.exec(
        select(GroceryOrderMeal)
        .where(GroceryOrderMeal.grocery_order_id == order_id)
        .order_by(col(GroceryOrderMeal.meal_id))
    ).all()
    return ListSource(
        picks=[
            ListPick(
                meal_id=pick.meal_id,
                servings=pick.servings,
                revision=session.get_one(Meal, pick.meal_id).revision,
            )
            for pick in picks
        ]
    )


def recipe_meals(session: Session, source: ListSource) -> list[RecipeMeal]:
    meals: list[RecipeMeal] = []
    for number, pick in enumerate(source.picks, start=1):
        meal = session.get_one(Meal, pick.meal_id)
        if pick.revision != meal.revision:
            raise InvalidList("The recipe changed; build a new shopping list.")
        context = RecipeContext(
            number=number,
            meal_id=meal.id,
            base_revision=meal.revision,
            base_servings=meal.servings,
            servings=pick.servings,
            ingredients=[
                RecipeIngredient.model_validate(i, from_attributes=True) for i in meal.ingredients
            ],
            steps=[RecipeStep.model_validate(s.model_dump()) for s in meal.steps],
        )
        meals.append(RecipeMeal(number=number, meal=meal, servings=pick.servings, context=context))
    return meals


def build_list_prompt(recipes: list[RecipeMeal]) -> str:
    def render(recipe: RecipeMeal) -> str:
        context = recipe.context
        ingredients = "\n".join(
            f"- {line.id}: {ingredient.model_dump_json()}"
            + (
                ""
                if context.ready or ingredient.quantity is None
                else " [estimate "
                f"{ingredient.quantity * context.servings / context.base_servings:.2f}]"
            )
            for line, ingredient in zip(recipe.lines, context.ingredients, strict=True)
        )
        steps = "\n".join(
            f"{n}. {step.text} [ingredients {step.ingredients}; timer {step.timer_minutes}]"
            for n, step in enumerate(context.steps, start=1)
        )
        return (
            f"Meal {recipe.number}: {recipe.meal.name} (id {recipe.meal.id}, "
            f"revision {context.base_revision}; original yield {context.base_servings}; "
            f"chosen servings {context.servings})\n"
            f"Times: {recipe.meal.active_time_minutes} min active; "
            f"{recipe.meal.total_time_minutes} min total\n{ingredients}\nSteps:\n{steps}"
        )

    return prompts.fill(
        "build_list",
        meals="\n\n".join(render(recipe) for recipe in recipes),
        staples=", ".join(Staple),
        aisles="\n".join(f"- {a.value}: {meaning}" for a, meaning in AISLE_MEANINGS.items()),
    )


def list_job(recipes: list[RecipeMeal]) -> ListJob:
    return ListJob(
        lines=[line for r in recipes for line in r.lines], recipes=[r.context for r in recipes]
    )


def validate_prepared(
    recipes: list[RecipeMeal],
    prepared: PreparedRecipes,
) -> None:
    """Check pinned source and fixed positions again at the persistence/read boundary."""
    checked = PreparedRecipes.model_validate(prepared.model_dump())
    if len(checked.recipes) != len(recipes):
        raise InvalidList("Prepared recipes do not cover the build's picks.")
    for base, result in zip(recipes, checked.recipes, strict=True):
        context = base.context
        if (result.meal_id, result.base_revision, result.servings) != (
            context.meal_id,
            context.base_revision,
            context.servings,
        ):
            raise InvalidList("Prepared recipes do not match the build's pinned source.")
        if (
            len(result.quantities) != len(context.ingredients)
            or len(result.steps) != len(context.steps)
            or any(
                (q is None) != (i.quantity is None)
                for q, i in zip(result.quantities, context.ingredients, strict=True)
            )
        ):
            raise InvalidList("Prepared recipe positions or amount masks changed.")
        if not checked.legacy_scaled and any(q is not None and q <= 0 for q in result.quantities):
            raise InvalidList("Confirmed recipe quantities must be positive.")
        if (
            context.ready
            and not checked.legacy_scaled
            and (
                result.quantities != [i.quantity for i in context.ingredients]
                or result.steps != [s.text for s in context.steps]
            )
        ):
            raise InvalidList("An identity recipe changed.")


def prepared_meals(session: Session, build: Job) -> list[tuple[RecipeMeal, PreparedRecipe]]:
    if build.list_source is None or build.prepared_recipes is None:
        raise InvalidList(f"Build {build.id} has no confirmed recipe snapshot.")
    recipes = recipe_meals(session, build.list_source)
    validate_prepared(recipes, build.prepared_recipes)
    return list(zip(recipes, build.prepared_recipes.recipes, strict=True))


@dataclass(frozen=True)
class FinishedList:
    items: list[ListEntry]
    prepared: PreparedRecipes


def finished_list(job_dir: Path, job: ListJob) -> FinishedList:
    """The agent's finished list for the job's lines (the worker's, not the
    agent-writable job.json), its lines turned into meal numbers; raises
    InvalidList if it isn't done."""
    draft = load_draft(job_dir)
    if problems := list_problems(job, draft):
        raise InvalidList(" ".join(problems))
    meal_of = {line.id: line.meal for line in job.lines}

    def meals(lines: list[str]) -> list[int]:
        return sorted({meal_of[line] for line in lines})

    items = [
        ListEntry(
            name=item.name,
            more_alternatives_query=item.more_alternatives_query,
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

    return FinishedList(items=items, prepared=confirmed_recipes(job, draft))


def add_items(session: Session, job_id: UUID, entries: list[ListEntry], source: ListSource) -> None:
    """Stage the finished list's items and staples for their job; the caller commits."""
    for position, entry in enumerate(entries):
        session.add(
            ShoppingListItem(
                job_id=job_id,
                position=position,
                name=entry.name,
                more_alternatives_query=entry.more_alternatives_query,
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


def source_current(session: Session, order_id: UUID, build: Job | None) -> bool:
    """Whether the pinned inputs still match, independent of build status."""
    return build is not None and build.list_source == current_source(session, order_id)


def is_current(session: Session, order_id: UUID, newest: Job | None) -> bool:
    """Whether the order's list is ready for its picks: its newest build
    succeeded, from the picks and servings the order has now."""
    return (
        newest is not None
        and newest.status == JobStatus.SUCCEEDED
        and source_current(session, order_id, newest)
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
