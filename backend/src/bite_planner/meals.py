"""Saving the agent's meals (bite_planner_agent.meals has the contract it writes against)."""

from calendar import monthrange
from datetime import UTC, datetime
from uuid import UUID

from bite_planner_agent.meals import RECIPE_SERVINGS, MealInput, MealsJob, MealTitle
from sqlmodel import Session, col, select

from bite_planner.models import (
    GroceryOrder,
    Job,
    Meal,
    MealIngredient,
    MealPreference,
    MealRating,
    MealStep,
)


def add_meals(session: Session, job_id: UUID, meal_inputs: list[MealInput]) -> None:
    """Stage meals linked to their job; the caller commits."""
    household_id = session.exec(
        select(GroceryOrder.household_id)
        .join(Job, col(Job.grocery_order_id) == GroceryOrder.id)
        .where(Job.id == job_id)
    ).one()
    for meal_input in meal_inputs:
        meal = Meal(
            job_id=job_id,
            household_id=household_id,
            servings=RECIPE_SERVINGS,
            **meal_input.model_dump(exclude={"ingredients", "steps"}),
            steps=[
                MealStep(
                    text=step.text,
                    timer_minutes=step.timer_minutes,
                    ingredients=meal_input.step_ingredients(step),
                )
                for step in meal_input.steps
            ],
            ingredients=[
                MealIngredient(position=position, **ingredient.model_dump())
                for position, ingredient in enumerate(meal_input.ingredients)
            ],
        )
        session.add(meal)


def novelty_snapshot(session: Session, job: Job, count: int, now: datetime) -> MealsJob:
    """Household/current rating snapshot; calendar-month window includes both ends."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Meal novelty snapshot requires a timezone-aware timestamp")
    now = now.astimezone(UTC)
    year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    cutoff = now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))
    household_id = session.exec(
        select(GroceryOrder.household_id).where(GroceryOrder.id == job.grocery_order_id)
    ).one()
    # One query sees a consistent set of canonical names and current ratings.
    rows = session.exec(
        select(Meal.name, Meal.created_at, Job.grocery_order_id, col(MealPreference.rating))
        .select_from(Meal)
        .outerjoin(Job, col(Meal.job_id) == Job.id)
        .outerjoin(
            MealPreference,
            (col(MealPreference.meal_id) == Meal.id)
            & (col(MealPreference.household_id) == household_id),
        )
        .where(Meal.household_id == household_id)
        .where(
            (col(Job.grocery_order_id) == job.grocery_order_id)
            | ((col(Meal.created_at) >= cutoff) & (col(Meal.created_at) <= now))
            | col(MealPreference.rating).in_([MealRating.UP, MealRating.FAVORITE])
        )
        .order_by(col(Meal.created_at), col(Meal.id))
    ).all()
    snapshot = MealsJob(count=count, snapshot_at=now)
    for name, created_at, order_id, rating in rows:
        title = MealTitle(name=name, created_at=created_at)
        if order_id == job.grocery_order_id:
            snapshot.current_draft.append(title)
        elif cutoff <= created_at <= now:
            snapshot.recent.append(title)
        if rating in (MealRating.UP, MealRating.FAVORITE):
            snapshot.positive.append(title)
    return snapshot
