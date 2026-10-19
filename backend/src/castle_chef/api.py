"""The Castle Chef API: what the app calls. Runs on AWS Lambda (through the Lambda
Web Adapter) in production, and with `uvicorn castle_chef.api:app` locally."""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import lru_cache
from time import monotonic
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from castle_chef_agent import windows
from castle_chef_agent.address import Address
from castle_chef_agent.checkout import Checkout
from castle_chef_agent.meal_edit import ProposedRecipe
from castle_chef_agent.meals import Color, Dish, Icon
from castle_chef_agent.names import key
from castle_chef_agent.products import ProductCount
from castle_chef_agent.shopping import STAPLE_NAMES, Aisle
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from castle_chef import (
    booking,
    delivery,
    failures,
    meal_edits,
    orders,
    push,
    quotes,
    ratings,
    reminders,
    shopping,
)
from castle_chef import household as households
from castle_chef import log as logging_config
from castle_chef.auth import (
    AuthError,
    AuthUnavailable,
    AuthVerifier,
    NeonAuthVerifier,
    NeonWebhookVerifier,
    Principal,
    WebhookRejected,
    WebhookVerifier,
)
from castle_chef.checkout import SIGNED_OUT_ERROR, Booking, BookIssue
from castle_chef.db import get_request_session
from castle_chef.dispatch import DispatchError, JobDispatcher, get_dispatcher
from castle_chef.jobs import (
    STALE_ERROR,
    BatchRunning,
    JobIdTaken,
    ListNotReady,
    NothingToList,
    fail_job,
    is_stale,
    shop_of,
    submit_build_list,
    submit_generate_meals,
    submit_shop,
)
from castle_chef.models import (
    DECIDE_BY_OFFSET_DAYS,
    REMINDER_LEAD_DAYS,
    SERVINGS,
    TIP_MAX_CENTS,
    TIP_PERCENT,
    AppUser,
    BookState,
    EditDecision,
    GroceryOrder,
    GroceryOrderStatus,
    Household,
    Job,
    JobKind,
    JobStatus,
    Meal,
    MealRating,
    OrderStep,
    ProductGone,
    ShoppingListItem,
)
from castle_chef.settings import current_release, get_settings, load_ssm_parameters

log = logging.getLogger(__name__)
client_log = logging.getLogger("castle_chef.client")

NOT_IN_A_HOUSEHOLD = "This account isn't in a household."
MAX_LOG_ENTRIES = 2000
# The app's log levels, as Python's. The app logs `error` only for defects
# (crashes, uncaught exceptions), so those alarm like the backend's own errors.
CLIENT_LOG_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING}
MAX_LOG_BYTES = 1_000_000


# Response and request bodies.


class MemberOut(BaseModel):
    user_id: UUID
    name: str | None


class PicturedIngredientOut(BaseModel):
    icon: Icon
    color: Color


class MealPictureOut(BaseModel):
    """What the app draws a meal's picture from."""

    dish: Dish
    # Its pictured ingredients, in recipe order.
    ingredients: list[PicturedIngredientOut]


class MealSummaryOut(BaseModel):
    id: UUID
    revision: int
    name: str
    description: str | None
    active_time_minutes: int | None
    total_time_minutes: int | None
    # Distinct recipe ingredient names, excluding the app-wide household staples.
    ingredient_count: int
    # The household's rating; null when not rated.
    rating: MealRating | None
    # Null for meals from before pictures.
    picture: MealPictureOut | None


class JobOut(BaseModel):
    """One of an order's jobs: a batch of suggestions, its shopping list's build or
    shopping, a quote of its total, or its booking."""

    id: UUID
    kind: JobKind
    meal_id: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    status: JobStatus
    error: str | None
    requested_by: MemberOut
    created_at: datetime
    # Empty until it succeeds; in a fixed order.
    meals: list[MealSummaryOut]


class PickOut(BaseModel):
    """A meal picked for an order, and how many to make of it."""

    meal_id: UUID
    servings: int


class PlacedMealOut(MealSummaryOut):
    """A meal of a placed order, and how many were ordered of it."""

    prepared_build_id: UUID | None = None
    servings: int


class ProductOut(BaseModel):
    """A product at the store, and how many of it: packages, bags, or pieces."""

    product_id: str
    name: str
    # As the store labels it: "2 lb", "13.5 fl oz", or "per lb" for weighed items.
    size: str | None
    # One's price in cents (estimated for weighed items); null if the store shows none.
    price_cents: int | None
    # Weighed items: the price per pound, and a package's estimated weight.
    price_per_lb_cents: int | None
    estimated_lb: float | None
    image_url: str | None
    bought_before: bool
    count: int


def product_out(chosen: ProductCount) -> ProductOut:
    return ProductOut(**chosen.product.model_dump(), count=chosen.count)


class ShoppingItemOut(BaseModel):
    """An item of an order's shopping list."""

    id: UUID
    name: str
    # What's ordered, in `unit` (a count when null): the member's amount, or the
    # one worked out for the recipes; null when the recipes give no amount.
    quantity: float | None
    # The amount worked out for the recipes, which the member can go back to.
    recipe_quantity: float | None
    unit: str | None
    aisle: Aisle
    # The picked meals it's for.
    meal_ids: list[UUID]
    # Assumed on hand (salt, pepper, ...): never ordered; the app shows them only on request.
    on_hand: bool
    # Taken off the list, or its product not to be bought.
    removed: bool
    # What's bought for it at the store, once the list is shopped; null for an
    # item the store doesn't have until the member picks an alternative.
    product: ProductOut | None
    # What the member can switch to (up to 8), or why there are none.
    alternatives: list[ProductOut]
    more_alternatives_query: str | None
    more_alternatives: list[ProductOut]
    no_alternatives_reason: str | None
    # The store doesn't have it; why the meals work without it, if they do.
    not_found: bool
    skippable_reason: str | None


class ShoppingListOut(BaseModel):
    """A draft's shopping list: its newest build, and the newest one's items that succeeded."""

    # The newest build: its status, error, who started it, and when.
    job: JobOut
    # Exact accepted-input match, including active or failed builds.
    source_current: bool
    # Ready for the order's picks and servings now: the newest build succeeded, from them.
    current: bool
    # Shopping the newest succeeded build's items at the store: its newest shop
    # job (null until it starts), and the store.
    shop: JobOut | None
    shop_current: bool
    address_problem: str | None
    store: str
    # The newest succeeded build's items, by aisle; [] until one succeeds.
    items: list[ShoppingItemOut]


class OrderLockOut(BaseModel):
    """Who is placing the order: one ordering session (an opening of the flow on
    one phone) has its lock, until it's closed, placed, or 2 minutes pass
    without a renewal."""

    member: MemberOut
    # The session that has it; shown only to its own member (their phones tell
    # whether it's theirs), null for everyone else.
    session: UUID | None
    # When they took it: "Started 6 min ago".
    since: datetime
    step: OrderStep
    expires_at: datetime


class QuoteOut(BaseModel):
    """What the order would cost at the store, for the final review: its newest
    quote (the cart filled with its products, and checkout read)."""

    job: JobOut
    # For the order's products, counts, and tip rule now (else a new one is due).
    current: bool
    # It failed because Castle Chef is signed out of Instacart's website (someone
    # signs it in again before trying again).
    signed_out: bool
    # Once it succeeds: why Instacart won't check the cart out (below the store's
    # minimum), or what checkout showed.
    blocked_because: str | None
    checkout: Checkout | None
    availability: windows.Availability | None
    availability_current: bool
    availability_valid_until: datetime | None


class ProposedRecipeOut(ProposedRecipe):
    ingredient_count: int


class RecipeChangeOut(BaseModel):
    meal_id: UUID
    job_id: UUID
    state: meal_edits.ChangeState
    picked: bool
    blocks: bool
    feedback: str
    requested_by: MemberOut
    created_at: datetime
    started_at: datetime | None
    error: str | None
    base_revision: int
    proposal: ProposedRecipeOut | None


