"""Database tables. Alembic migrations are generated from these models."""

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Annotated, NamedTuple
from uuid import UUID, uuid4

from castle_chef_agent.address import Address
from castle_chef_agent.meal_edit import ProposedRecipe
from castle_chef_agent.meals import Color, Dish, Icon
from castle_chef_agent.products import ProductCount
from castle_chef_agent.shopping import Aisle, PreparedRecipes
from pydantic import BaseModel, StringConstraints
from pydantic import Field as ModelField
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Dialect,
    Enum,
    ForeignKeyConstraint,
    Index,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Relationship, SQLModel

from castle_chef.checkout import Booking, BookIssue, CartLedger, Quote, QuoteRequest


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
TIP_PERCENT = Range(0, 30)  # of the items' total
TIP_MAX_CENTS = Range(0, 10_000)


def _between(column: str, allowed: Range) -> str:
    return f"{column} BETWEEN {allowed.low} AND {allowed.high}"


def _values(enum: type[StrEnum]) -> list[str]:
    """Store an enum's lowercase values (what the app sees), not its member names."""
    return [member.value for member in enum]


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
    __table_args__ = (
        CheckConstraint("(icon IS NULL) = (color IS NULL)", name="meal_ingredient_icon_color"),
    )

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
    # How the meal's picture shows it; both null when it isn't shown, and for meals
    # from before pictures.
    icon: Icon | None = Field(
        default=None, sa_type=Enum(Icon, name="ingredient_icon", values_callable=_values)
    )
    color: Color | None = Field(
        default=None, sa_type=Enum(Color, name="ingredient_color", values_callable=_values)
    )

    meal: Meal = Relationship(back_populates="ingredients")


class ModelJSONB[M: BaseModel](TypeDecorator[M]):
    """A JSONB column holding one typed model: validated as it's read."""

    impl = JSONB
    cache_ok = True

    def __init__(self, model: type[M]) -> None:
        # None is SQL NULL, not a JSON null.
        super().__init__(none_as_null=True)
        self.model = model

    def process_bind_param(self, value: M | None, dialect: Dialect) -> object:
        return None if value is None else value.model_dump(mode="json")

    def process_result_value(self, value: object, dialect: Dialect) -> M | None:
        return None if value is None else self.model.model_validate(value)


class ModelJSONBList[M: BaseModel](TypeDecorator[list[M]]):
    """A JSONB column holding a list of one typed model: validated as it's read."""

    impl = JSONB
    cache_ok = True

    def __init__(self, model: type[M]) -> None:
        super().__init__()
        self.model = model

    def process_bind_param(self, value: list[M] | None, dialect: Dialect) -> object:
        return [item.model_dump(mode="json") for item in value or []]

    def process_result_value(self, value: object, dialect: Dialect) -> list[M]:
        if not isinstance(value, list):
            raise ValueError(f"Expected a JSON list, got {type(value).__name__}.")
        return [self.model.model_validate(item) for item in value]


class MealStep(BaseModel):
    """One step of a recipe."""

    text: str
    # How long the cook waits in it, for a timer; null when there's nothing to time.
    timer_minutes: int | None = None
    # The positions of the meal's ingredients it uses.
    ingredients: list[int] = []


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
    revision: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    active_time_minutes: int | None = None
    total_time_minutes: int | None = None

    # What its picture shows it served in; null for meals from before pictures.
    dish: Dish | None = Field(
        default=None, sa_type=Enum(Dish, name="dish", values_callable=_values)
    )

    # In order. They don't need their own table.
    steps: list[MealStep] = Field(default_factory=list, sa_type=ModelJSONBList(MealStep))

    ingredients: list[MealIngredient] = Relationship(
        back_populates="meal",
        cascade_delete=True,
        # Loaded with the meal: every meal the app sees is drawn from them.
        sa_relationship_kwargs={"order_by": "MealIngredient.position", "lazy": "selectin"},
    )


class ListPick(BaseModel):
    """A pick as a shopping list was built from it."""

    meal_id: UUID
    servings: int
    revision: int = 0


class ListSource(BaseModel):
    """The picks (and servings) a shopping list was built from, sorted by meal id,
    so two builds from the same picks have equal sources."""

    picks: list[ListPick]


