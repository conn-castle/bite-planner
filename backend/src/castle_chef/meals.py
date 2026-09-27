"""Saving the agent's meals (castle_chef_agent.meals has the contract it writes against)."""

from uuid import UUID

from castle_chef_agent.meals import MealInput
from sqlmodel import Session

from castle_chef.models import Meal, MealIngredient


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