class EditMealIn(BaseModel):
    job_id: UUID
    feedback: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    replaces: UUID | None = None


class AcceptEditIn(BaseModel):
    base_revision: int = Field(ge=0)


class GroceryOrderOut(BaseModel):
    id: UUID
    number: int
    status: GroceryOrderStatus
    created_by: MemberOut
    created_at: datetime
    # The previous order's next delivery date; none for the first order.
    target_date: date | None
    # The target date less the household's decide-by offset.
    decide_by_date: date | None
    # The decide-by date less the household's reminder lead: daily reminders from then.
    reminders_start_date: date | None
    earliest_delivery_date: date
    delivery_time_zone: str
    delivery_date: date | None
    # The latest time it should arrive by, in the store's time zone; null until chosen.
    delivery_by: time | None
    next_delivery_date: date | None
    # The furthest step its flow has reached: where the flow opens again.
    reached_step: OrderStep
    # Its jobs, oldest first: the suggestions are their meals.
    jobs: list[JobOut]
    recipe_changes: list[RecipeChangeOut]
    recipes_editable: bool
    # In the order their meals were suggested.
    picks: list[PickOut]
    # Its live lock (a draft's), or null when no one is placing it.
    lock: OrderLockOut | None
    # Its shopping list; null until one is started.
    shopping_list: ShoppingListOut | None
    # What it would cost: its newest quote; null until one is started.
    quote: QuoteOut | None
    # Once placed (approved), where it is with the store (else null): the day it
    # books, what Instacart confirmed once booked (null for orders placed before
    # booking existed), and why it isn't booked.
    book_state: BookState | None
    book_on: date | None
    booking: Booking | None
    book_issue: BookIssue | None
    can_confirm_absent: bool


class PlacedOrderOut(BaseModel):
    """A placed order. It can't change, so it has no suggestions, and its dates are set."""

    id: UUID
    number: int
    placed_at: datetime
    placed_by: MemberOut
    delivery_date: date
    # The latest time it should arrive by; null for orders placed before there was one.
    delivery_by: time | None
    next_delivery_date: date
    # The picked meals, in the order they were suggested.
    meals: list[PlacedMealOut]
    # What was ordered for them, by aisle.
    shopping_list: list[ShoppingItemOut]
    # Where it is with the store (GroceryOrderOut's).
    book_state: BookState
    book_on: date
    booking: Booking | None
    book_issue: BookIssue | None
    can_confirm_absent: bool


class NextOrderOut(BaseModel):
    """The household's next order, whether or not anyone has started its draft."""

    # "Order 4": the number it has, or will have once started.
    number: int
    # The last placed order's next delivery date; none before the first order.
    target_date: date | None
    decide_by_date: date | None
    # The first day of the daily reminders to decide it; none without a target date.
    reminders_start_date: date | None
    earliest_delivery_date: date
    delivery_time_zone: str
    # Its draft, once someone has started one.
    draft: GroceryOrderOut | None


class IngredientOut(BaseModel):
    name: str
    quantity: float | None
    unit: str | None
    preparation: str | None


class StepOut(BaseModel):
    text: str
    timer_minutes: int | None
    # Positions in the meal's `ingredients`.
    ingredients: list[int]


class PreparedRecipeOut(BaseModel):
    build_id: UUID
    base_revision: int
    servings: int
    legacy_scaled: bool
    ingredients: list[IngredientOut]
    steps: list[StepOut]


def prepared_recipe_out(session: Session, meal: Meal) -> PreparedRecipeOut | None:
    batch = session.get(Job, meal.job_id) if meal.job_id is not None else None
    if batch is None:
        return None
    order = session.get_one(GroceryOrder, batch.grocery_order_id)
    build = (
        shopping.current_build(session, order.id)
        if order.status == GroceryOrderStatus.DRAFT
        else shopping.newest_built(session, order.id)
    )
    if build is None:
        return None
    for original, prepared in shopping.prepared_meals(session, build):
        if original.meal.id != meal.id:
            continue
        assert build.prepared_recipes is not None
        return PreparedRecipeOut(
            build_id=build.id,
            base_revision=prepared.base_revision,
            servings=prepared.servings,
            legacy_scaled=build.prepared_recipes.legacy_scaled,
            ingredients=[
                IngredientOut.model_validate(i, from_attributes=True).model_copy(
                    update={"quantity": quantity}
                )
                for i, quantity in zip(meal.ingredients, prepared.quantities, strict=True)
            ],
            steps=[
                StepOut.model_validate(step.model_dump()).model_copy(update={"text": text})
                for step, text in zip(meal.steps, prepared.steps, strict=True)
            ],
        )
    return None


class MealOut(MealSummaryOut):
    prepared: PreparedRecipeOut | None = None
    servings: int
    steps: list[StepOut]
    ingredients: list[IngredientOut]


class HouseholdRefOut(BaseModel):
    id: UUID
    name: str


class HouseholdMemberOut(BaseModel):
    user_id: UUID
    name: str | None
    # For when there's no name.
    email: str | None


class InviteOut(BaseModel):
    """Someone invited, who joins when they first sign in with this email."""

    id: UUID
    name: str
    email: str
    # The member who invited them; null for the admin command.
    invited_by: MemberOut | None
    created_at: datetime


class HouseholdOut(BaseModel):
    """The household's settings, shared by everyone in it, and who's in it."""

    id: UUID
    name: str
    decide_by_offset_days: int
    reminder_lead_days: int
    default_servings: int
    # The latest time deliveries should arrive by, in the store's time zone.
    delivery_time: time
    # The tip: a percentage of the items' total, never more than the maximum.
    tip_percent: int
    tip_max_cents: int
    # When an approved product is gone at booking.
    product_gone: ProductGone
    delivery_address: Address | None
    # In the order they were invited.
    members: list[HouseholdMemberOut]
    # Invitations not used yet, oldest first.
    invites: list[InviteOut]


class AddressSearchIn(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]


class AddressSuggestionsOut(BaseModel):
    addresses: list[Address]


class PersonalSettingsOut(BaseModel):
    # A clock time in `time_zone`.
    notification_time: time
    # The phone's IANA zone; none until the app first reports it.
    time_zone: str | None
    # Whether they get the daily reminder to pick meals ("ideas ready" always goes out).
    reminders_on: bool


class MeOut(BaseModel):
    user_id: UUID
    email: str | None
    name: str | None
    # Whether the account is in a household (the API serves only those).
    member: bool
    household: HouseholdRefOut | None
    # None when not in a household.
    settings: PersonalSettingsOut | None


def clock_minute(value: time | None) -> time | None:
    """A clock time is hours and minutes, with no time zone."""
    if value is not None and (value.second or value.microsecond or value.tzinfo):
        raise ValueError("A time is hours and minutes, with no time zone.")
    return value


def zone(name: str) -> ZoneInfo:
    """The IANA time zone with this name; ValueError if there's none."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown time zone {name!r}.") from error


class PartialUpdate(BaseModel):
    """A PATCH body: only the fields sent are changed. At least one must be sent,
    and none can be null (every setting has a value)."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def some_values_sent(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Send at least one setting to change.")
        nulls = sorted(name for name in self.model_fields_set if getattr(self, name) is None)
        if nulls:
            raise ValueError(f"{', '.join(nulls)} can't be null.")
        return self


# Trimmed; the name the household's members see.
HouseholdName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
]


class HouseholdSettingsIn(PartialUpdate):
    """Named as the household's columns: the fields sent are copied to them."""

    name: HouseholdName | None = None
    decide_by_offset_days: int | None = Field(
        default=None, ge=DECIDE_BY_OFFSET_DAYS.low, le=DECIDE_BY_OFFSET_DAYS.high
    )
    reminder_lead_days: int | None = Field(
        default=None, ge=REMINDER_LEAD_DAYS.low, le=REMINDER_LEAD_DAYS.high
    )
    default_servings: int | None = Field(default=None, ge=SERVINGS.low, le=SERVINGS.high)
    delivery_time: time | None = None
    tip_percent: int | None = Field(default=None, ge=TIP_PERCENT.low, le=TIP_PERCENT.high)
    tip_max_cents: int | None = Field(default=None, ge=TIP_MAX_CENTS.low, le=TIP_MAX_CENTS.high)
    product_gone: ProductGone | None = None
    delivery_address: Address | None = None

    _minute = field_validator("delivery_time")(clock_minute)


