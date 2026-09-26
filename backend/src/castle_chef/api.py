"""The Castle Chef API: what the app calls. Runs on AWS Lambda (through the Lambda
Web Adapter) in production, and with `uvicorn castle_chef.api:app` locally."""

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import bindparam, text
from sqlmodel import Session, col, select

from castle_chef import log as logging_config
from castle_chef import orders, ratings
from castle_chef.auth import AuthError, AuthUnavailable, AuthVerifier, NeonAuthVerifier, Principal
from castle_chef.db import get_request_session
from castle_chef.dispatch import DispatchError, JobDispatcher, get_dispatcher
from castle_chef.failures import handle_event
from castle_chef.jobs import (
    STALE_ERROR,
    BatchRunning,
    JobIdTaken,
    fail_job,
    is_stale,
    submit_generate_meals,
)
from castle_chef.models import (
    GroceryOrder,
    GroceryOrderStatus,
    Household,
    HouseholdMember,
    Job,
    JobKind,
    JobStatus,
    Meal,
    MealRating,
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
    earliest_delivery_date: date
    delivery_date: date | None
    next_delivery_date: date | None
    # Its jobs, oldest first: the suggestions are their meals.
    jobs: list[JobOut]
    # The picks.
    meal_ids: list[UUID]


class PlacedOrderOut(BaseModel):
    """A placed order. It can't change, so it has no suggestions, and its dates are set."""

    id: UUID
    number: int
    placed_at: datetime
    placed_by: MemberOut
    delivery_date: date
    next_delivery_date: date
    # The picked meals, in the order they were suggested.
    meals: list[MealSummaryOut]


class NextOrderOut(BaseModel):
    """The household's next order, whether or not anyone has started its draft."""

    # "Order 4": the number it has, or will have once started.
    number: int
    # The last placed order's next delivery date; none before the first order.
    target_date: date | None
    decide_by_date: date | None
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


class HouseholdOut(BaseModel):
    id: UUID
    name: str


class MeOut(BaseModel):
    user_id: UUID
    email: str | None
    name: str | None
    # Whether the account is in a household (the API serves only those).
    member: bool
    household: HouseholdOut | None


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Chosen by the app, so a retried request can't start a second batch.
    job_id: UUID


class DatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_date: date | None
    next_delivery_date: date | None


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
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown time zone {time_zone!r}."
        ) from error
    return datetime.now(zone).date()


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


def order_out(session: Session, order: GroceryOrder, today: date) -> GroceryOrderOut:
    """An order as the app sees it."""
    jobs = orders.jobs(session, order.id)
    meals = orders.job_meals(session, [job.id for job in jobs])
    people = names(session, {order.created_by, *(job.requested_by for job in jobs)})
    rated = ratings.of_meals(
        session, order.household_id, [meal.id for batch in meals.values() for meal in batch]
    )

    target = orders.target_date(session, order)
    return GroceryOrderOut(
        id=order.id,
        number=orders.number(session, order),
        status=order.status,
        created_by=MemberOut(user_id=order.created_by, name=people[order.created_by]),
        created_at=order.created_at,
        target_date=target,
        decide_by_date=orders.decide_by_date(session, order.household_id, target),
        earliest_delivery_date=orders.earliest_delivery_date(today),
        delivery_date=order.delivery_date,
        next_delivery_date=order.next_delivery_date,
        jobs=[job_out(job, meals[job.id], people, rated) for job in jobs],
        meal_ids=orders.picks(session, order.id),
    )


def placed_order_out(
    order: GroceryOrder,
    number: int,
    meals: list[Meal],
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
        meals=[meal_summary(meal, rated) for meal in meals],
    )


# Expected failures of order and meal requests, as the status the app gets.
ORDER_ERRORS: dict[type[Exception], int] = {
    orders.OrderNotFound: status.HTTP_404_NOT_FOUND,
    orders.NotASuggestion: status.HTTP_404_NOT_FOUND,
    orders.MealNotFound: status.HTTP_404_NOT_FOUND,
    orders.OrderPlaced: status.HTTP_409_CONFLICT,
    orders.NotReady: status.HTTP_409_CONFLICT,
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
    """The user's household, or None if they aren't in one (a membership row
    without a household doesn't count)."""
    return session.exec(
        select(Household)
        .join(HouseholdMember, col(HouseholdMember.household_id) == Household.id)
        .where(HouseholdMember.user_id == user_id)
    ).first()


def member(
    principal: Annotated[Principal, Depends(signed_in)],
    session: Annotated[Session, Depends(db)],
) -> Member:
    household = household_of(session, principal.user_id)
    if household is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_IN_A_HOUSEHOLD)
    return Member(user_id=principal.user_id, household_id=household.id)


