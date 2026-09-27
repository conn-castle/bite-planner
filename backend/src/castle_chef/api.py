"""The Castle Chef API: what the app calls. Runs on AWS Lambda (through the Lambda
Web Adapter) in production, and with `uvicorn castle_chef.api:app` locally."""

import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from time import monotonic
from typing import Annotated, Any, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, Response, status
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

from castle_chef import failures, orders, push, ratings, reminders, shopping
from castle_chef import household as households
from castle_chef import log as logging_config
from castle_chef.auth import AuthError, AuthUnavailable, AuthVerifier, NeonAuthVerifier, Principal
from castle_chef.db import get_request_session
from castle_chef.dispatch import DispatchError, JobDispatcher, get_dispatcher
from castle_chef.jobs import (
    STALE_ERROR,
    BatchRunning,
    JobIdTaken,
    NothingToList,
    fail_job,
    is_stale,
    submit_build_list,
    submit_generate_meals,
)
from castle_chef.models import (
    DECIDE_BY_OFFSET_DAYS,
    REMINDER_LEAD_DAYS,
    SERVINGS,
    Aisle,
    AppUser,
    GroceryOrder,
    GroceryOrderStatus,
    Household,
    Job,
    JobKind,
    JobStatus,
    Meal,
    MealRating,
    OrderStep,
    ShoppingListItem,
)
from castle_chef.settings import get_settings, load_ssm_parameters

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


class MealSummaryOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    active_time_minutes: int | None
    total_time_minutes: int | None
    # The household's rating; null when not rated.
    rating: MealRating | None


class JobOut(BaseModel):
    """One batch of suggestions."""

    id: UUID
    kind: JobKind
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

    servings: int


class ShoppingItemOut(BaseModel):
    """An item of an order's shopping list."""

    id: UUID
    name: str
    # What's ordered, in `unit` (a count when null); null when the recipes give no amount.
    quantity: float | None
    unit: str | None
    aisle: Aisle
    # The picked meals it's for.
    meal_ids: list[UUID]
    # Assumed on hand (salt, pepper, ...): never ordered; the app shows them only on request.
    on_hand: bool
    removed: bool


class ShoppingListOut(BaseModel):
    """A draft's shopping list: its newest build, and the newest one's items that succeeded."""

    # The newest build: its status, error, who started it, and when.
    job: JobOut
    # Ready for the order's picks and servings now: the newest build succeeded, from them.
    current: bool
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
    delivery_date: date | None
    next_delivery_date: date | None
    # Its jobs, oldest first: the suggestions are their meals.
    jobs: list[JobOut]
    # In the order their meals were suggested.
    picks: list[PickOut]
    # Its live lock (a draft's), or null when no one is placing it.
    lock: OrderLockOut | None
    # Its shopping list; null until one is started.
    shopping_list: ShoppingListOut | None


class PlacedOrderOut(BaseModel):
    """A placed order. It can't change, so it has no suggestions, and its dates are set."""

    id: UUID
    number: int
    placed_at: datetime
    placed_by: MemberOut
    delivery_date: date
    next_delivery_date: date
    # The picked meals, in the order they were suggested.
    meals: list[PlacedMealOut]
    # What was ordered for them, by aisle.
    shopping_list: list[ShoppingItemOut]


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
    # Its draft, once someone has started one.
    draft: GroceryOrderOut | None


class IngredientOut(BaseModel):
    name: str
    quantity: float | None
    unit: str | None
    preparation: str | None


class MealOut(MealSummaryOut):
    servings: int
    steps: list[str]
    ingredients: list[IngredientOut]


class HouseholdRefOut(BaseModel):
    id: UUID
    name: str


class HouseholdMemberOut(BaseModel):
    user_id: UUID
    name: str | None
    # For when there's no name.
    email: str | None


class HouseholdOut(BaseModel):
    """The household's settings, shared by everyone in it, and who's in it."""

    id: UUID
    name: str
    decide_by_offset_days: int
    reminder_lead_days: int
    default_servings: int
    # In the order they were invited.
    members: list[HouseholdMemberOut]


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
    name: HouseholdName | None = None
    decide_by_offset_days: int | None = Field(
        default=None, ge=DECIDE_BY_OFFSET_DAYS.low, le=DECIDE_BY_OFFSET_DAYS.high
    )
    reminder_lead_days: int | None = Field(
        default=None, ge=REMINDER_LEAD_DAYS.low, le=REMINDER_LEAD_DAYS.high
    )
    default_servings: int | None = Field(default=None, ge=SERVINGS.low, le=SERVINGS.high)


