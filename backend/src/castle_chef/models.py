"""Database tables. Alembic migrations are generated from these models."""

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


class Range(NamedTuple):
    low: int
    high: int


# Ranges checked by the database and the API alike. The earliest delivery is
# tomorrow, so the decision comes at least a day before.
DECIDE_BY_OFFSET_DAYS = Range(1, 7)
REMINDER_LEAD_DAYS = Range(0, 7)  # 0: on the decide-by day
SERVINGS = Range(1, 12)  # a pick's, and the household's default for new picks


def _between(column: str, allowed: Range) -> str:
    return f"{column} BETWEEN {allowed.low} AND {allowed.high}"


class TimestampMixin(SQLModel):
    """Common creation and modification timestamps.

    Server defaults cover rows inserted outside Python (plain SQL), where the
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

    # The run that created it: the suggestion batch it belongs to. Null once that
    # job is deleted with its discarded draft; the meal itself is kept.
    job_id: UUID | None = Field(default=None, foreign_key="job.id", ondelete="SET NULL", index=True)

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


def _values(enum: type[StrEnum]) -> list[str]:
    """Store an enum's lowercase values (what the app sees), not its member names."""
    return [member.value for member in enum]


class JobKind(StrEnum):
    GENERATE_MEALS = "generate_meals"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(TimestampMixin, SQLModel, table=True):
    """One run of the agent, for an order, processed by the worker. The app picks
    the id, so a retried request can't start a second run."""

    __tablename__ = "job"

    id: UUID = Field(primary_key=True)
    grocery_order_id: UUID = Field(foreign_key="grocery_order.id", ondelete="CASCADE", index=True)
    kind: JobKind = Field(sa_type=Enum(JobKind, name="job_kind", values_callable=_values))
    status: JobStatus = Field(
        default=JobStatus.PENDING,
        index=True,
        sa_type=Enum(JobStatus, name="job_status", values_callable=_values),
        sa_column_kwargs={"server_default": JobStatus.PENDING.value},
    )
    # The Neon Auth user who asked for it.
    requested_by: UUID
    error: str | None = None
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class GroceryOrderStatus(StrEnum):
    DRAFT = "draft"
    PLACED = "placed"


class OrderStep(StrEnum):
    """The create-order flow's steps, as its lock reports where the holder is."""

    PICK = "pick"  # picking meals, or waiting for ideas
    SERVINGS = "servings"
    DELIVERY = "delivery"  # the delivery date
    NEXT = "next"  # the next delivery date
    REVIEW = "review"  # the final review, which places it


