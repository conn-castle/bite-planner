"""One bounded recipe proposal; the agent cannot change the accepted recipe."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bite_planner_agent.jobfiles import locked, read_context, replace_file
from bite_planner_agent.meals import RECIPE_SERVINGS, Color, Dish, Icon, MealInput, StepInput
from bite_planner_agent.names import key


class CurrentIngredient(BaseModel):
    name: str
    quantity: float | None = None
    unit: str | None = None
    preparation: str | None = None
    icon: Icon | None = None
    color: Color | None = None


class CurrentRecipe(BaseModel):
    """Accepted recipe data, preserving missing legacy facts."""

    name: str
    servings: int = Field(gt=0)
    description: str | None = None
    dish: Dish | None = None
    active_time_minutes: int | None = None
    total_time_minutes: int | None = None
    ingredients: list[CurrentIngredient]
    steps: list[StepInput]


class ProposedRecipe(MealInput):
    """A complete, strictly validated replacement for a two-serving recipe."""

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if (
            not self.name.strip()
            or any(not i.name.strip() for i in self.ingredients)
            or any(not s.text.strip() for s in self.steps)
        ):
            raise ValueError("Recipe names, ingredient names and instructions cannot be blank.")
        if len(self.name) > 100 or len(self.description or "") > 300:
            raise ValueError("Use at most 100 characters for the name and 300 for the description.")
        if len(self.ingredients) > 30 or len(self.steps) > 20:
            raise ValueError("Use at most 30 ingredients and 20 steps.")
        for ingredient in self.ingredients:
            if (
                len(ingredient.name) > 80
                or len(ingredient.unit or "") > 20
                or len(ingredient.preparation or "") > 80
            ):
                raise ValueError("Ingredient name/unit/preparation limits are 80/20/80 characters.")
        if any(len(step.text) > 600 for step in self.steps):
            raise ValueError("Use at most 600 characters per step.")
        return self


class EditMealJob(BaseModel):
    current: CurrentRecipe


class EditOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: ProposedRecipe | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=300)
    completed: bool = False

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.proposal is None) == (self.reason is None):
            raise ValueError("Provide exactly one proposal or refusal reason.")
        return self


class EditRefused(Exception):
    """An unusable edit, with a reason for the member or agent."""


def _text(value: str | None) -> str | None:
    return None if value is None else " ".join(value.split()) or None


def _content(recipe: CurrentRecipe) -> object:
    return (
        recipe.servings,
        _text(recipe.name),
        _text(recipe.description),
        recipe.active_time_minutes,
        recipe.total_time_minutes,
        [
            (
                _text(i.name),
                None if i.quantity is None else round(i.quantity, 2),
                _text(i.unit),
                _text(i.preparation),
            )
            for i in recipe.ingredients
        ],
        [
            (_text(s.text), s.timer_minutes, sorted({key(n) for n in s.ingredients}))
            for s in recipe.steps
        ],
    )


def changed(current: CurrentRecipe, proposal: MealInput) -> bool:
    """Missing-to-present metadata counts; picture tags alone do not."""
    return _content(current) != _content(
        CurrentRecipe.model_validate({**proposal.model_dump(), "servings": RECIPE_SERVINGS})
    )


def load_outcome(job_dir: Path) -> EditOutcome | None:
    path = job_dir / "edit.json"
    return EditOutcome.model_validate_json(path.read_text()) if path.exists() else None


def propose(job_dir: Path, recipe: ProposedRecipe) -> None:
    # Revalidate even when called directly, rather than by MCP's input validator.
    recipe = ProposedRecipe.model_validate(recipe.model_dump())
    current = read_context(job_dir, EditMealJob).current
    if not changed(current, recipe):
        raise EditRefused("The agent didn't change anything. Try a different request.")
    # Resolve named references to exactly the listed spelling at the file boundary.
    for step in recipe.steps:
        step.ingredients = [recipe.ingredients[p].name for p in recipe.step_ingredients(step)]
    _write(job_dir, EditOutcome(proposal=recipe))


def cannot_change(job_dir: Path, reason: str) -> None:
    _write(job_dir, EditOutcome(reason=reason.strip()))


def _write(job_dir: Path, outcome: EditOutcome) -> None:
    with locked(job_dir):
        replace_file(job_dir / "edit.json", outcome.model_dump_json(indent=2))


def done(job_dir: Path) -> None:
    with locked(job_dir):
        outcome = load_outcome(job_dir)
        if outcome is None:
            raise EditRefused(
                "Propose a complete recipe or explain why it cannot be changed first."
            )
        outcome.completed = True
        replace_file(job_dir / "edit.json", outcome.model_dump_json(indent=2))