class JobKind(StrEnum):
    GENERATE_MEALS = "generate_meals"  # a batch of meal ideas
    BUILD_LIST = "build_list"  # the draft's shopping list
    SHOP = "shop"  # the products to buy for a list, at the Instacart store
    QUOTE = "quote"  # what the list's products cost at checkout, for the final review
    BOOK = "book"  # booking an approved order at the store
    EDIT_MEAL = "edit_meal"  # a proposed change, awaiting an explicit decision


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EditDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MealEditRequest(BaseModel):
    feedback: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    base_revision: int = ModelField(ge=0)
    replaces: UUID | None = None


class Job(TimestampMixin, SQLModel, table=True):
    """One run of the agent, for an order, processed by the worker. The app picks
    the id, so a retried request can't start a second run."""

    __tablename__ = "job"
    __table_args__ = (
        ForeignKeyConstraint(
            ["meal_id"], ["meal.id"], name="job_meal_id_fkey", ondelete="CASCADE", use_alter=True
        ),
        CheckConstraint(
            "(kind::text = 'build_list') = (list_source IS NOT NULL)",
            name="job_list_source_for_lists",
        ),
        CheckConstraint(
            "(kind::text = 'build_list' AND status::text = 'succeeded') = "
            "(prepared_recipes IS NOT NULL)",
            name="job_prepared_success",
        ),
        CheckConstraint(
            "(kind::text = 'shop') = (build_id IS NOT NULL)", name="job_build_for_shops"
        ),
        CheckConstraint(
            "(kind::text = 'quote') = (quote_request IS NOT NULL)",
            name="job_quote_request_for_quotes",
        ),
        CheckConstraint("kind::text = 'quote' OR quote IS NULL", name="job_quote_only_quotes"),
        CheckConstraint("(kind::text = 'edit_meal') = (meal_id IS NOT NULL)", name="job_edit_meal"),
        CheckConstraint(
            "(kind::text = 'edit_meal') = (meal_edit IS NOT NULL)", name="job_edit_request"
        ),
        CheckConstraint("kind::text = 'edit_meal' OR proposal IS NULL", name="job_edit_proposal"),
        CheckConstraint(
            ("kind::text <> 'edit_meal' OR (status::text = 'succeeded') = (proposal IS NOT NULL)"),
            name="job_edit_success",
        ),
        CheckConstraint("decision IS NULL OR kind::text = 'edit_meal'", name="job_edit_decision"),
        CheckConstraint(
            "decision::text <> 'accepted' OR status::text = 'succeeded'", name="job_edit_accept"
        ),
        CheckConstraint(
            "decision::text <> 'rejected' OR status::text IN ('succeeded', 'failed')",
            name="job_edit_reject",
        ),
        CheckConstraint(
            (
                "(decision IS NOT NULL) = (decided_by IS NOT NULL) AND "
                "(decision IS NOT NULL) = (decided_at IS NOT NULL)"
            ),
            name="job_edit_decided",
        ),
        Index(
            "job_one_undecided_edit",
            "meal_id",
            unique=True,
            postgresql_where=text("meal_id IS NOT NULL AND decision IS NULL"),
        ),
    )

    id: UUID = Field(primary_key=True)
    grocery_order_id: UUID = Field(foreign_key="grocery_order.id", ondelete="CASCADE", index=True)
    # The destination when shopping started; changed settings invalidate its products.
    delivery_address: Address | None = Field(default=None, sa_type=ModelJSONB(Address))
    kind: JobKind = Field(sa_type=Enum(JobKind, name="job_kind", values_callable=_values))
    status: JobStatus = Field(
        default=JobStatus.PENDING,
        index=True,
        sa_type=Enum(JobStatus, name="job_status", values_callable=_values),
        sa_column_kwargs={"server_default": JobStatus.PENDING.value},
    )
    # The Neon Auth user who asked for it.
    requested_by: UUID
    # A build_list job's picks, as they were when it started; null for other kinds.
    list_source: ListSource | None = Field(default=None, sa_type=ModelJSONB(ListSource))
    prepared_recipes: PreparedRecipes | None = Field(
        default=None, sa_type=ModelJSONB(PreparedRecipes)
    )
    # A shop job's list: the build_list job whose items it chooses products for.
    build_id: UUID | None = Field(
        default=None, foreign_key="job.id", ondelete="CASCADE", index=True
    )
    # A quote job's products, counts, and tip rule, as they were when it started;
    # and, once it succeeds, what checkout showed. Null for other kinds.
    quote_request: QuoteRequest | None = Field(default=None, sa_type=ModelJSONB(QuoteRequest))
    quote: Quote | None = Field(default=None, sa_type=ModelJSONB(Quote))
    meal_id: UUID | None = Field(default=None, index=True)
    meal_edit: MealEditRequest | None = Field(default=None, sa_type=ModelJSONB(MealEditRequest))
    proposal: ProposedRecipe | None = Field(default=None, sa_type=ModelJSONB(ProposedRecipe))
    decision: EditDecision | None = Field(
        default=None, sa_type=Enum(EditDecision, name="edit_decision", values_callable=_values)
    )
    decided_by: UUID | None = None
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    error: str | None = None
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class GroceryOrderStatus(StrEnum):
    DRAFT = "draft"
    # Approved: ordered, as far as the rest of the app goes (reminders stop, and
    # the next order starts from it), whether it's booked at the store yet or not.
    PLACED = "placed"


class BookState(StrEnum):
    """Where a placed (approved) order is with the store (spec/requirements.md
    "Shopping list", stage 3)."""

    WAITING = "waiting"  # until the day it books (four days before delivery)
    BOOKING = "booking"  # its book job is under way
    BOOKED = "booked"
    NEEDS_TIME = "needs_time"  # no window left that day by the time: choose another
    NEEDS_PRODUCT = "needs_product"  # a product is gone: choose another
    NOT_BOOKED = "not_booked"  # failed before the click; nothing was ordered
    CHECK_INSTACART = "check_instacart"  # clicked, but not confirmed: someone checks


class OrderStep(StrEnum):
    """The create-order flow's steps, in the flow's order: where its lock's holder
    is, and the furthest an order's flow has gone."""

    PICK = "pick"  # picking meals, or waiting for ideas
    SERVINGS = "servings"
    LIST = "list"  # the shopping list: waiting for it, then reviewing it
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
        CheckConstraint(
            "(status = 'placed') = (book_state IS NOT NULL)", name="grocery_order_book_state_placed"
        ),
        CheckConstraint(
            "(book_state = 'booking') = (book_job_id IS NOT NULL)",
            name="grocery_order_book_job_while_booking",
        ),
        CheckConstraint(
            "status = 'draft' OR quote_id IS NOT NULL OR book_state = 'booked'",
            name="grocery_order_approved_quote",
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
    # The latest time it should arrive by on the delivery date, in the store's
    # time zone ("by 6:00 PM"); null until the member chooses one (the household's
    # delivery time is shown until then).
    delivery_by: time | None = None
    next_delivery_date: date | None = None
    # The furthest step any opening of its flow has reached: where the flow opens
    # again (the member can go back from there).
    reached_step: OrderStep = Field(
        default=OrderStep.PICK,
        sa_type=Enum(OrderStep, name="order_step", values_callable=_values),
        sa_column_kwargs={"server_default": OrderStep.PICK.value},
    )
    # When it was placed, and the Neon Auth user who placed it (anyone in the
    # household can finish a draft). Both null while it's a draft.
    placed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    placed_by: UUID | None = None
    # Once placed: where it is with the store; null for a draft.
    book_state: BookState | None = Field(
        default=None, sa_type=Enum(BookState, name="book_state", values_callable=_values)
    )
    # The quote approved with it (the estimated total the member saw), whose
    # address and card booking checks it still has. Null for a draft, and for
    # orders placed before booking existed.
    quote_id: UUID | None = Field(default=None, foreign_key="job.id", ondelete="SET NULL")
    # The book job under way (book_state 'booking').
    book_job_id: UUID | None = Field(default=None, foreign_key="job.id", ondelete="SET NULL")
    # When booking pressed Place order (recorded just before): it's never pressed
    # again for this order, unless someone says it wasn't placed after all.
    clicked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # What Instacart confirmed, once booked; null before (and for orders placed
    # before booking existed).
    booking: Booking | None = Field(default=None, sa_type=ModelJSONB(Booking))
    # Why it isn't booked (needs a time, a product, not booked, check Instacart).
    book_issue: BookIssue | None = Field(default=None, sa_type=ModelJSONB(BookIssue))
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


class ShoppingListItem(TimestampMixin, SQLModel, table=True):
    """An item of a draft's shopping list, as a build_list job made it: the
    order's list is its newest build's items."""

    __tablename__ = "shopping_list_item"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="shopping_list_item_quantity_positive"),
        CheckConstraint(
            "changed_quantity IS NULL OR (changed_quantity > 0 AND quantity IS NOT NULL)",
            name="shopping_list_item_changed_quantity_positive",
        ),
        CheckConstraint("NOT (on_hand AND removed)", name="shopping_list_item_on_hand_kept"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="job.id", ondelete="CASCADE", index=True)
    # The agent's order.
    position: int
    name: str
    # How much the recipes need, as the agent worked it out, in `unit` (a count
    # when null); null when the recipes give no amount.
    quantity: float | None = None
    # How much the member chose instead; null: the agent's amount is ordered.
    changed_quantity: float | None = None
    unit: str | None = None
    aisle: Aisle = Field(sa_type=Enum(Aisle, name="aisle", values_callable=_values))
    # The picked meals it's for.
    meal_ids: list[UUID] = Field(sa_type=ARRAY(Uuid))
    # Assumed on hand (salt, pepper, ...): never ordered, shown only on request.
    on_hand: bool = False
    # The member took it off the list (or chose not to buy its product).
    removed: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    # What to buy for it at the store (a shop job's choices, then the member's
    # changes): the product and how many; null before the list is shopped, for
    # staples, and for an item the store doesn't have until the member picks one
    # of its alternatives.
    product: ProductCount | None = Field(default=None, sa_type=ModelJSONB(ProductCount))
    # What the member can choose instead.
    more_alternatives_query: str | None = None
    # Matching products from the one saved broader query, fetched during shopping.
    more_alternatives: list[ProductCount] = Field(
        default_factory=list,
        sa_type=ModelJSONBList(ProductCount),
        sa_column_kwargs={"server_default": text("'[]'::jsonb")},
    )
    alternatives: list[ProductCount] = Field(
        default_factory=list,
        sa_type=ModelJSONBList(ProductCount),
        sa_column_kwargs={"server_default": text("'[]'::jsonb")},
    )
    # Why the agent gave no alternatives.
    no_alternatives_reason: str | None = None
    # The store doesn't have it (the member picks an alternative or skips it), and
    # why the meals work without it, if they do.
    not_found: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    skippable_reason: str | None = None


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


