"""The meals the agent adds (`castle-chef meal add`), kept in the job directory
for the worker."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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


class MealInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None
    servings: int = Field(ge=1, description="How many people the quantities feed.")
    active_time_minutes: int | None = Field(default=None, ge=0)
    total_time_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[IngredientInput] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)


class MealsJob(BaseModel):
    """What a generate_meals job's commands check against (the worker writes it)."""

    count: int


class MealRefused(Exception):
    """`meal add` refused a meal; the message says why, for the agent."""


def meals_dir(job_dir: Path) -> Path:
    """Where `castle-chef meal add` saves the agent's meals for the worker."""
    return job_dir / "meals"


def save_meal(job_dir: Path, meal_input: MealInput) -> Path:
    """Save a validated meal as the next numbered JSON file in the job directory,
    or raise MealRefused: a meal named like one already added, or one more than
    the job asks for. The agent may run several `meal add` commands at once."""
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