class PersonalSettingsIn(PartialUpdate):
    """Named as the member's columns: the fields sent are copied to them."""

    notification_time: time | None = None
    time_zone: str | None = None
    reminders_on: bool | None = None

    _minute = field_validator("notification_time")(clock_minute)

    @field_validator("time_zone")
    @classmethod
    def known_zone(cls, value: str | None) -> str | None:
        if value is not None:
            zone(value)
        return value


# Apple's push address for the app on a phone: hex, 32 bytes today, possibly longer.
DeviceToken = Annotated[str, Path(pattern="^[0-9a-fA-F]{64,200}$")]


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Chosen by the app, so a retried request can't start a second batch.
    job_id: UUID


class DatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_date: date | None
    # The latest time it should arrive by, in the store's time zone.
    delivery_by: time | None
    next_delivery_date: date | None

    _minute = field_validator("delivery_by")(clock_minute)


class QuoteIn(JobIn):
    refresh: bool = False


class PlaceIn(BaseModel):
    """The dates and time the member saw when placing, saved with it, and the
    quote (the estimated total) they saw, which must be the order's current one."""

    model_config = ConfigDict(extra="forbid")

    delivery_date: date
    delivery_by: time
    next_delivery_date: date
    quote_id: UUID

    _minute = field_validator("delivery_by")(clock_minute)


class SettleIn(BaseModel):
    """Whether Instacart has an order booking couldn't confirm."""

    model_config = ConfigDict(extra="forbid")

    placed: bool


class DeliveryProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID


class ApproveDeliveryIn(DeliveryProposalIn):
    next_delivery_date: date


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str


class NeonAuthUser(BaseModel):
    email: str | None = None


class NeonAuthEvent(BaseModel):
    """A Neon Auth webhook's envelope (https://neon.com/docs/auth/guides/webhooks):
    what's used of it."""

    event_type: str
    user: NeonAuthUser = NeonAuthUser()


class SignUpAnswer(BaseModel):
    """The answer to `user.before_create`: whether the account may be made."""

    allowed: bool
    error_message: str | None = None


class AuthDiagnosticIn(BaseModel):
    """A bounded sign-in event from a phone that does not have a session yet."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    event: Literal[
        "code_sent", "code_send_failed", "code_verified", "verify_failed", "session_failed"
    ]
    status: int | None = Field(default=None, ge=100, le=599)
    code: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=200)


class LockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The step the flow shows, for the other members.
    step: OrderStep


class ItemIn(PartialUpdate):
    """A change to a shopping list item: taken off (or put back; for a product,
    not bought), how much to buy, how many of its product, or switched to one of
    its alternatives (its product id)."""

    removed: bool | None = None
    quantity: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, ge=1)
    alternative: str | None = None


class ServingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servings: int = Field(ge=SERVINGS.low, le=SERVINGS.high)


class RatingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: MealRating


class LogsIn(BaseModel):
    device: dict[str, Any]
    entries: list[dict[str, Any]] = Field(max_length=MAX_LOG_ENTRIES)

    @field_validator("entries")
    @classmethod
    def not_too_large(cls, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(json.dumps(entries)) > MAX_LOG_BYTES:
            raise ValueError(f"Log batches are limited to {MAX_LOG_BYTES} bytes.")
        return entries


# Dependencies.


@lru_cache
def get_webhook_verifier() -> WebhookVerifier:
    settings = get_settings()
    if not settings.neon_auth_url:
        raise RuntimeError("Set CASTLE_CHEF_NEON_AUTH_URL.")
    return NeonWebhookVerifier(settings.neon_auth_url)


@lru_cache
def get_verifier() -> AuthVerifier:
    settings = get_settings()
    if not settings.neon_auth_url:
        raise RuntimeError("Set CASTLE_CHEF_NEON_AUTH_URL.")
    return NeonAuthVerifier(
        settings.neon_auth_url, settings.neon_auth_issuer, settings.neon_auth_audience
    )


def db() -> Iterator[Session]:
    with get_request_session() as session:
        yield session


# A request's session closes before its answer is sent (scope "function"): on
# Lambda, the environment can freeze as soon as the answer is out, and a session
# closed after that is left mid-transaction until it thaws or shuts down.
DbSession = Annotated[Session, Depends(db, scope="function")]


def today(time_zone: Annotated[str | None, Header(alias="Time-Zone")] = None) -> date:
    """Today in the phone's time zone, which the app sends with every request."""
    if not time_zone:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Send the phone's time zone in the Time-Zone header."
        )
    try:
        phone_zone = zone(time_zone)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return datetime.now(phone_zone).date()


def names(session: Session, user_ids: set[UUID]) -> dict[UUID, str | None]:
    """Neon Auth display names, by user id."""
    if not user_ids:
        return {}  # an empty IN list doesn't compare with uuid in Postgres
    rows = session.execute(
        text('SELECT id, name FROM neon_auth."user" WHERE id IN :ids').bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": list(user_ids)},
    ).all()
    # An account named nothing (made by signing in with a code) has no name.
    found: dict[UUID, str | None] = {row.id: row.name or None for row in rows}
    return {user_id: found.get(user_id) for user_id in user_ids}


def meal_summary(meal: Meal, rated: dict[UUID, MealRating]) -> MealSummaryOut:
    """A meal as the app lists it, with its rating from `rated` (the household's
    ratings, by meal id)."""
    return MealSummaryOut(
        id=meal.id,
        revision=meal.revision,
        name=meal.name,
        description=meal.description,
        active_time_minutes=meal.active_time_minutes,
        total_time_minutes=meal.total_time_minutes,
        ingredient_count=len(
            {key(ingredient.name) for ingredient in meal.ingredients} - STAPLE_NAMES.keys()
        ),
        rating=rated.get(meal.id),
        picture=meal_picture(meal),
    )


def meal_picture(meal: Meal) -> MealPictureOut | None:
    """What the app draws the meal's picture from; None for a meal from before
    pictures (it has no dish). A meal with a dish pictures at least one ingredient
    (`add_meal` refuses one that doesn't); one that doesn't is an error."""
    if meal.dish is None:
        return None
    ingredients = [
        PicturedIngredientOut(icon=ingredient.icon, color=ingredient.color)
        for ingredient in meal.ingredients
        if ingredient.icon is not None and ingredient.color is not None
    ]
    if not ingredients:
        raise RuntimeError(f"Meal {meal.id} has a dish but no pictured ingredients.")
    return MealPictureOut(dish=meal.dish, ingredients=ingredients)


def job_out(
    job: Job,
    meals: list[Meal],
    people: dict[UUID, str | None],
    rated: dict[UUID, MealRating],
) -> JobOut:
    """A job as the app sees it. An overdue job is shown as failed, the state the
    next request (or the failure handler) will write, so the app stops waiting on
    it without this read writing anything."""
    stale = is_stale(job, datetime.now(UTC))
    return JobOut(
        id=job.id,
        kind=job.kind,
        meal_id=job.meal_id,
        started_at=job.started_at,
        finished_at=job.finished_at,
        status=JobStatus.FAILED if stale else job.status,
        error=STALE_ERROR if stale else job.error,
        requested_by=MemberOut(user_id=job.requested_by, name=people[job.requested_by]),
        created_at=job.created_at,
        meals=[meal_summary(meal, rated) for meal in meals],
    )


