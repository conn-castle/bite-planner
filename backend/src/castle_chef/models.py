"""Database tables. Alembic migrations are generated from these models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


class TimestampMixin(SQLModel):
    """Common creation and modification timestamps.

    Server defaults cover rows inserted through the Neon Data API, where the
    Python defaults never run.
    """

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": utc_now},
    )


class MealIngredient(SQLModel, table=True):
    """An ingredient belonging to a meal."""

    __tablename__ = "meal_ingredient"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    meal_id: UUID = Field(
        foreign_key="meal.id",
        ondelete="CASCADE",
        index=True,
    )

    # Maintains the ingredient order presented in the recipe.
    position: int

    name: str
    quantity: float | None = None
    unit: str | None = None
    preparation: str | None = None

    meal: Meal = Relationship(back_populates="ingredients")


class Meal(TimestampMixin, SQLModel, table=True):
    """A complete recipe for a meal."""

    __tablename__ = "meal"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    name: str
    description: str | None = None

    servings: int
    active_time_minutes: int | None = None
    total_time_minutes: int | None = None

    # Steps are just ordered strings. They don't need their own table.
    steps: list[str] = Field(default_factory=list, sa_type=JSONB)

    ingredients: list[MealIngredient] = Relationship(
        back_populates="meal",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "MealIngredient.position"},
    )


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(TimestampMixin, SQLModel, table=True):
    """A unit of agent work, queued by the app and processed by the worker."""

    __tablename__ = "job"
    # The app inserts jobs directly, so the database enforces the params shape.
    __table_args__ = (
        # COALESCE: a missing count makes the expression NULL, which a CHECK accepts.
        CheckConstraint(
            "kind <> 'generate_meals' OR coalesce("
            "jsonb_typeof(params->'count') = 'number' "
            "AND (params->>'count')::numeric BETWEEN 1 AND 14 "
            "AND (params->>'count')::numeric = trunc((params->>'count')::numeric) "
            "AND params - 'count' = '{}'::jsonb, false)",
            name="job_generate_meals_params",
        ),
    )

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    kind: str
    params: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    status: JobStatus = Field(
        default=JobStatus.PENDING,
        index=True,
        # Store the lowercase values (what the app sees), not the member names.
        sa_type=Enum(
            JobStatus,
            name="job_status",
            values_callable=lambda members: [member.value for member in members],
        ),
        sa_column_kwargs={"server_default": JobStatus.PENDING.value},
    )
    # Neon Auth user id of the requester; `auth.uid()` fills it for app inserts.
    requested_by: UUID | None = Field(
        default=None, sa_column_kwargs={"server_default": text("auth.uid()")}
    )
    error: str | None = None
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class JobMeal(SQLModel, table=True):
    """Links a job to the meals its agent created."""

    __tablename__ = "job_meal"

    job_id: UUID = Field(foreign_key="job.id", ondelete="CASCADE", primary_key=True)
    meal_id: UUID = Field(foreign_key="meal.id", ondelete="CASCADE", primary_key=True)


class HouseholdMember(SQLModel, table=True):
    """A Neon Auth user invited to the household. The app is invite-only: RLS
    lets only members read data or queue jobs. Email and name live in Neon Auth."""

    __tablename__ = "household_member"

    user_id: UUID = Field(primary_key=True)
    invited_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )


class ClientLogBatch(SQLModel, table=True):
    """Logs the app sent from its Account page; the worker saves them to disk."""

    __tablename__ = "client_log_batch"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(entries) = 'array' AND jsonb_array_length(entries) <= 2000",
            name="client_log_batch_entries",
        ),
    )

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    # Neon Auth user id of the sender; `auth.uid()` fills it for app inserts.
    user_id: UUID | None = Field(
        default=None, sa_column_kwargs={"server_default": text("auth.uid()")}
    )
    # Platform, OS, app version, etc., as reported by the app.
    device: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    entries: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )
