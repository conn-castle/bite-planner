"""The shopping list the agent makes (the shopping-list tools): items to buy
and the staples the meals use, each covering scaled ingredient lines, kept in the
job directory for the worker. Every change is checked as it's made."""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from castle_chef_agent.jobfiles import locked, read_context, replace_file
from castle_chef_agent.names import key


class Aisle(StrEnum):
    """Where a shopping list item is in the store (the mockup's aisles, in order)."""

    PRODUCE = "produce"
    MEAT_SEAFOOD = "meat_seafood"
    DAIRY_EGGS = "dairy_eggs"
    FROZEN = "frozen"
    PANTRY = "pantry"
    OTHER = "other"  # not in the mockup: for what fits no other aisle


class Staple(StrEnum):
    """Assumed to be in every kitchen: never on a list (app-wide, not editable).
    The agent records the ones the meals use, shown only on request."""

    SALT = "salt"
    PEPPER = "pepper"
    OLIVE_OIL = "olive oil"
    VEGETABLE_OIL = "vegetable oil"
    WATER = "water"


# Names that are a staple by another name: refused as items to buy.
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


class ListDraft(BaseModel):
    """The list the agent has made so far (its tools keep it in the job directory)."""

    items: list[ListItemInput] = []
    staples: list[StapleInput] = []


class IngredientLine(BaseModel):
    """One scaled ingredient line of a picked meal, as the prompt shows it."""

    id: str  # e.g. "2c": the meal's number and the line's letter
    meal: int  # the meal's number, from 1
    text: str
    has_amount: bool  # its recipe gives an amount (not "to taste")


class ListJob(BaseModel):
    """What a build_list job's tools check against (the worker writes it)."""

    lines: list[IngredientLine]


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


def _line_problems(job: ListJob, draft: ListDraft, lines: list[str]) -> list[str]:
    known = {line.id for line in job.lines}
    covered = dict(_covered(draft))
    problems = [f"There is no line {line}." for line in lines if line not in known]
    problems += [f"Line {line} is already in {covered[line]}." for line in lines if line in covered]
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
    problems = []
    seen: dict[str, str] = {}
    for line, by in _covered(draft):
        if line in seen:
            problems.append(f"Line {line} is in both {seen[line]} and {by}.")
        seen.setdefault(line, by)
    known = {line.id for line in job.lines}
    problems += [f"There is no line {line}." for line in seen if line not in known]
    if missing := [line for line in job.lines if line.id not in seen]:
        problems.append(
            "Not covered yet: " + "; ".join(f"{line.id} ({line.text})" for line in missing) + "."
        )
    problems += [
        problem for item in draft.items if (problem := _amount_problem(job, item)) is not None
    ]
    if not draft.items:
        problems.append("The list has nothing to buy.")
    return problems