def lock_out(
    order: GroceryOrder, viewer: UUID, people: dict[UUID, str | None]
) -> OrderLockOut | None:
    """The order's live lock as `viewer` sees it, or None."""
    locked_by, lock_session, since = order.locked_by, order.lock_session, order.locked_at
    step, expires = order.lock_step, orders.lock_expires_at(order)
    if not orders.has_live_lock(order, datetime.now(UTC)):
        return None
    # Set together; the table's check guarantees it.
    if locked_by is None or lock_session is None or since is None or step is None or not expires:
        raise RuntimeError(f"Order {order.id} has an incomplete lock.")
    return OrderLockOut(
        member=MemberOut(user_id=locked_by, name=people[locked_by]),
        session=lock_session if locked_by == viewer else None,
        since=since,
        step=step,
        expires_at=expires,
    )


def own_lock(session: Session, order: GroceryOrder, user_id: UUID) -> OrderLockOut:
    """The lock the user's session just took or renewed. Gone already: the flow
    closed (releasing it) while the request was on its way, so it isn't open here."""
    lock = lock_out(order, user_id, names(session, {user_id}))
    if lock is None:
        raise orders.OrderLocked("This order isn't open here any more. Open it again.")
    return lock


def item_out(item: ShoppingListItem) -> ShoppingItemOut:
    return ShoppingItemOut(
        id=item.id,
        name=item.name,
        quantity=shopping.ordered_quantity(item),
        recipe_quantity=item.quantity,
        unit=item.unit,
        aisle=item.aisle,
        meal_ids=item.meal_ids,
        on_hand=item.on_hand,
        removed=item.removed,
        product=product_out(item.product) if item.product else None,
        alternatives=[product_out(alternative) for alternative in item.alternatives],
        more_alternatives_query=item.more_alternatives_query,
        more_alternatives=[product_out(product) for product in item.more_alternatives],
        no_alternatives_reason=item.no_alternatives_reason,
        not_found=item.not_found,
        skippable_reason=item.skippable_reason,
    )


def shopping_list_out(
    session: Session,
    order: GroceryOrder,
    people: dict[UUID, str | None],
    rated: dict[UUID, MealRating],
) -> ShoppingListOut | None:
    """The order's shopping list: its newest build and the newest succeeded one's items."""
    newest = shopping.newest_build(session, order.id)
    if newest is None:
        return None
    built = shopping.newest_built(session, order.id)
    shop = shop_of(session, built.id) if built is not None else None
    household = session.get_one(Household, order.household_id)
    address_problem = None
    if order.status == GroceryOrderStatus.DRAFT and delivery.selected(household) is None:
        address_problem = delivery.ADDRESS_REQUIRED
    elif (
        shop is not None
        and shop.delivery_address == household.delivery_address
        and shop.error
        and "Delivery address:" in shop.error
    ):
        address_problem = shop.error[shop.error.index("Delivery address:") :]
    source_matches: bool = shopping.source_current(session, order.id, newest)
    return ShoppingListOut(
        job=job_out(newest, [], people, rated),
        source_current=source_matches,
        current=source_matches and newest.status == JobStatus.SUCCEEDED,
        shop=job_out(shop, [], people, rated) if shop is not None else None,
        shop_current=order.status == GroceryOrderStatus.PLACED
        or (
            shop is not None
            and shop.delivery_address == household.delivery_address
            and delivery.selected(household) is not None
        ),
        address_problem=address_problem,
        store=get_settings().instacart_store,
        items=[]
        if built is None
        else [item_out(item) for item in shopping.items_of(session, built.id)],
    )


def quote_out(
    session: Session,
    order: GroceryOrder,
    people: dict[UUID, str | None],
) -> QuoteOut | None:
    """The order's newest quote, and whether it's for the order as it is now."""
    newest = quotes.newest_quote(session, order.id)
    if newest is None:
        return None
    current = quotes.current(session, order)
    result = newest.quote
    return QuoteOut(
        job=job_out(newest, [], people, {}),
        current=current is not None and current.id == newest.id,
        signed_out=newest.status == JobStatus.FAILED and newest.error == SIGNED_OUT_ERROR,
        blocked_because=result.blocked_because if result else None,
        checkout=result.checkout if result else None,
        availability=result.availability if result else None,
        availability_current=quotes.availability_fresh(result, booking.store_now()),
        availability_valid_until=(
            result.availability.observed_at + windows.AVAILABILITY_MAX_AGE
            if result and result.availability
            else None
        ),
    )


def book_state_out(order: GroceryOrder) -> tuple[BookState | None, BookIssue | None]:
    """Where an order is with the store, and why it isn't booked, as the app is
    told. An order whose booking is late (booking.is_late) is shown as not booked,
    so a member can try again, without this read writing anything: it stays
    waiting, and the next hourly run may still book it."""
    delivery = order.delivery_date
    if delivery is not None and booking.is_late(order, booking.store_now()):
        day = booking.book_on(delivery)
        late = BookIssue(message=f"It didn't book on {day:%a, %b} {day.day} as planned.")
        return BookState.NOT_BOOKED, late
    return order.book_state, order.book_issue


def order_out(session: Session, order: GroceryOrder, today: date, viewer: UUID) -> GroceryOrderOut:
    """An order as `viewer`, a member of its household, sees it."""
    jobs = orders.jobs(session, order.id)
    meals = orders.job_meals(session, [job.id for job in jobs])
    lockers = {order.locked_by} if order.locked_by else set()
    people = names(session, {order.created_by, *(job.requested_by for job in jobs), *lockers})
    rated = ratings.of_meals(
        session, order.household_id, [meal.id for batch in meals.values() for meal in batch]
    )

    target = orders.target_date(session, order)
    decide_by = orders.decide_by_date(session, order.household_id, target)
    book_state, book_issue = book_state_out(order)
    return GroceryOrderOut(
        id=order.id,
        number=orders.number(session, order),
        status=order.status,
        created_by=MemberOut(user_id=order.created_by, name=people[order.created_by]),
        created_at=order.created_at,
        target_date=target,
        decide_by_date=decide_by,
        reminders_start_date=orders.reminders_start_date(session, order.household_id, decide_by),
        earliest_delivery_date=booking.store_now().date(),
        delivery_time_zone=get_settings().store_time_zone,
        delivery_date=order.delivery_date,
        delivery_by=order.delivery_by,
        next_delivery_date=order.next_delivery_date,
        reached_step=order.reached_step,
        jobs=[job_out(job, meals[job.id], people, rated) for job in jobs],
        recipe_changes=[
            RecipeChangeOut(
                **change.model_dump(exclude={"requested_by", "proposal"}),
                proposal=ProposedRecipeOut(
                    **change.proposal.model_dump(),
                    ingredient_count=len(
                        {key(i.name) for i in change.proposal.ingredients} - STAPLE_NAMES.keys()
                    ),
                )
                if change.proposal is not None
                else None,
                requested_by=MemberOut(
                    user_id=change.requested_by, name=people[change.requested_by]
                ),
            )
            for change in meal_edits.changes(session, order)
        ],
        recipes_editable=order.status == GroceryOrderStatus.DRAFT,
        picks=[
            PickOut(meal_id=pick.meal.id, servings=pick.servings)
            for pick in orders.picks(session, order.id)
        ],
        lock=lock_out(order, viewer, people),
        shopping_list=shopping_list_out(session, order, people, rated),
        quote=quote_out(session, order, people),
        book_state=book_state,
        book_on=booking.book_on(order.delivery_date)
        if book_state is not None and order.delivery_date is not None
        else None,
        booking=order.booking,
        book_issue=book_issue,
        can_confirm_absent=booking.can_confirm_absent(session, order),
    )


