"""The meal contract the agent writes against, and its persistence."""

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from castle_chef.models import Meal, MealIngredient


class IngredientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="The ingredient only, as it reads after the quantity, e.g. 'garlic cloves', "
        "'Italian sausage', 'bell peppers'. Never include the quantity or unit.",
    )
    quantity: float | None = Field(
        default=None, gt=0, description="Amount in `unit`, or the item count when `unit` is null."
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


def meals_dir(job_dir: Path) -> Path:
    """Where `castle-chef meal add` saves the agent's meals for the worker."""
    return job_dir / "meals"


def save_meal(job_dir: Path, meal_input: MealInput) -> Path:
    """Save a validated meal as the next numbered JSON file in the job directory.

    The agent may run several `meal add` commands in parallel, so each claims
    its file number with an exclusive create.
    """
    directory = meals_dir(job_dir)
    directory.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = directory / f"{number:03d}.json"
        try:
            with path.open("x") as file:
                file.write(meal_input.model_dump_json(indent=2))
            return path
        except FileExistsError:
            number += 1


def load_meals(job_dir: Path) -> list[MealInput]:
    return [
        MealInput.model_validate_json(path.read_text())
        for path in sorted(meals_dir(job_dir).glob("*.json"))
    ]


def add_meals(session: Session, job_id: UUID, meal_inputs: list[MealInput]) -> None:
    """Stage meals linked to their job; the caller commits."""
    for meal_input in meal_inputs:
        meal = Meal(
            job_id=job_id,
            **meal_input.model_dump(exclude={"ingredients"}),
            ingredients=[
                MealIngredient(position=position, **ingredient.model_dump())
                for position, ingredient in enumerate(meal_input.ingredients)
            ],
        )
        session.add(meal)
