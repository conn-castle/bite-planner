"""Persist each successful list build's cooking recipe snapshot.

Revision ID: d9a71b0e962f
Revises: c6e4a291b502
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy.dialects import postgresql

revision: str = "d9a71b0e962f"
down_revision: str | Sequence[str] | None = "c6e4a291b502"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class HistoricalPick(BaseModel):
    meal_id: UUID
    servings: int
    revision: int = 0


class HistoricalSource(BaseModel):
    picks: list[HistoricalPick]


class HistoricalStep(BaseModel):
    text: str = Field(min_length=1, pattern=r"\S")


class HistoricalRecipe(BaseModel):
    meal_id: UUID
    base_revision: int
    servings: int
    quantities: list[float | None]
    steps: list[str]


class HistoricalPrepared(BaseModel):
    recipes: list[HistoricalRecipe]
    legacy_scaled: bool = True


def upgrade() -> None:
    # contract: pre-release; old workers cannot finish new build_list jobs under
    # the required success snapshot constraint. Rollback must restore_database.
    op.add_column("job", sa.Column("prepared_recipes", postgresql.JSONB(), nullable=True))
    # Historical builds used Python round(q * target / base, 2), preserving
    # original step text. Calculate once with that exact formula, not SQL round's
    # different tie behavior. This provenance never claims culinary adaptation.
    connection = op.get_bind()
    builds = connection.execute(
        sa.text(
            "SELECT id, list_source FROM job WHERE kind::text = 'build_list' "
            "AND status::text = 'succeeded'"
        )
    ).all()
    for job_id, source in builds:
        recipes: list[HistoricalRecipe] = []
        for pick in HistoricalSource.model_validate(source).picks:
            meal = connection.execute(
                sa.text("SELECT servings, revision, steps FROM meal WHERE id = :id"),
                {"id": pick.meal_id},
            ).one()
            quantities = (
                connection.execute(
                    sa.text(
                        "SELECT quantity FROM meal_ingredient WHERE meal_id = :id ORDER BY position"
                    ),
                    {"id": pick.meal_id},
                )
                .scalars()
                .all()
            )
            try:
                steps: list[HistoricalStep] = TypeAdapter(list[HistoricalStep]).validate_python(
                    meal.steps
                )
            except ValidationError as error:
                raise ValueError(
                    f"Cannot backfill prepared recipes for build {job_id}, meal {pick.meal_id}: "
                    "cooking steps must contain non-whitespace text."
                ) from error
            recipes.append(
                HistoricalRecipe(
                    meal_id=pick.meal_id,
                    base_revision=pick.revision,
                    servings=pick.servings,
                    quantities=[
                        None if q is None else round(q * (pick.servings / meal.servings), 2)
                        for q in TypeAdapter(list[float | None]).validate_python(quantities)
                    ],
                    steps=[step.text for step in steps],
                )
            )
        connection.execute(
            sa.text("UPDATE job SET prepared_recipes = CAST(:value AS jsonb) WHERE id = :id"),
            {"id": job_id, "value": HistoricalPrepared(recipes=recipes).model_dump_json()},
        )
    op.create_check_constraint(
        "job_prepared_success",
        "job",
        "(kind::text = 'build_list' AND status::text = 'succeeded') = "
        "(prepared_recipes IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("job_prepared_success", "job", type_="check")
    op.drop_column("job", "prepared_recipes")