def placed_order_out(
    session: Session,
    order: GroceryOrder,
    number: int,
    picks: list[orders.Pick],
    items: list[ShoppingListItem],
    people: dict[UUID, str | None],
    rated: dict[UUID, MealRating],
) -> PlacedOrderOut:
    """A placed order, with its picked meals, as the app sees it."""
    placed_at, placed_by = order.placed_at, order.placed_by
    delivery, next_delivery = order.delivery_date, order.next_delivery_date
    book_state, book_issue = book_state_out(order)
    # Set when it's placed; the table's checks guarantee it.
    if (
        placed_at is None
        or placed_by is None
        or delivery is None
        or next_delivery is None
        or book_state is None
    ):
        raise RuntimeError(f"Placed order {order.id} is missing its placement details or dates.")
    return PlacedOrderOut(
        id=order.id,
        number=number,
        placed_at=placed_at,
        placed_by=MemberOut(user_id=placed_by, name=people[placed_by]),
        delivery_date=delivery,
        delivery_by=order.delivery_by,
        next_delivery_date=next_delivery,
        meals=[
            PlacedMealOut(
                **meal_summary(pick.meal, rated).model_dump(),
                servings=pick.servings,
                prepared_build_id=items[0].job_id if items else None,
            )
            for pick in picks
        ],
        shopping_list=[item_out(item) for item in items],
        book_state=book_state,
        book_on=booking.book_on(delivery),
        booking=order.booking,
        book_issue=book_issue,
        can_confirm_absent=booking.can_confirm_absent(session, order),
    )


# Expected failures of order and meal requests, as the status the app gets.
ORDER_ERRORS: dict[type[Exception], int] = {
    meal_edits.RecipeChangesPending: status.HTTP_409_CONFLICT,
    meal_edits.EditOpen: status.HTTP_409_CONFLICT,
    meal_edits.EditDecided: status.HTTP_409_CONFLICT,
    meal_edits.EditNotReady: status.HTTP_409_CONFLICT,
    meal_edits.EditStale: status.HTTP_409_CONFLICT,
    meal_edits.EditNotFound: status.HTTP_404_NOT_FOUND,
    orders.OrderNotFound: status.HTTP_404_NOT_FOUND,
    orders.NotASuggestion: status.HTTP_404_NOT_FOUND,
    orders.MealNotFound: status.HTTP_404_NOT_FOUND,
    orders.NotPicked: status.HTTP_404_NOT_FOUND,
    orders.OrderPlaced: status.HTTP_409_CONFLICT,
    orders.OrderLocked: status.HTTP_423_LOCKED,
    orders.NotReady: status.HTTP_409_CONFLICT,
    orders.ListNotCurrent: status.HTTP_409_CONFLICT,
    orders.ItemNotFound: status.HTTP_404_NOT_FOUND,
    orders.NoAmount: status.HTTP_409_CONFLICT,
    orders.NoSuchProduct: status.HTTP_409_CONFLICT,
    quotes.NothingToQuote: status.HTTP_409_CONFLICT,
    ListNotReady: status.HTTP_409_CONFLICT,
    NothingToList: status.HTTP_409_CONFLICT,
    orders.NotForUs: status.HTTP_409_CONFLICT,
    booking.NotChangeable: status.HTTP_409_CONFLICT,
    households.NotFound: status.HTTP_404_NOT_FOUND,
    households.Refused: status.HTTP_409_CONFLICT,
    households.Invalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    JobIdTaken: status.HTTP_409_CONFLICT,
    orders.InvalidDates: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BatchRunning: status.HTTP_429_TOO_MANY_REQUESTS,
}


def start_job(
    session: Session,
    job: Job,
    created: bool,
    response: Response,
    dispatcher: JobDispatcher,
    not_started: str,
) -> None:
    """Start the worker of a job the request made (one it didn't make, already
    there for a retry or for the same thing, is answered 200 instead of 201). A
    worker that can't be started fails the job: 503, saying `not_started`."""
    if not created:
        response.status_code = status.HTTP_200_OK
        return
    try:
        dispatcher.dispatch(job.id)
    except DispatchError as error:
        log.error("job.dispatch_failed", extra={"job_id": str(job.id), "error": str(error)})
        fail_job(session, job.id, not_started)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, not_started) from error


def dispatch_booking(session: Session, dispatcher: JobDispatcher, job_id: UUID) -> None:
    """Start a book job's worker; if it can't be started, the order isn't booked
    (and says so), but the approval stands."""
    try:
        dispatcher.dispatch(job_id)
    except DispatchError as error:
        log.error("job.dispatch_failed", extra={"job_id": str(job_id), "error": str(error)})
        message = "Booking couldn't be started."
        fail_job(session, job_id, message)
        booking.job_failed(session, job_id, message)