class PersonalSettingsIn(PartialUpdate):
    notification_time: time | None = None
    time_zone: str | None = None
    reminders_on: bool | None = None

    @field_validator("notification_time")
    @classmethod
    def clock_minute(cls, value: time | None) -> time | None:
        if value is not None and (value.second or value.microsecond or value.tzinfo):
            raise ValueError("A notification time is hours and minutes, with no time zone.")
        return value

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
    next_delivery_date: date | None


class PlaceIn(BaseModel):
    """The dates the member saw when placing, saved with it."""

    model_config = ConfigDict(extra="forbid")

    delivery_date: date
    next_delivery_date: date


class LockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The step the flow shows, for the other members.
    step: OrderStep


class ItemIn(PartialUpdate):
    """A change to a shopping list item: taken off (or put back), or how much to buy."""

    removed: bool | None = None
    quantity: float | None = Field(default=None, gt=0)


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


def stale_after() -> tuple[timedelta, timedelta]:
    """How long a job may stay pending, and running, before it counts as lost."""
    settings = get_settings()
    return (
        timedelta(seconds=settings.pending_timeout_seconds),
        timedelta(seconds=settings.agent_timeout_seconds + 300),
    )


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
    found: dict[UUID, str | None] = {row.id: row.name for row in rows}
    return {user_id: found.get(user_id) for user_id in user_ids}


def meal_summary(meal: Meal, rated: dict[UUID, MealRating]) -> MealSummaryOut:
    """A meal as the app lists it, with its rating from `rated` (the household's
    ratings, by meal id)."""
    return MealSummaryOut(
        id=meal.id,
        name=meal.name,
        description=meal.description,
        active_time_minutes=meal.active_time_minutes,
        total_time_minutes=meal.total_time_minutes,
        rating=rated.get(meal.id),
    )


def job_out(
    job: Job,
    meals: list[Meal],
    people: dict[UUID, str | None],
    rated: dict[UUID, MealRating],
) -> JobOut:
    """A job as the app sees it. An overdue job is shown as failed, the state the
    next request (or the failure handler) will write, so the app stops waiting on
    it without this read writing anything."""
    stale = is_stale(job, datetime.now(UTC), *stale_after())
    return JobOut(
        id=job.id,
        kind=job.kind,
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
    """The lock the user's session just took or renewed."""
    lock = lock_out(order, user_id, names(session, {user_id}))
    if lock is None:  # just taken or renewed, so live
        raise RuntimeError(f"Order {order.id}'s lock isn't live just after taking it.")
    return lock


def item_out(item: ShoppingListItem) -> ShoppingItemOut:
    return ShoppingItemOut.model_validate(item, from_attributes=True)


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
    return ShoppingListOut(
        job=job_out(newest, [], people, rated),
        current=shopping.is_current(session, order.id, newest),
        items=[]
        if built is None
        else [item_out(item) for item in shopping.items_of(session, built.id)],
    )


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
    return GroceryOrderOut(
        id=order.id,
        number=orders.number(session, order),
        status=order.status,
        created_by=MemberOut(user_id=order.created_by, name=people[order.created_by]),
        created_at=order.created_at,
        target_date=target,
        decide_by_date=decide_by,
        reminders_start_date=orders.reminders_start_date(session, order.household_id, decide_by),
        earliest_delivery_date=orders.earliest_delivery_date(today),
        delivery_date=order.delivery_date,
        next_delivery_date=order.next_delivery_date,
        jobs=[job_out(job, meals[job.id], people, rated) for job in jobs],
        picks=[
            PickOut(meal_id=pick.meal.id, servings=pick.servings)
            for pick in orders.picks(session, order.id)
        ],
        lock=lock_out(order, viewer, people),
        shopping_list=shopping_list_out(session, order, people, rated),
    )


def placed_order_out(
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
    # Set when it's placed; the table's checks guarantee it.
    if placed_at is None or placed_by is None or delivery is None or next_delivery is None:
        raise RuntimeError(f"Placed order {order.id} is missing its placement details or dates.")
    return PlacedOrderOut(
        id=order.id,
        number=number,
        placed_at=placed_at,
        placed_by=MemberOut(user_id=placed_by, name=people[placed_by]),
        delivery_date=delivery,
        next_delivery_date=next_delivery,
        meals=[
            PlacedMealOut(**meal_summary(pick.meal, rated).model_dump(), servings=pick.servings)
            for pick in picks
        ],
        shopping_list=[item_out(item) for item in items],
    )


# Expected failures of order and meal requests, as the status the app gets.
ORDER_ERRORS: dict[type[Exception], int] = {
    orders.OrderNotFound: status.HTTP_404_NOT_FOUND,
    orders.NotASuggestion: status.HTTP_404_NOT_FOUND,
    orders.MealNotFound: status.HTTP_404_NOT_FOUND,
    orders.NotPicked: status.HTTP_404_NOT_FOUND,
    orders.OrderPlaced: status.HTTP_409_CONFLICT,
    orders.OrderLocked: status.HTTP_423_LOCKED,
    orders.NotReady: status.HTTP_409_CONFLICT,
    orders.ListNotCurrent: status.HTTP_409_CONFLICT,
    orders.ItemNotFound: status.HTTP_404_NOT_FOUND,
    NothingToList: status.HTTP_409_CONFLICT,
    orders.NotForUs: status.HTTP_409_CONFLICT,
    JobIdTaken: status.HTTP_409_CONFLICT,
    orders.InvalidDates: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BatchRunning: status.HTTP_429_TOO_MANY_REQUESTS,
}


def signed_in(
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
        return verifier.verify(token)
    except AuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(error), headers={"WWW-Authenticate": "Bearer"}
        ) from error
    except AuthUnavailable as error:
        log.error("auth.unavailable", extra={"error": str(error)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is unavailable; try again."
        ) from error


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
    principal: Annotated[Principal, Depends(signed_in)],
    session: Annotated[Session, Depends(db)],
) -> Member:
    household = household_of(session, principal.user_id)
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
    return (row.email, row.name) if row else (None, None)


def personal_settings_out(user: AppUser) -> PersonalSettingsOut:
    return PersonalSettingsOut(
        notification_time=user.notification_time,
        time_zone=user.time_zone,
        reminders_on=user.reminders_on,
    )


def household_out(session: Session, household: Household) -> HouseholdOut:
    return HouseholdOut(
        id=household.id,
        name=household.name,
        decide_by_offset_days=household.decide_by_offset_days,
        reminder_lead_days=household.reminder_lead_days,
        default_servings=household.default_servings,
        members=[
            HouseholdMemberOut(user_id=person.user_id, name=person.name, email=person.email)
            for person in households.members_of(session, household.id)
        ],
    )


# The app.


def create_app() -> FastAPI:
    logging_config.configure(None)  # JSON lines on stderr; CloudWatch on Lambda
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
        response = await call_next(request)
        # The route's template, so the line carries no ids.
        route = getattr(request.scope.get("route"), "path", None)
        log.info(
            "http.request",
            extra={
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "ms": round((monotonic() - started) * 1000),
            },
        )
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
        session: Annotated[Session, Depends(db)],
    ) -> MeOut:
        email, name = account(session, principal.user_id)
        household = household_of(session, principal.user_id)
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
        session: Annotated[Session, Depends(db)],
    ) -> PersonalSettingsOut:
        """Change the caller's own settings; only the fields sent. A new time or zone
        moves their reminder schedule too, or changes nothing if it can't."""
        user = reminders.lock_user(session, caller.user_id)
        if user is None:  # removed since `member` checked
            raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_IN_A_HOUSEHOLD)
        before = (user.notification_time, user.time_zone)
        if body.notification_time is not None:
            user.notification_time = body.notification_time
        if body.time_zone is not None:
            user.time_zone = body.time_zone
        # Turned off, the schedule stays: each firing checks the switch.
        if body.reminders_on is not None:
            user.reminders_on = body.reminders_on
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
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """This phone gets the caller's notifications (taken over from whoever
        signed in on it before)."""
        push.register_device(session, caller.user_id, token.lower())

    @api.delete("/v1/me/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
    def unregister_device(
        token: DeviceToken,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """This phone stops getting the caller's notifications (signing out)."""
        push.unregister_device(session, caller.user_id, token.lower())

    @api.get("/v1/household")
    def get_household(
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> HouseholdOut:
        """The household's settings and members."""
        return household_out(session, session.get_one(Household, caller.household_id))

    @api.patch("/v1/household")
    def update_household(
        body: HouseholdSettingsIn,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> HouseholdOut:
        """Change the household's settings; only the fields sent, so members changing
        different settings at once don't undo each other."""
        household = session.get_one(Household, caller.household_id)
        if body.name is not None:
            household.name = body.name
        if body.decide_by_offset_days is not None:
            household.decide_by_offset_days = body.decide_by_offset_days
        if body.reminder_lead_days is not None:
            household.reminder_lead_days = body.reminder_lead_days
        if body.default_servings is not None:
            household.default_servings = body.default_servings
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

    def order_error(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=ORDER_ERRORS[type(error)])

    for error_type in ORDER_ERRORS:
        api.add_exception_handler(error_type, order_error)

    @api.exception_handler(RequestValidationError)
    def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        # One message, like every other error the app gets.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in error.errors()
        )
        return JSONResponse(
            {"detail": f"Invalid request: {problems}"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @api.get("/v1/grocery-orders")
    def list_placed(
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
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
                order, numbers[order.id], picks[order.id], lists[order.id], people, rated
            )
            for order in placed
        ]

    @api.get("/v1/grocery-orders/next")
    def get_next(
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
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
            earliest_delivery_date=orders.earliest_delivery_date(day),
            draft=order_out(session, draft, day, caller.user_id) if draft else None,
        )

    @api.post("/v1/grocery-orders", status_code=status.HTTP_201_CREATED)
    def open_draft(
        response: Response,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.delete("/v1/grocery-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
    def discard_draft(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """Discard the draft, unless another member is placing it."""
        orders.discard(session, caller.household_id, order_id, caller.user_id)

    @api.post("/v1/grocery-orders/{order_id}/lock")
    def take_lock(
        order_id: UUID,
        body: LockIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Start a batch of suggestions for the draft."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        job, created = submit_generate_meals(
            session, draft, caller.user_id, body.job_id, stale_after()
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        else:
            try:
                dispatcher.dispatch(job.id)
            except DispatchError as error:
                log.error("job.dispatch_failed", extra={"job_id": str(job.id), "error": str(error)})
                fail_job(session, job.id, "The suggestions could not be started. Try again.")
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "The suggestions could not be started. Try again.",
                ) from error
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
        session: Annotated[Session, Depends(db)],
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Start building the draft's shopping list from its picks and servings now;
        a build already made, or being made, for them is returned instead (200)."""
        draft = orders.lock_draft(session, caller.household_id, order_id, by)
        source = shopping.current_source(session, draft.id)
        job, created = submit_build_list(
            session, draft, caller.user_id, body.job_id, source, stale_after()
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        else:
            try:
                dispatcher.dispatch(job.id)
            except DispatchError as error:
                log.error("job.dispatch_failed", extra={"job_id": str(job.id), "error": str(error)})
                fail_job(session, job.id, "The shopping list could not be started. Try again.")
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "The shopping list could not be started. Try again.",
                ) from error
        return job_out(job, [], names(session, {job.requested_by}), {})

    @api.patch("/v1/grocery-orders/{order_id}/list/items/{item_id}")
    def change_list_item(
        order_id: UUID,
        item_id: UUID,
        body: ItemIn,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: Annotated[Session, Depends(db)],
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
        )
        order = orders.get_order(session, caller.household_id, order_id)
        return order_out(session, order, day, caller.user_id)

    @api.put("/v1/grocery-orders/{order_id}/meals/{meal_id}")
    def pick_meal(
        order_id: UUID,
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        by: Annotated[orders.Orderer, Depends(orderer)],
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
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
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.set_dates(
            session,
            caller.household_id,
            order_id,
            body.delivery_date,
            body.next_delivery_date,
            day,
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
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        placed = orders.place(
            session,
            caller.household_id,
            order_id,
            by,
            body.delivery_date,
            body.next_delivery_date,
            day,
        )
        return order_out(session, placed, day, caller.user_id)

    @api.get("/v1/meals/{meal_id}")
    def get_meal(
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> MealOut:
        """A recipe: one of the household's suggestions (every pick is one)."""
        meal = orders.household_meal(session, caller.household_id, meal_id, with_ingredients=True)
        rated = ratings.of_meals(session, caller.household_id, [meal.id])
        return MealOut(
            **meal_summary(meal, rated).model_dump(),
            servings=meal.servings,
            steps=meal.steps,
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
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """Set the household's rating of one of its meals. Not for us also unpicks it
        from the draft, which only the ordering session with its lock can do."""
        by = None if session_id is None else orders.Orderer(caller.user_id, session_id)
        orders.rate_meal(session, caller.household_id, meal_id, body.rating, by)

    @api.delete("/v1/meals/{meal_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
    def clear_meal_rating(
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """Set one of the household's meals back to not rated."""
        orders.clear_meal_rating(session, caller.household_id, meal_id)

    @api.post("/v1/logs", status_code=status.HTTP_204_NO_CONTENT)
    def send_logs(
        body: LogsIn,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> None:
        email, _ = account(session, caller.user_id)
        batch = {"batch_id": str(uuid4()), "user_id": str(caller.user_id), "email": email}
        client_log.info(
            "client.batch", extra=batch | {"device": body.device, "entries": len(body.entries)}
        )
        for entry in body.entries:
            level = CLIENT_LOG_LEVELS.get(entry.get("level"), logging.INFO)
            client_log.log(level, "client.log", extra=batch | {"entry": entry})

    return api


app = create_app()
