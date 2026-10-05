"""The meals the agent adds (the `add_meal` tool), kept in the job directory
for the worker."""

from collections import Counter
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from castle_chef_agent.jobfiles import locked, read_context, replace_file
from castle_chef_agent.names import key


class IngredientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="The ingredient only, as it reads after the quantity, e.g. 'garlic cloves', "
        "'Italian sausage', 'bell peppers'. Never include the quantity or unit.",
    )
    quantity: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Amount in `unit`, or the item count when `unit` is null.",
    )
    unit: str | None = Field(
        default=None,
        description="Measurement unit such as 'lb', 'oz', 'cup', 'tbsp', 'tsp'. Null for items "
        "counted whole (eggs, garlic cloves, tortillas, bell peppers); never repeat the name here.",
    )
    preparation: str | None = Field(default=None, description="e.g. 'minced', 'sliced'.")


class StepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The step, as the cook reads it.")
    timer_minutes: int | None = Field(
        default=None,
        ge=1,
        description="When the step has the cook wait for a set time (roasting, baking, "
        "simmering, resting), those minutes, for a timer. Null when there's nothing to time.",
    )
    ingredients: list[str] = Field(
        default_factory=list,
        description="The ingredients this step uses, each named exactly as in the meal's "
        "`ingredients`. Empty when it uses none (e.g. 'Heat the oven to 425°F.').",
    )


class MealInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None
    servings: int = Field(ge=1, description="How many people the quantities feed.")
    active_time_minutes: int | None = Field(default=None, ge=0)
    total_time_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[IngredientInput] = Field(min_length=1)
    steps: list[StepInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _steps_use_its_ingredients(self) -> Self:
        listed = Counter(key(ingredient.name) for ingredient in self.ingredients)
        named = [name for step in self.steps for name in step.ingredients]
        if unknown := [name for name in named if not listed[key(name)]]:
            raise ValueError(f"Steps name ingredients the meal doesn't list: {_quoted(unknown)}.")
        if twice := sorted({name for name in named if listed[key(name)] > 1}):
            raise ValueError(
                f"Steps name ingredients the meal lists more than once: {_quoted(twice)}. "
                "Name each entry so a step can say which it uses, e.g. 'milk, for the sauce'."
            )
        return self

    def step_ingredients(self, step: StepInput) -> list[int]:
        """The positions in `ingredients` of the ones `step` uses (each listed once)."""
        names = {key(name) for name in step.ingredients}
        return [
            position
            for position, ingredient in enumerate(self.ingredients)
            if key(ingredient.name) in names
        ]


def _quoted(names: list[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


class MealsJob(BaseModel):
    """What a generate_meals job's tools check against (the worker writes it)."""

    count: int


class MealRefused(Exception):
    """`add_meal` refused a meal; the message says why, for the agent."""


def meals_dir(job_dir: Path) -> Path:
    """Where `add_meal` saves the agent's meals for the worker."""
    return job_dir / "meals"


def save_meal(job_dir: Path, meal_input: MealInput) -> Path:
    """Save a validated meal as the next numbered JSON file in the job directory,
    or raise MealRefused: a meal named like one already added, or one more than
    the job asks for. The agent may call several `add_meal`s at once."""
    count = read_context(job_dir, MealsJob).count
    with locked(job_dir):
        saved = load_meals(job_dir)
        if any(key(meal.name) == key(meal_input.name) for meal in saved):
            raise MealRefused(f'"{meal_input.name}" is already added.')
        if len(saved) >= count:
            raise MealRefused(f"All {count} meals are already added.")
        directory = meals_dir(job_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{len(saved) + 1:03d}.json"
        replace_file(path, meal_input.model_dump_json(indent=2))
        return path


def load_meals(job_dir: Path) -> list[MealInput]:
    return [
        MealInput.model_validate_json(path.read_text())
        for path in sorted(meals_dir(job_dir).glob("*.json"))
    ]


def meal_problems(job_dir: Path, count: int) -> list[str]:
    """What keeps a batch of `count` from being done (all its meals added); empty
    when it's done."""
    added = len(load_meals(job_dir))
    return [] if added == count else [f"{added} of {count} meals are added."]
