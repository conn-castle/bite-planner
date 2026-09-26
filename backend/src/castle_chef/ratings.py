"""Meal ratings: each household's rating of its meals, "don't show meals like
this" (not for us) included. This module reads and writes the ratings table
only; orders.py owns rating a meal (a not-for-us mark also unpicks it from the
draft, under the draft's lock) and asks here whether a meal can be picked."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, func, select

from castle_chef.models import Meal, MealPreference, MealRating


def write(session: Session, household_id: UUID, meal_id: UUID, rating: MealRating) -> None:
    """Stage the household's rating of a meal, replacing any earlier one; the caller commits."""
    upsert = insert(MealPreference).values(
        household_id=household_id, meal_id=meal_id, rating=rating
    )
    session.execute(
        upsert.on_conflict_do_update(
            index_elements=[col(MealPreference.household_id), col(MealPreference.meal_id)],
            set_={"rating": upsert.excluded.rating, "updated_at": func.now()},
        )
    )


def clear(session: Session, household_id: UUID, meal_id: UUID) -> None:
    """Stage a meal back to not rated (nothing to do if it isn't rated); the caller commits."""
    session.execute(
        delete(MealPreference).where(
            col(MealPreference.household_id) == household_id,
            col(MealPreference.meal_id) == meal_id,
        )
    )


def of_meals(
    session: Session, household_id: UUID, meal_ids: Collection[UUID]
) -> dict[UUID, MealRating]:
    """The household's ratings of these meals, by meal id; unrated meals are absent."""
    if not meal_ids:
        return {}
    return {
        preference.meal_id: preference.rating
        for preference in session.exec(
            select(MealPreference).where(
                MealPreference.household_id == household_id,
                col(MealPreference.meal_id).in_(meal_ids),
            )
        ).all()
    }


def is_not_for_us(session: Session, household_id: UUID, meal_id: UUID) -> bool:
    """Whether the household marked the meal "don't show meals like this"."""
    return of_meals(session, household_id, [meal_id]).get(meal_id) == MealRating.NOT_FOR_US


def not_for_us_meals(session: Session, household_id: UUID) -> list[Meal]:
    """The meals the household marked "don't show meals like this", by name, one per
    name (the same idea marked in two batches is listed once)."""
    meals = session.exec(
        select(Meal)
        .join(MealPreference, col(MealPreference.meal_id) == Meal.id)
        .where(
            MealPreference.household_id == household_id,
            MealPreference.rating == MealRating.NOT_FOR_US,
        )
        .order_by(col(Meal.name), col(Meal.id))
    ).all()
    by_name: dict[str, Meal] = {}
    for meal in meals:
        by_name.setdefault(meal.name.casefold(), meal)
    return list(by_name.values())