class GroceryOrder(TimestampMixin, SQLModel, table=True):
    """A household's order: its draft while it's being made, then the placed order."""

    __tablename__ = "grocery_order"
    __table_args__ = (
        Index(
            "grocery_order_one_draft",
            "household_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        CheckConstraint(
            "status <> 'placed' OR (delivery_date IS NOT NULL AND next_delivery_date IS NOT NULL)",
            name="grocery_order_placed_has_dates",
        ),
        CheckConstraint(
            "(status = 'placed') = (placed_at IS NOT NULL AND placed_by IS NOT NULL)",
            name="grocery_order_placed_when_and_by",
        ),
        CheckConstraint(
            "next_delivery_date > delivery_date", name="grocery_order_next_after_delivery"
        ),
        CheckConstraint(
            "(locked_by IS NULL) = (lock_session IS NULL) AND (locked_by IS NULL) = "
            "(locked_at IS NULL) AND (locked_by IS NULL) = (lock_renewed_at IS NULL) AND "
            "(locked_by IS NULL) = (lock_step IS NULL)",
            name="grocery_order_lock_complete",
        ),
        CheckConstraint(
            "status = 'draft' OR locked_by IS NULL", name="grocery_order_lock_only_draft"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    household_id: UUID = Field(foreign_key="household.id", index=True)
    # The Neon Auth user who started it.
    created_by: UUID
    status: GroceryOrderStatus = Field(
        default=GroceryOrderStatus.DRAFT,
        sa_type=Enum(GroceryOrderStatus, name="grocery_order_status", values_callable=_values),
    )
    delivery_date: date | None = None
    next_delivery_date: date | None = None
    # When it was placed, and the Neon Auth user who placed it (anyone in the
    # household can finish a draft). Both null while it's a draft.
    placed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    placed_by: UUID | None = None
    # The order lock: only one ordering session (one opening of the flow, on one
    # phone) changes the draft at a time. Who holds it (a Neon Auth user), that
    # session (chosen by the app), when they took it, their last renewal (it
    # expires 2 minutes after), and the step they're at. All null when no one
    # has it; an expired lock stays until someone takes, renews, or releases it.
    locked_by: UUID | None = None
    lock_session: UUID | None = None
    locked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    lock_renewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    lock_step: OrderStep | None = Field(
        default=None, sa_type=Enum(OrderStep, name="order_step", values_callable=_values)
    )


class GroceryOrderMeal(SQLModel, table=True):
    """A meal picked for an order: a pick."""

    __tablename__ = "grocery_order_meal"
    __table_args__ = (
        CheckConstraint(_between("servings", SERVINGS), name="grocery_order_meal_servings_range"),
    )

    grocery_order_id: UUID = Field(
        foreign_key="grocery_order.id", ondelete="CASCADE", primary_key=True
    )
    meal_id: UUID = Field(foreign_key="meal.id", primary_key=True)
    # How many to make of it in this order (the meal's recipe is for meal.servings).
    # Starts at the household's default servings when picked.
    servings: int


class MealRating(StrEnum):
    # Declared worst to best: Postgres orders enum values as declared, so
    # "liked or better" is `rating >= 'up'`.
    NOT_FOR_US = "not_for_us"  # "don't show meals like this" (the ✕ while picking)
    DOWN = "down"
    UP = "up"
    FAVORITE = "favorite"  # the heart; a favorite is always liked


class MealPreference(TimestampMixin, SQLModel, table=True):
    """The household's rating of a meal. No row means not rated."""

    __tablename__ = "meal_preference"

    # Stored rather than found through the meal's job and order: discarding a
    # draft deletes its jobs (nulling meal.job_id), but its "not for us" marks
    # stay with the household.
    household_id: UUID = Field(foreign_key="household.id", primary_key=True)
    meal_id: UUID = Field(foreign_key="meal.id", primary_key=True)
    rating: MealRating = Field(
        sa_type=Enum(MealRating, name="meal_rating", values_callable=_values)
    )


class Household(TimestampMixin, SQLModel, table=True):
    """A group of people who share orders and meals. Each household sees only its own."""

    __tablename__ = "household"
    __table_args__ = (
        CheckConstraint(
            _between("decide_by_offset_days", DECIDE_BY_OFFSET_DAYS),
            name="household_decide_by_offset_range",
        ),
        CheckConstraint(
            _between("reminder_lead_days", REMINDER_LEAD_DAYS), name="household_reminder_lead_range"
        ),
        CheckConstraint(
            _between("default_servings", SERVINGS), name="household_default_servings_range"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True)
    # Days before an order's target date that the household decides by.
    decide_by_offset_days: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    # Days before the decide-by date that reminders start; 0 is the decide-by day.
    reminder_lead_days: int = Field(default=2, sa_column_kwargs={"server_default": "2"})
    # The servings each picked meal starts at.
    default_servings: int = Field(default=4, sa_column_kwargs={"server_default": "4"})


class AppUser(SQLModel, table=True):
    """The app's side of a user: a Neon Auth account (same id) invited to a
    household, and their own settings. The app is invite-only: the API serves only
    users in a household. Email and name live in Neon Auth (neon_auth.user) only."""

    __tablename__ = "app_user"

    # One household per user.
    user_id: UUID = Field(primary_key=True)
    household_id: UUID = Field(foreign_key="household.id", index=True)
    invited_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )
    # When their daily reminder goes out: a clock time in `time_zone`, not UTC,
    # so it stays put through daylight saving changes and follows the phone.
    notification_time: time = Field(
        default=time(8, 0), sa_column_kwargs={"server_default": "08:00"}
    )
    # The phone's IANA zone, which the app reports; None until it first does.
    time_zone: str | None = None
    # Whether they get the daily reminder to pick meals (a switch in Settings);
    # "your meal ideas are ready" always goes out.
    reminders_on: bool = Field(default=True, sa_column_kwargs={"server_default": "true"})
    # The day (in `time_zone`) of the last reminder sent: at most one a day.
    reminded_on: date | None = None


class PushDevice(TimestampMixin, SQLModel, table=True):
    """A phone that gets a user's notifications. The token is Apple's push address
    for the app on that phone, so a phone someone else signs in on moves to them.
    updated_at is the last time the app reported it."""

    __tablename__ = "push_device"

    token: str = Field(primary_key=True)
    user_id: UUID = Field(foreign_key="app_user.user_id", ondelete="CASCADE", index=True)