class ProductGone(StrEnum):
    """What booking does when an approved product is no longer available."""

    USE_ALTERNATIVE = "use_alternative"  # the first of its alternatives available
    ASK = "ask"  # nothing is ordered until whoever approved it chooses


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
        CheckConstraint(_between("tip_percent", TIP_PERCENT), name="household_tip_percent_range"),
        CheckConstraint(
            _between("tip_max_cents", TIP_MAX_CENTS), name="household_tip_max_cents_range"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True)
    delivery_address: Address | None = Field(default=None, sa_type=ModelJSONB(Address))
    # Days before an order's target date that the household decides by.
    decide_by_offset_days: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    # Days before the decide-by date that reminders start; 0 is the decide-by day.
    reminder_lead_days: int = Field(default=2, sa_column_kwargs={"server_default": "2"})
    # The servings each picked meal starts at.
    default_servings: int = Field(default=4, sa_column_kwargs={"server_default": "4"})
    # The latest time deliveries should arrive by (each order's starts at it), in
    # the store's time zone.
    delivery_time: time = Field(default=time(18, 0), sa_column_kwargs={"server_default": "18:00"})
    # The tip: this percentage of the items' total, but never more than the maximum.
    tip_percent: int = Field(default=10, sa_column_kwargs={"server_default": "10"})
    tip_max_cents: int = Field(default=1500, sa_column_kwargs={"server_default": "1500"})
    # When an approved product is gone at booking: use its first available
    # alternative, or ask whoever approved the order.
    product_gone: ProductGone = Field(
        default=ProductGone.USE_ALTERNATIVE,
        sa_type=Enum(ProductGone, name="product_gone", values_callable=_values),
        sa_column_kwargs={"server_default": ProductGone.USE_ALTERNATIVE.value},
    )
    # The household's own items a run took out of its Instacart cart (and the
    # products it put in), until they're put back (castle_chef.cart); null when
    # the cart is the household's own.
    cart_ledger: CartLedger | None = Field(default=None, sa_type=ModelJSONB(CartLedger))


class HouseholdInvite(TimestampMixin, SQLModel, table=True):
    """Someone invited to a household, by email, until they first sign in with it
    (they join then). Only an invited email can create an account (the sign-in
    provider asks the API first: castle_chef.household)."""

    __tablename__ = "household_invite"
    __table_args__ = (
        # One invitation per person, whatever the case of their email.
        Index("household_invite_email", text("lower(email)"), unique=True),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    household_id: UUID = Field(foreign_key="household.id", ondelete="CASCADE", index=True)
    email: str
    # What to call them (their account's name, once they sign in).
    name: str
    # The member who invited them; null for the admin command.
    invited_by: UUID | None = None


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