def account(session: Session, user_id: UUID) -> tuple[str | None, str | None]:
    row = session.execute(
        text('SELECT email, name FROM neon_auth."user" WHERE id = :id'), {"id": user_id}
    ).first()
    return (row.email, row.name) if row else (None, None)


# The app.


def create_app() -> FastAPI:
    logging_config.configure(None)  # JSON lines on stderr; CloudWatch on Lambda
    load_ssm_parameters()  # once per cold start, before settings are read
    settings = get_settings()
    api = FastAPI(title="Castle Chef", docs_url=None, redoc_url=None)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Time-Zone"],
    )

    @api.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.monotonic()
        response = await call_next(request)
        # The route's template, so the line carries no ids.
        route = getattr(request.scope.get("route"), "path", None)
        log.info(
            "http.request",
            extra={
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "ms": round((time.monotonic() - started) * 1000),
            },
        )
        return response

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if settings.failure_handler:

        @api.post("/events")
        def events(event: dict[str, Any]) -> dict[str, str]:
            # Non-HTTP Lambda events, posted here by the Lambda Web Adapter.
            handle_event(event)
            return {"status": "handled"}

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
            household=HouseholdOut(id=household.id, name=household.name) if household else None,
        )

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
        meals = orders.picked_meals_of(session, [order.id for order in placed])
        people = names(session, {order.placed_by for order in placed if order.placed_by})
        rated = ratings.of_meals(
            session, caller.household_id, [meal.id for picked in meals.values() for meal in picked]
        )
        return [
            placed_order_out(order, numbers[order.id], meals[order.id], people, rated)
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
        return NextOrderOut(
            number=orders.next_number(session, caller.household_id),
            target_date=target,
            decide_by_date=orders.decide_by_date(session, caller.household_id, target),
            earliest_delivery_date=orders.earliest_delivery_date(day),
            draft=order_out(session, draft, day) if draft else None,
        )

    @api.post("/v1/grocery-orders", status_code=status.HTTP_201_CREATED)
    def open_draft(
        response: Response,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        """The household's draft, created if it has none."""
        draft, created = orders.get_or_create_draft(session, caller.household_id, caller.user_id)
        if not created:
            response.status_code = status.HTTP_200_OK
        return order_out(session, draft, day)

    @api.get("/v1/grocery-orders/{order_id}")
    def get_order(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        return order_out(session, orders.get_order(session, caller.household_id, order_id), day)

    @api.delete("/v1/grocery-orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
    def discard_draft(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> None:
        orders.discard(session, caller.household_id, order_id)

    @api.post("/v1/grocery-orders/{order_id}/jobs", status_code=status.HTTP_201_CREATED)
    def generate_meals(
        order_id: UUID,
        body: JobIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        """Start a batch of suggestions for the draft."""
        draft = orders.lock_draft(session, caller.household_id, order_id)
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

    @api.put("/v1/grocery-orders/{order_id}/meals/{meal_id}")
    def pick_meal(
        order_id: UUID,
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.pick(session, caller.household_id, order_id, meal_id)
        return order_out(session, orders.get_order(session, caller.household_id, order_id), day)

    @api.delete("/v1/grocery-orders/{order_id}/meals/{meal_id}")
    def unpick_meal(
        order_id: UUID,
        meal_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.unpick(session, caller.household_id, order_id, meal_id)
        return order_out(session, orders.get_order(session, caller.household_id, order_id), day)

    @api.put("/v1/grocery-orders/{order_id}/dates")
    def set_dates(
        order_id: UUID,
        body: DatesIn,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        orders.set_dates(
            session, caller.household_id, order_id, body.delivery_date, body.next_delivery_date, day
        )
        return order_out(session, orders.get_order(session, caller.household_id, order_id), day)

    @api.post("/v1/grocery-orders/{order_id}/place")
    def place_order(
        order_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        day: Annotated[date, Depends(today)],
    ) -> GroceryOrderOut:
        placed = orders.place(session, caller.household_id, order_id, caller.user_id, day)
        return order_out(session, placed, day)

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
        session: Annotated[Session, Depends(db)],
    ) -> None:
        """Set the household's rating of one of its meals. Not for us also unpicks it
        from the draft."""
        orders.rate_meal(session, caller.household_id, meal_id, body.rating)

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
