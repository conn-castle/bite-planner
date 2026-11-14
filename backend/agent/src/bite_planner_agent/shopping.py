"""The shopping list the agent makes (the shopping-list tools): items to buy
and the staples the meals use, each covering confirmed ingredient lines, kept in the
job directory for the worker. Every change is checked as it's made."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from bite_planner_agent.jobfiles import locked, read_context, replace_file
from bite_planner_agent.names import key


class Aisle(StrEnum):
    """Where a shopping list item is in the store (the mockup's aisles, in order)."""

    PRODUCE = "produce"
    MEAT_SEAFOOD = "meat_seafood"
    DAIRY_EGGS = "dairy_eggs"
    FROZEN = "frozen"
    PANTRY = "pantry"
    OTHER = "other"  # not in the mockup: for what fits no other aisle


class Staple(StrEnum):
    """Assumed to be in every kitchen (app-wide, not editable): excluded from
    items to buy and recipe ingredient counts.
    The agent records the ones the meals use, shown only on request."""

    SALT = "salt"
    PEPPER = "pepper"
    OLIVE_OIL = "olive oil"
    VEGETABLE_OIL = "vegetable oil"
    WATER = "water"


# Canonical staple names and aliases: excluded from buying and recipe ingredient counts.
STAPLE_NAMES: dict[str, Staple] = {
    key(name): staple
    for name, staple in [
        *((staple.value, staple) for staple in Staple),
        ("kosher salt", Staple.SALT),
        ("sea salt", Staple.SALT),
        ("table salt", Staple.SALT),
        ("black pepper", Staple.PEPPER),
        ("ground black pepper", Staple.PEPPER),
        ("ground pepper", Staple.PEPPER),
        ("extra virgin olive oil", Staple.OLIVE_OIL),
        ("warm water", Staple.WATER),
    ]
}


AlternativesQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ListItemInput(BaseModel):
    """An item to buy, as the agent adds it (`add_item`)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    more_alternatives_query: AlternativesQuery
    # The total the lines need, in `unit` (a count when `unit` is null); null
    # when their recipes give no amount.
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1)
    aisle: Aisle
    lines: list[str] = Field(min_length=1)


class StapleInput(BaseModel):
    """A staple the meals use (`add_staple`)."""

    model_config = ConfigDict(extra="forbid")

    staple: Staple
    lines: list[str] = Field(min_length=1)


class RecipeIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    quantity: float | None = Field(gt=0, allow_inf_nan=False)
    unit: str | None = None
    preparation: str | None = None


class RecipeStep(BaseModel):
    text: str
    timer_minutes: int | None = None
    ingredients: list[int] = []


class RecipeAmounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    quantity: float | None = Field(gt=0, allow_inf_nan=False, strict=True)


class RecipeAdaptation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal: int = Field(ge=1)
    ingredients: list[RecipeAmounts]
    steps: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]]


class PreparedRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: UUID
    base_revision: int = Field(ge=0)
    servings: int = Field(gt=0)
    quantities: list[Annotated[float, Field(ge=0, allow_inf_nan=False)] | None]
    steps: list[Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]]


class PreparedRecipes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipes: list[PreparedRecipe]
    legacy_scaled: bool = False


class RecipeContext(BaseModel):
    number: int
    meal_id: UUID
    base_revision: int
    base_servings: int
    servings: int
    ingredients: list[RecipeIngredient]
    steps: list[RecipeStep]

    @property
    def ready(self) -> bool:
        return self.base_servings == self.servings


def letters(index: int) -> str:
    """a through z, aa, ab, ...: stable ingredient positions."""
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("a") + remainder) + result
    return result


def recipe_lines(recipe: RecipeContext, quantities: list[float | None]) -> list[IngredientLine]:
    lines: list[IngredientLine] = []
    for index, (ingredient, quantity) in enumerate(
        zip(recipe.ingredients, quantities, strict=True)
    ):
        amount = (
            []
            if quantity is None
            else [
                str(int(quantity)) if quantity.is_integer() else str(quantity),
                *filter(None, [ingredient.unit]),
            ]
        )
        text = " ".join([*amount, ingredient.name])
        lines.append(
            IngredientLine(
                id=f"{recipe.number}{letters(index)}",
                meal=recipe.number,
                text=f"{text}, {ingredient.preparation}" if ingredient.preparation else text,
                has_amount=quantity is not None,
            )
        )
    return lines


class ListDraft(BaseModel):
    """The list the agent has made so far (its tools keep it in the job directory)."""

    model_config = ConfigDict(extra="forbid")

    recipes: list[RecipeAdaptation] = []
    items: list[ListItemInput] = []
    staples: list[StapleInput] = []


class IngredientLine(BaseModel):
    """One stable ingredient position of a picked meal."""

    id: str  # e.g. "2c": the meal's number and the line's letter
    meal: int  # the meal's number, from 1
    text: str
    has_amount: bool  # its recipe gives an amount (not "to taste")


class ListJob(BaseModel):
    """What a build_list job's tools check against (the worker writes it)."""

    lines: list[IngredientLine]
    recipes: list[RecipeContext] = []


class ListRefused(Exception):
    """A tool's change breaks a rule; the message says which, for the agent."""


def _draft_path(job_dir: Path) -> Path:
    return job_dir / "list.json"


def load_draft(job_dir: Path) -> ListDraft:
    path = _draft_path(job_dir)
    return ListDraft.model_validate_json(path.read_text()) if path.exists() else ListDraft()


@contextmanager
def _changing(job_dir: Path) -> Iterator[ListDraft]:
    """The list so far, to change under the job directory's lock: saved when the
    block ends, unless it raises."""
    with locked(job_dir):
        draft = load_draft(job_dir)
        yield draft
        replace_file(_draft_path(job_dir), draft.model_dump_json(indent=2))


def _covered(draft: ListDraft) -> Iterator[tuple[str, str]]:
    """Each line the draft covers, with the item or staple covering it."""
    for item in draft.items:
        yield from ((line, f'"{item.name}"') for line in item.lines)
    for staple in draft.staples:
        yield from ((line, f"the staple {staple.staple.value}") for line in staple.lines)


def confirmed_recipes(job: ListJob, draft: ListDraft) -> PreparedRecipes:
    """Validate against the caller's context; identities always come from the base."""
    known = {recipe.number: recipe for recipe in job.recipes}
    adapted: dict[int, RecipeAdaptation] = {}
    for raw in draft.recipes:
        adaptation = RecipeAdaptation.model_validate(raw.model_dump())
        recipe = known.get(adaptation.meal)
        if recipe is None:
            raise ListRefused(f"There is no meal {adaptation.meal}.")
        if recipe.ready:
            raise ListRefused(f"Meal {recipe.number} is already ready from its original recipe.")
        if adaptation.meal in adapted:
            raise ListRefused(f"Meal {recipe.number} was confirmed twice.")
        if [i.name for i in adaptation.ingredients] != [i.name for i in recipe.ingredients]:
            raise ListRefused(f"Meal {recipe.number}: keep every ingredient's name and order.")
        if any(
            (a.quantity is None) != (b.quantity is None)
            for a, b in zip(adaptation.ingredients, recipe.ingredients, strict=True)
        ):
            raise ListRefused(f"Meal {recipe.number}: keep which ingredients have amounts.")
        if len(adaptation.steps) != len(recipe.steps):
            raise ListRefused(f"Meal {recipe.number}: supply every step in its original order.")
        adapted[recipe.number] = adaptation
    return PreparedRecipes(
        recipes=[
            PreparedRecipe(
                meal_id=recipe.meal_id,
                base_revision=recipe.base_revision,
                servings=recipe.servings,
                quantities=[i.quantity for i in recipe.ingredients]
                if recipe.ready
                else [i.quantity for i in adapted[recipe.number].ingredients],
                steps=[step.text for step in recipe.steps]
                if recipe.ready
                else adapted[recipe.number].steps,
            )
            for recipe in job.recipes
            if recipe.ready or recipe.number in adapted
        ]
    )


def confirmed_lines(job: ListJob, draft: ListDraft) -> list[IngredientLine]:
    prepared = {r.meal_id: r for r in confirmed_recipes(job, draft).recipes}
    # Lines are the structural coverage inventory; recipe context determines readiness.
    if not job.recipes:
        return job.lines
    return [
        line
        for recipe in job.recipes
        if recipe.meal_id in prepared
        for line in recipe_lines(recipe, prepared[recipe.meal_id].quantities)
    ]


def adapt_recipe(job_dir: Path, adaptation: RecipeAdaptation) -> list[IngredientLine]:
    job = read_context(job_dir, ListJob)
    with _changing(job_dir) as draft:
        covered = dict(_covered(draft))
        if any(line.meal == adaptation.meal and line.id in covered for line in job.lines):
            raise ListRefused(
                "Remove the meal's covered items and staples before adapting it again."
            )
        draft.recipes = [r for r in draft.recipes if r.meal != adaptation.meal] + [adaptation]
        return [line for line in confirmed_lines(job, draft) if line.meal == adaptation.meal]


def _line_problems(job: ListJob, draft: ListDraft, lines: list[str]) -> list[str]:
    known = {line.id for line in job.lines}
    ready = {line.id for line in confirmed_lines(job, draft)}
    covered = dict(_covered(draft))
    problems = [f"There is no line {line}." for line in lines if line not in known]
    problems += [f"Line {line} is already in {covered[line]}." for line in lines if line in covered]
    problems += [
        f"Confirm the recipe before covering line {line}."
        for line in lines
        if line in known and line not in ready
    ]
    if len(set(lines)) < len(lines):
        problems.append("A line is given twice.")
    return problems


def _amount_problem(job: ListJob, item: ListItemInput) -> str | None:
    """An item without a quantity may cover only lines whose recipes give no amount."""
    if item.quantity is not None:
        return None
    measured = [line.id for line in job.lines if line.id in item.lines and line.has_amount]
    if not measured:
        return None
    verb = "gives" if len(measured) == 1 else "give"
    return f'"{item.name}" needs a quantity: {", ".join(measured)} {verb} an amount.'


def _rounded(item: ListItemInput) -> ListItemInput:
    """Counts are rounded up to whole ones (you buy whole lemons); amounts in a
    unit keep two decimals."""
    if item.quantity is None:
        if item.unit is not None:
            raise ListRefused("A unit needs a quantity.")
        return item
    if item.unit is None:
        # Rounded first so float noise (2.0000000001) isn't a whole one more.
        quantity = float(math.ceil(round(item.quantity, 6)))
    else:
        quantity = round(item.quantity, 2)
        if quantity == 0:
            raise ListRefused(f"{item.quantity} {item.unit} rounds to 0: use a smaller unit.")
    return ListItemInput.model_validate(item.model_dump() | {"quantity": quantity})


def add_item(job_dir: Path, item: ListItemInput) -> ListItemInput:
    """Add an item to the job's list, as saved (its quantity rounded), or raise
    ListRefused with the reason."""
    try:
        item = _rounded(
            ListItemInput.model_validate(item.model_dump() | {"name": item.name.strip()})
        )
    except ValidationError as error:
        raise ListRefused(f"Invalid item:\n{error}") from error
    if (staple := STAPLE_NAMES.get(key(item.name))) is not None:
        raise ListRefused(
            f'"{item.name}" is the staple {staple.value}: add it as a staple with '
            "`add_staple` instead."
        )
    job = read_context(job_dir, ListJob)
    with _changing(job_dir) as draft:
        if any(key(other.name) == key(item.name) for other in draft.items):
            raise ListRefused(
                f'"{item.name}" is already on the list. To change it, `remove` it and add it again.'
            )
        problems = _line_problems(job, draft, item.lines)
        if (problem := _amount_problem(job, item)) is not None:
            problems.append(problem)
        if problems:
            raise ListRefused(" ".join(problems))
        draft.items.append(item)
    return item


def add_staple(job_dir: Path, staple: StapleInput) -> None:
    """Record a staple the meals use, or raise ListRefused with the reason."""
    job = read_context(job_dir, ListJob)
    with _changing(job_dir) as draft:
        if any(other.staple == staple.staple for other in draft.staples):
            raise ListRefused(
                f"{staple.staple.value} is already added. To change its lines, `remove` it "
                "and add it again."
            )
        if problems := _line_problems(job, draft, staple.lines):
            raise ListRefused(" ".join(problems))
        draft.staples.append(staple)


def remove(job_dir: Path, name: str) -> None:
    """Take an item or staple off the job's list, or raise ListRefused."""
    with _changing(job_dir) as draft:
        items = [item for item in draft.items if key(item.name) != key(name)]
        staples = [staple for staple in draft.staples if staple.staple.value != key(name)]
        if len(items) + len(staples) == len(draft.items) + len(draft.staples):
            raise ListRefused(f'"{name}" is not on the list.')
        draft.items, draft.staples = items, staples


def list_problems(job: ListJob, draft: ListDraft) -> list[str]:
    """What keeps the list from being done: every line covered exactly once, by
    known lines; a quantity wherever the lines give an amount; and something to
    buy. Empty when it's done."""
    try:
        ready = confirmed_lines(job, draft)
    except (ListRefused, ValidationError) as error:
        return [str(error)]
    ready_ids = {line.id for line in ready}
    problems = [
        f"Meal {recipe.number} needs adapt_recipe confirmation."
        for recipe in job.recipes
        if not any(line.meal == recipe.number for line in ready)
    ]
    seen: dict[str, str] = {}
    for line, by in _covered(draft):
        if line in seen:
            problems.append(f"Line {line} is in both {seen[line]} and {by}.")
        seen.setdefault(line, by)
    known = {line.id for line in job.lines}
    problems += [
        f"Confirm the recipe before covering line {line}."
        for line in seen
        if line in known and line not in ready_ids
    ]
    problems += [f"There is no line {line}." for line in seen if line not in known]
    if missing := [line for line in job.lines if line.id not in seen]:
        problems.append(
            "Not covered yet: "
            + "; ".join(
                f"{line.id} ({next((r.text for r in ready if r.id == line.id), line.text)})"
                for line in missing
            )
            + "."
        )
    problems += [
        problem for item in draft.items if (problem := _amount_problem(job, item)) is not None
    ]
    if not draft.items:
        problems.append("The list has nothing to buy.")
    return problems