def signed_in(
    request: Request,
    verifier: Annotated[AuthVerifier, Depends(get_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign in first.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        started = monotonic()
        principal = verifier.verify(token)
    except AuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(error), headers={"WWW-Authenticate": "Bearer"}
        ) from error
    except AuthUnavailable as error:
        log.error("auth.unavailable", extra={"error": str(error)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is unavailable; try again."
        ) from error
    finally:
        request.state.auth_ms = round((monotonic() - started) * 1000)
    # Who made the request, for its log line.
    request.state.user_id = str(principal.user_id)
    return principal


@dataclass(frozen=True)
class Member:
    """A signed-in user who is in a household."""

    user_id: UUID
    household_id: UUID


def household_of(session: Session, user_id: UUID) -> Household | None:
    """The user's household, or None if they aren't in one (an app_user row
    without a household doesn't count)."""
    return session.exec(
        select(Household)
        .join(AppUser, col(AppUser.household_id) == Household.id)
        .where(AppUser.user_id == user_id)
    ).first()


def member(
    request: Request,
    principal: Annotated[Principal, Depends(signed_in)],
    session: DbSession,
) -> Member:
    started = monotonic()
    try:
        household = household_of(session, principal.user_id)
    finally:
        request.state.membership_ms = round((monotonic() - started) * 1000)
    if household is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_IN_A_HOUSEHOLD)
    return Member(user_id=principal.user_id, household_id=household.id)


def order_session(
    session_id: Annotated[UUID | None, Header(alias="Order-Session")] = None,
) -> UUID | None:
    """The ordering session a request comes from (one opening of the order flow,
    on one phone), which the app sends with each change to a draft."""
    return session_id


def orderer(
    caller: Annotated[Member, Depends(member)],
    session_id: Annotated[UUID | None, Depends(order_session)],
) -> orders.Orderer:
    """The caller, in the ordering session a change to a draft comes from."""
    if session_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Send the ordering session in the Order-Session header."
        )
    return orders.Orderer(user_id=caller.user_id, session=session_id)


def constraint_name(error: IntegrityError) -> str | None:
    """The database constraint an insert or update broke."""
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def account(session: Session, user_id: UUID) -> tuple[str | None, str | None]:
    row = session.execute(
        text('SELECT email, name FROM neon_auth."user" WHERE id = :id'), {"id": user_id}
    ).first()
    return (row.email, row.name or None) if row else (None, None)


def personal_settings_out(user: AppUser) -> PersonalSettingsOut:
    return PersonalSettingsOut(
        notification_time=user.notification_time,
        time_zone=user.time_zone,
        reminders_on=user.reminders_on,
    )


def household_out(session: Session, household: Household) -> HouseholdOut:
    members = households.members_of(session, household.id)
    invites = households.invites_of(session, household.id)
    people = names(session, {invited.invited_by for invited in invites if invited.invited_by})
    return HouseholdOut(
        id=household.id,
        name=household.name,
        decide_by_offset_days=household.decide_by_offset_days,
        reminder_lead_days=household.reminder_lead_days,
        default_servings=household.default_servings,
        delivery_time=household.delivery_time,
        tip_percent=household.tip_percent,
        tip_max_cents=household.tip_max_cents,
        product_gone=household.product_gone,
        delivery_address=delivery.selected(household),
        members=[
            HouseholdMemberOut(user_id=person.user_id, name=person.name, email=person.email)
            for person in members
        ],
        invites=[
            InviteOut(
                id=invited.id,
                name=invited.name,
                email=invited.email,
                invited_by=MemberOut(
                    user_id=invited.invited_by, name=people.get(invited.invited_by)
                )
                if invited.invited_by
                else None,
                created_at=invited.created_at,
            )
            for invited in invites
        ],
    )


# The app.


def create_app() -> FastAPI:
    logging_config.configure(None)  # JSON lines on stderr; CloudWatch on Lambda
    # Once per cold start of each function (the API, failure handler, and Reminders).
    log.info("api.started", extra=current_release())
    load_ssm_parameters()  # once per cold start, before settings are read
    settings = get_settings()
    api = FastAPI(title="Castle Chef", docs_url=None, redoc_url=None)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Time-Zone", "Order-Session"],
    )

    @api.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = monotonic()

        def fields() -> dict[str, object]:
            return {
                "method": request.method,
                # The route's template, so the line carries no ids.
                "route": getattr(request.scope.get("route"), "path", None),
                "user_id": getattr(request.state, "user_id", None),
                "ms": round((monotonic() - started) * 1000),
                "auth_ms": getattr(request.state, "auth_ms", None),
                "membership_ms": getattr(request.state, "membership_ms", None),
            }

        try:
            response = await call_next(request)
        except ClientDisconnect:
            # The caller hung up before its request was read (e.g. Neon Auth giving
            # up on a slow sign-up check): no defect, and no one to answer.
            log.warning("http.client_gone", extra=fields())
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        except Exception:
            # A defect: the traceback, with what the request was and whose.
            log.exception("http.failed", extra=fields())
            raise
        refused = (
            {"detail": getattr(request.state, "detail", None)}
            if response.status_code >= 400
            else {}
        )
        log.info("http.request", extra=fields() | {"status": response.status_code} | refused)
        return response

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Non-HTTP Lambda events, posted here by the Lambda Web Adapter, on the two
    # functions without a URL; each serves its own.
    if settings.failure_handler and settings.reminders_handler:
        raise RuntimeError(
            "Set CASTLE_CHEF_FAILURE_HANDLER or CASTLE_CHEF_REMINDERS_HANDLER, not both."
        )
    if settings.failure_handler:

        @api.post("/events")
        def failure_events(event: dict[str, Any]) -> dict[str, str]:
            failures.handle_event(event)
            return {"status": "handled"}

    elif settings.reminders_handler:

        @api.post("/events")
        def reminder_events(event: dict[str, Any]) -> dict[str, Any]:
            # The response is the invocation's result (the deploy reads the sync's).
            return reminders.handle_event(event)

    @api.get("/v1/me")
    def me(
        principal: Annotated[Principal, Depends(signed_in)],
        session: DbSession,
    ) -> MeOut:
        # Someone invited joins on their first sign-in (and is named, if unnamed).
        household = households.join_if_invited(session, principal.user_id)
        email, name = account(session, principal.user_id)
        return MeOut(
            user_id=principal.user_id,
            email=email,
            name=name,
            member=household is not None,
            household=HouseholdRefOut(id=household.id, name=household.name) if household else None,
            settings=(
                personal_settings_out(session.get_one(AppUser, principal.user_id))
                if household
                else None
            ),
        )

    @api.patch("/v1/me/settings")
    def update_personal_settings(
        body: PersonalSettingsIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> PersonalSettingsOut:
        """Change the caller's own settings; only the fields sent. A new time or zone
        moves their reminder schedule too, or changes nothing if it can't."""
        user = reminders.lock_user(session, caller.user_id)
        if user is None:  # removed since `member` checked
            raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_IN_A_HOUSEHOLD)
        before = (user.notification_time, user.time_zone)
        user.sqlmodel_update(body.model_dump(exclude_unset=True))
        # Turned off (reminders_on), the schedule stays: each firing checks the switch.
        if (user.notification_time, user.time_zone) != before:
            try:
                reminders.save_schedule(user)
            except reminders.ScheduleError as error:
                session.rollback()
                log.error("settings.schedule_failed", extra={"error": str(error)})
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Couldn't update your reminder time. Try again.",
                ) from error
        session.commit()
        log.info("settings.personal_updated", extra={"fields": sorted(body.model_fields_set)})
        return personal_settings_out(user)

    @api.put("/v1/me/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
    def register_device(
        token: DeviceToken,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """This phone gets the caller's notifications (taken over from whoever
        signed in on it before)."""
        push.register_device(session, caller.user_id, token.lower())

    @api.delete("/v1/me/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
    def unregister_device(
        token: DeviceToken,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """This phone stops getting the caller's notifications (signing out)."""
        push.unregister_device(session, caller.user_id, token.lower())

    @api.get("/v1/household")
    def get_household(
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> HouseholdOut:
        """The household's settings and members."""
        return household_out(session, session.get_one(Household, caller.household_id))

    @api.post("/v1/household/delivery-address/suggestions")
    def address_suggestions(
        body: AddressSearchIn,
        caller: Annotated[Member, Depends(member)],
    ) -> AddressSuggestionsOut:
        try:
            return AddressSuggestionsOut(addresses=delivery.suggestions(body.query))
        except delivery.AddressUnavailable as error:
            log.exception("delivery.search_unavailable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    @api.post("/v1/household/delivery-address/normalize")
    def normalize_address(
        body: Address,
        caller: Annotated[Member, Depends(member)],
    ) -> Address:
        """Review AWS's normalized address before approving it in Settings."""
        try:
            return delivery.validate_address(body)
        except delivery.AddressInvalid as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except delivery.AddressUnavailable as error:
            log.exception("delivery.validation_unavailable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    @api.patch("/v1/household")
    def update_household(
        body: HouseholdSettingsIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> HouseholdOut:
        """Change the household's settings; only the fields sent, so members changing
        different settings at once don't undo each other."""
        household = session.get_one(Household, caller.household_id)
        if body.delivery_address is not None:
            try:
                normalized_address: Address = delivery.validate_address(body.delivery_address)
            except delivery.AddressInvalid as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
            except delivery.AddressUnavailable as error:
                log.exception("delivery.validation_unavailable")
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        changes = body.model_dump(exclude_unset=True, exclude={"delivery_address"})
        household.sqlmodel_update(changes)
        if "delivery_address" in body.model_fields_set:
            household.delivery_address = normalized_address
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            # The admin command finds households by name, so names are unique.
            if constraint_name(error) == "household_name_key":
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Another household is called that."
                ) from error
            raise
        log.info("settings.household_updated", extra={"fields": sorted(body.model_fields_set)})
        return household_out(session, household)

    @api.post("/v1/household/invites", status_code=status.HTTP_201_CREATED)
    def invite_member(
        body: InviteIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> HouseholdOut:
        """Invite someone by email: they join when they first sign in with it (and
        only an invited email can make an account)."""
        household = session.get_one(Household, caller.household_id)
        households.invite(session, household, body.email, body.name, caller.user_id)
        return household_out(session, household)

    @api.delete("/v1/household/invites/{invite_id}")
    def cancel_invite(
        invite_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> HouseholdOut:
        """Withdraw an invitation not used yet."""
        households.cancel_invite(session, caller.household_id, invite_id)
        return household_out(session, session.get_one(Household, caller.household_id))

    @api.delete("/v1/household/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_member(
        user_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """Remove someone from the household, the caller too (leaving); never the last."""
        households.remove_member(session, caller.household_id, user_id)

    # Neon rejects {"allowed": true, "error_message": null} as an invalid
    # blocking-webhook response. Omit the optional field when allowing signup.
    @api.post("/v1/auth/neon-webhook", response_model_exclude_none=True)
    async def neon_auth_webhook(
        request: Request,
        verifier: Annotated[WebhookVerifier, Depends(get_webhook_verifier)],
        session: DbSession,
    ) -> SignUpAnswer | dict[str, str]:
        """Neon Auth's webhook: before it makes an account (`user.before_create`), it
        asks whether the email is invited; an account for anyone else is refused."""
        body = await request.body()
        try:
            verifier.verify(body, request.headers)
        except WebhookRejected as error:
            log.warning("auth.webhook_rejected", extra={"error": str(error)})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not a Neon Auth request.") from error
        except AuthUnavailable as error:
            log.error("auth.unavailable", extra={"error": str(error)})
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        event = NeonAuthEvent.model_validate_json(body)
        if event.event_type != "user.before_create":
            log.warning("auth.webhook_unexpected", extra={"event_type": event.event_type})
            return {"status": "ignored"}
        email = event.user.email or ""
        if households.may_sign_up(session, email):
            log.info("auth.sign_up_allowed")
            return SignUpAnswer(allowed=True)
        log.warning("auth.sign_up_refused")
        return SignUpAnswer(
            allowed=False,
            error_message=(
                "Castle Chef is invite-only: ask someone in your household to invite this email."
            ),
        )

    @api.post("/v1/auth/diagnostics", status_code=status.HTTP_204_NO_CONTENT)
    def auth_diagnostics(body: AuthDiagnosticIn, session: DbSession) -> None:
        """Record sign-in failures before a new member can upload normal app logs.

        Only known emails are logged. The same empty response for unknown emails
        does not reveal whether an address was invited or already has an account.
        """
        email = body.email.strip().lower()
        known = session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM household_invite WHERE lower(email) = :email) "
                'OR EXISTS (SELECT 1 FROM neon_auth."user" WHERE lower(email) = :email)'
            ),
            {"email": email},
        ).scalar_one()
        if not known:
            return
        # Provider errors are useful for diagnosis, but must never contain an OTP.
        error = re.sub(r"\b\d{6}\b", "[redacted]", body.error or "")
        error = re.sub(re.escape(email), "[email]", error, flags=re.IGNORECASE)
        code = re.sub(r"\b\d{6}\b", "[redacted]", body.code or "")
        client_log.log(
            logging.WARNING if body.event.endswith("failed") else logging.INFO,
            "auth.client_event",
            extra={
                "email": email,
                "auth_event": body.event,
                "status": body.status,
                "code": code or None,
                "error": error or None,
            },
        )

    # Why a request was refused, for its log line (the answer's detail).
    @api.exception_handler(StarletteHTTPException)
    async def refused(request: Request, error: StarletteHTTPException) -> Response:
        request.state.detail = error.detail
        return await http_exception_handler(request, error)

    def order_error(request: Request, error: Exception) -> JSONResponse:
        request.state.detail = str(error)
        detail: object = str(error)
        if isinstance(error, meal_edits.RecipeChangesPending):
            detail = {
                "code": "recipe_changes_pending",
                "message": str(error),
                "meals": [b.model_dump(mode="json") for b in error.blockers],
            }
        return JSONResponse({"detail": detail}, status_code=ORDER_ERRORS[type(error)])

    for error_type in ORDER_ERRORS:
        api.add_exception_handler(error_type, order_error)

    @api.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # One message, like every other error the app gets.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors()
        )
        request.state.detail = f"Invalid request: {problems}"
        return JSONResponse(
            {"detail": request.state.detail},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @api.get("/v1/grocery-orders")
    def list_placed(
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        covering_from: date | None = None,
    ) -> list[PlacedOrderOut]:
        """The household's placed orders, oldest first: all of them (History), or
        only those that cover `covering_from` or a later day."""
        placed = orders.placed_orders(session, caller.household_id, covering_from)
        numbers = orders.numbers(session, caller.household_id)
        picks = orders.picks_of(session, [order.id for order in placed])
        lists = shopping.placed_lists(session, [order.id for order in placed])
        people = names(session, {order.placed_by for order in placed if order.placed_by})
        rated = ratings.of_meals(
            session,
            caller.household_id,
            [pick.meal.id for order_picks in picks.values() for pick in order_picks],
        )
        return [
            placed_order_out(
                session, order, numbers[order.id], picks[order.id], lists[order.id], people, rated
            )
            for order in placed
        ]

    @api.get("/v1/grocery-orders/next")
    def get_next(
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> NextOrderOut:
        """The household's next order, and its draft if someone has started one."""
        draft = orders.get_draft(session, caller.household_id)
        target = orders.next_target_date(session, caller.household_id)
        decide_by = orders.decide_by_date(session, caller.household_id, target)
        return NextOrderOut(
            number=orders.next_number(session, caller.household_id),
            target_date=target,
            decide_by_date=decide_by,
            reminders_start_date=orders.reminders_start_date(
                session, caller.household_id, decide_by
            ),
            earliest_delivery_date=booking.store_now().date(),
            delivery_time_zone=get_settings().store_time_zone,
            draft=order_out(session, draft, day, caller.user_id) if draft else None,
        )

    @api.post("/v1/grocery-orders", status_code=status.HTTP_201_CREATED)
    def open_draft(
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Open the household's draft, created if it has none: its lock is taken
        for the caller's ordering session, unless another member has it (the
        draft's `lock` then says who)."""
        draft, created = orders.get_or_create_draft(session, caller.household_id, by)
        if not created:
            response.status_code = status.HTTP_200_OK
        return order_out(session, draft, day, caller.user_id)

    @api.get("/v1/grocery-orders/{order_id}")
    def get_order(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.delete("/v1/grocery-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
    def discard_draft(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """Discard the draft, unless another member is placing it; or cancel an
        approved order that isn't booked yet."""
        order = session.get(GroceryOrder, order_id)
        if order is not None and order.status == GroceryOrderStatus.PLACED:
            booking.cancel(session, caller.household_id, order_id)
        else:
            orders.discard(session, caller.household_id, order_id, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/change")
    def change_order(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Make an approved order that isn't booked yet the household's draft
        again, to change it and approve it again (409 once the next order has a
        draft or has been placed)."""
        order = booking.change(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/book")
    def book_again(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Try booking an approved order again, after it wasn't booked."""
        job = booking.book_again(session, caller.household_id, order_id)
        dispatch_booking(session, dispatcher, job.id)
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/settle")
    def settle_order(
        order_id: UUID,
        body: SettleIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Say whether Instacart has an order booking couldn't confirm."""
        order = booking.settle(session, caller.household_id, order_id, body.placed, caller.user_id)
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/delivery/approve")
    def approve_delivery(
        order_id: UUID,
        body: ApproveDeliveryIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        order, job = booking.approve_delivery(
            session,
            caller.household_id,
            order_id,
            body.proposal_id,
            body.next_delivery_date,
            caller.user_id,
        )
        if job is not None:
            dispatch_booking(session, dispatcher, job.id)
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/delivery/later")
    def defer_delivery(
        order_id: UUID,
        body: DeliveryProposalIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        order = booking.defer_delivery(
            session, caller.household_id, order_id, body.proposal_id, caller.user_id
        )
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/lock")
    def take_lock(
        order_id: UUID,
        body: LockIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
    ) -> OrderLockOut:
        """Take the draft's lock for the caller's ordering session, as the flow
        opens it (its own member's other session gives way); 423 if another
        member has it."""
        order = orders.take_lock(session, caller.household_id, order_id, by, body.step)
        return own_lock(session, order, caller.user_id)

    @api.put("/v1/grocery-orders/{order_id}/lock")
    def renew_lock(
        order_id: UUID,
        body: LockIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
    ) -> OrderLockOut:
        """Renew the lock the caller's ordering session has, with the step it's at
        (every 30 seconds while the flow is open); 423 if it no longer has it."""
        order = orders.renew_lock(session, caller.household_id, order_id, by, body.step)
        return own_lock(session, order, caller.user_id)

    @api.delete("/v1/grocery-orders/{order_id}/lock", status_code=status.HTTP_204_NO_CONTENT)
    def release_lock(
        order_id: UUID,
        by: Annotated[orders.Orderer, Depends(orderer)],
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """Release the lock if the caller's ordering session has it, as the flow
        closes; otherwise do nothing."""
        orders.release_lock(session, caller.household_id, order_id, by.session)

    @api.post("/v1/grocery-orders/{order_id}/jobs", status_code=status.HTTP_201_CREATED)
    def generate_meals(
        order_id: UUID,
        body: JobIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Start a batch of suggestions for the draft."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        job, created = submit_generate_meals(session, draft, caller.user_id, body.job_id)
        start_job(
            session,
            job,
            created,
            response,
            dispatcher,
            "The suggestions could not be started. Try again.",
        )
        meals = orders.job_meals(session, [job.id])[job.id]
        return job_out(
            job,
            meals,
            names(session, {job.requested_by}),
            ratings.of_meals(session, caller.household_id, [meal.id for meal in meals]),
        )

    @api.post("/v1/grocery-orders/{order_id}/list", status_code=status.HTTP_201_CREATED)
    def build_list(
        order_id: UUID,
        body: JobIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Start building the draft's shopping list from its picks and servings now;
        a build already made, or being made, for them is returned instead (200)."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        source = shopping.current_source(session, draft.id)
        job, created = submit_build_list(session, draft, caller.user_id, body.job_id, source)
        start_job(
            session,
            job,
            created,
            response,
            dispatcher,
            "The shopping list could not be started. Try again.",
        )
        return job_out(job, [], names(session, {job.requested_by}), {})

    @api.post("/v1/grocery-orders/{order_id}/list/shop", status_code=status.HTTP_201_CREATED)
    def shop_list(
        order_id: UUID,
        body: JobIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Shop the draft's current list again (after shopping failed); shopping
        already waiting, running, or done for it is returned instead (200)."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        current = shopping.current_build(session, draft.id)
        job, created = submit_shop(session, draft, caller.user_id, body.job_id, current)
        start_job(
            session, job, created, response, dispatcher, "Shopping could not be started. Try again."
        )
        return job_out(job, [], names(session, {job.requested_by}), {})

    @api.post("/v1/grocery-orders/{order_id}/quote", status_code=status.HTTP_201_CREATED)
    def quote_order(
        order_id: UUID,
        body: QuoteIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Find out what the draft would cost at the store now (its final review):
        the cart is filled and checkout read. A quote waiting, running, or done for
        the same products, counts, and tip rule is returned instead (200)."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        meal_edits.require_resolved(session, draft)
        if (problem := orders.shopped_problem(session, draft.id)) is not None:
            session.rollback()
            raise orders.NotReady(problem)
        job, created = quotes.submit_quote(
            session, draft, caller.user_id, body.job_id, body.refresh
        )
        start_job(
            session,
            job,
            created,
            response,
            dispatcher,
            "The total could not be started. Try again.",
        )
        return job_out(job, [], names(session, {job.requested_by}), {})

    @api.patch("/v1/grocery-orders/{order_id}/list/items/{item_id}")
    def change_list_item(
        order_id: UUID,
        item_id: UUID,
        body: ItemIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Take an item off the current list (or put it back), or change how much to buy."""
        orders.change_item(
            session,
            caller.household_id,
            order_id,
            item_id,
            by,
            removed=body.removed,
            quantity=body.quantity,
            count=body.count,
            alternative=body.alternative,
        )
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.post(
        "/v1/grocery-orders/{order_id}/meals/{meal_id}/edits", status_code=status.HTTP_201_CREATED
    )
    def edit_meal(
        order_id: UUID,
        meal_id: UUID,
        body: EditMealIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        job, created = meal_edits.submit(
            session, draft, caller.user_id, meal_id, body.job_id, body.feedback, body.replaces
        )
        start_job(
            session,
            job,
            created,
            response,
            dispatcher,
            "The recipe change could not be started. Try again or keep the current recipe.",
        )
        return order_out(session, draft, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/meals/{meal_id}/edits/{job_id}/accept")
    def accept_edit(
        order_id: UUID,
        meal_id: UUID,
        job_id: UUID,
        body: AcceptEditIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        meal_edits.decide(
            session,
            draft,
            caller.user_id,
            meal_id,
            job_id,
            EditDecision.ACCEPTED,
            body.base_revision,
        )
        return order_out(session, draft, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/meals/{meal_id}/edits/{job_id}/reject")
    def reject_edit(
        order_id: UUID,
        meal_id: UUID,
        job_id: UUID,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        meal_edits.decide(session, draft, caller.user_id, meal_id, job_id, EditDecision.REJECTED)
        return order_out(session, draft, day, caller.user_id)

    @api.put("/v1/grocery-orders/{order_id}/meals/{meal_id}")
    def pick_meal(
        order_id: UUID,
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.pick(session, caller.household_id, order_id, meal_id, by)
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.put("/v1/grocery-orders/{order_id}/meals/{meal_id}/servings")
    def set_servings(
        order_id: UUID,
        meal_id: UUID,
        body: ServingsIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Change how many to make of one of the draft's picks."""
        orders.set_servings(session, caller.household_id, order_id, meal_id, body.servings, by)
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.delete("/v1/grocery-orders/{order_id}/meals/{meal_id}")
    def unpick_meal(
        order_id: UUID,
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.unpick(session, caller.household_id, order_id, meal_id, by)
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.put("/v1/grocery-orders/{order_id}/dates")
    def set_dates(
        order_id: UUID,
        body: DatesIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.set_dates(
            session,
            caller.household_id,
            order_id,
            body.delivery_date,
            body.delivery_by,
            body.next_delivery_date,
            booking.store_now().date(),
            by,
        )
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/place")
    def place_order(
        order_id: UUID,
        body: PlaceIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: DbSession,
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """Approve the draft: it waits to book at the store, and books now if
        its day has come (castle_chef.booking)."""
        placed, now = orders.place(
            session,
            caller.household_id,
            order_id,
            by,
            body.delivery_date,
            body.delivery_by,
            body.next_delivery_date,
            body.quote_id,
            booking.store_now().date(),
        )
        if now:
            job = booking.start_if_due(session, placed, booking.store_now())
            session.commit()
            log.info(
                "grocery_order.placed",
                extra={
                    "grocery_order_id": str(placed.id),
                    "user_id": str(caller.user_id),
                    "delivery_date": placed.delivery_date,
                    "delivery_by": placed.delivery_by,
                    "next_delivery_date": placed.next_delivery_date,
                    "quote_id": str(placed.quote_id),
                },
            )
            if job is not None:
                dispatch_booking(session, dispatcher, job.id)
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.get("/v1/meals/{meal_id}")
    def get_meal(
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> MealOut:
        """A recipe: one of the household's suggestions (every pick is one)."""
        meal = orders.household_meal(session, caller.household_id, meal_id)
        rated = ratings.of_meals(session, caller.household_id, [meal.id])
        return MealOut(
            **meal_summary(meal, rated).model_dump(),
            prepared=prepared_recipe_out(session, meal),
            servings=meal.servings,
            steps=[StepOut.model_validate(step.model_dump()) for step in meal.steps],
            ingredients=[
                IngredientOut.model_validate(ingredient, from_attributes=True)
                for ingredient in meal.ingredients
            ],
        )

    @api.put("/v1/meals/{meal_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
    def rate_meal(
        meal_id: UUID,
        body: RatingIn,
        caller: Annotated[Member, Depends(member)],
        session_id: Annotated[UUID | None, Depends(order_session)],
        session: DbSession,
    ) -> None:
        """Set the household's rating of one of its meals. Not for us also unpicks it
        from the draft, which only the ordering session with its lock can do."""
        by = None if session_id is None else orders.Orderer(caller.user_id, session_id)
        orders.rate_meal(session, caller.household_id, meal_id, body.rating, by)

    @api.delete("/v1/meals/{meal_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
    def clear_meal_rating(
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        """Set one of the household's meals back to not rated."""
        orders.clear_meal_rating(session, caller.household_id, meal_id)

    @api.post("/v1/logs", status_code=status.HTTP_204_NO_CONTENT)
    def send_logs(
        body: LogsIn,
        caller: Annotated[Member, Depends(member)],
        session: DbSession,
    ) -> None:
        email, _ = account(session, caller.user_id)
        batch = {"batch_id": str(uuid4()), "user_id": str(caller.user_id), "email": email}
        client_log.info(
            "client.batch", extra=batch | {"device": body.device, "entries": len(body.entries)}
        )
        for entry in body.entries:
            level = CLIENT_LOG_LEVELS.get(entry.get("level", ""), logging.INFO)
            client_log.log(level, "client.log", extra=batch | {"entry": entry})

    return api


app = create_app()
