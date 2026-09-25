"""The Castle Chef API: what the app calls. Runs on AWS Lambda (through the Lambda
Web Adapter) in production, and with `uvicorn castle_chef.api:app` locally."""

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, select

from castle_chef import log as logging_config
from castle_chef.auth import AuthError, AuthUnavailable, AuthVerifier, NeonAuthVerifier, Principal
from castle_chef.db import get_request_session
from castle_chef.dispatch import DispatchError, JobDispatcher, get_dispatcher
from castle_chef.failures import handle_event
from castle_chef.jobs import (
    STALE_ERROR,
    GenerateMealsParams,
    JobIdTaken,
    NotAMember,
    TooManyActiveJobs,
    fail_job,
    is_stale,
    submit_job,
)
from castle_chef.models import Household, HouseholdMember, Job, JobMeal, JobStatus, Meal
from castle_chef.settings import get_settings, load_ssm_parameters

log = logging.getLogger(__name__)
client_log = logging.getLogger("castle_chef.client")

RECENT_JOBS = 50
NOT_IN_A_HOUSEHOLD = "This account isn't in a household."
MAX_LOG_ENTRIES = 2000
# The app's log levels, as Python's. The app logs `error` only for defects
# (crashes, uncaught exceptions), so those alarm like the backend's own errors.
CLIENT_LOG_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING}
MAX_LOG_BYTES = 1_000_000


# Response and request bodies. Field names match what the app already reads.


class JobOut(BaseModel):
    id: UUID
    kind: str
    params: dict[str, Any]
    status: JobStatus
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class MealSummaryOut(BaseModel):
    id: UUID
    name: str


class ListedJobOut(JobOut):
    # The job's meals in the order the agent added them (none until it succeeds).
    meals: list[MealSummaryOut]


class IngredientOut(BaseModel):
    id: UUID
    position: int
    name: str
    quantity: float | None
    unit: str | None
    preparation: str | None


class MealOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    servings: int
    active_time_minutes: int | None
    total_time_minutes: int | None
    steps: list[str]
    created_at: datetime
    meal_ingredient: list[IngredientOut]


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
    # Chosen by the app, so a retried request can't create a second job.
    job_id: UUID = Field(default_factory=uuid4)
    count: int = Field(ge=1, le=14, strict=True)


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


def job_out(job: Job, now: datetime) -> JobOut:
    """A job as the app sees it. An overdue job is shown as failed, the state the
    next submission (or the failure handler) will write, so the app stops
    waiting on it without this read writing anything."""
    out = JobOut.model_validate(job, from_attributes=True)
    if is_stale(job, now, *stale_after()):
        out.status, out.error = JobStatus.FAILED, STALE_ERROR
    return out


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
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
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

    @api.get("/v1/jobs")
    def list_jobs(
        caller: Annotated[Member, Depends(member)], session: Annotated[Session, Depends(db)]
    ) -> list[ListedJobOut]:
        """The household's most recent jobs, newest first, with their meals' names."""
        jobs = session.exec(
            select(Job)
            .where(Job.household_id == caller.household_id)
            .order_by(col(Job.created_at).desc())
            .limit(RECENT_JOBS)
        ).all()
        meals: dict[UUID, list[MealSummaryOut]] = {job.id: [] for job in jobs}
        rows = session.exec(
            select(JobMeal.job_id, Meal.id, Meal.name)
            .join(Meal, col(Meal.id) == JobMeal.meal_id)
            .where(col(JobMeal.job_id).in_(list(meals)))
            .order_by(col(Meal.created_at))
        ).all()
        for job_id, meal_id, name in rows:
            meals[job_id].append(MealSummaryOut(id=meal_id, name=name))
        now = datetime.now(UTC)
        return [ListedJobOut(**job_out(job, now).model_dump(), meals=meals[job.id]) for job in jobs]

    @api.post("/v1/jobs", status_code=status.HTTP_201_CREATED)
    def request_meals(
        body: JobIn,
        response: Response,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
        dispatcher: Annotated[JobDispatcher, Depends(get_dispatcher)],
    ) -> JobOut:
        settings = get_settings()
        try:
            job, created = submit_job(
                session,
                caller.user_id,
                caller.household_id,
                body.job_id,
                GenerateMealsParams(count=body.count),
                settings.max_active_jobs_per_member,
                stale_after(),
            )
        except TooManyActiveJobs as error:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"You already have {settings.max_active_jobs_per_member} requests waiting. "
                "Try again when one finishes.",
            ) from error
        except JobIdTaken as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except NotAMember as error:
            raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_IN_A_HOUSEHOLD) from error
        if not created:
            response.status_code = status.HTTP_200_OK
            return JobOut.model_validate(job, from_attributes=True)
        try:
            dispatcher.dispatch(job.id)
        except DispatchError as error:
            log.error("job.dispatch_failed", extra={"job_id": str(job.id), "error": str(error)})
            fail_job(session, job.id, "The request could not be started. Try again.")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The request could not be started. Try again.",
            ) from error
        return JobOut.model_validate(job, from_attributes=True)

    @api.get("/v1/jobs/{job_id}/meals")
    def job_meals(
        job_id: UUID,
        caller: Annotated[Member, Depends(member)],
        session: Annotated[Session, Depends(db)],
    ) -> list[MealOut]:
        job = session.get(Job, job_id)
        # Another household's job is indistinguishable from a missing one.
        if job is None or job.household_id != caller.household_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request.")
        meals = session.exec(
            select(Meal)
            .join(JobMeal, col(JobMeal.meal_id) == Meal.id)
            .where(JobMeal.job_id == job_id)
            .options(selectinload(Meal.ingredients))  # type: ignore[arg-type]
            .order_by(col(Meal.created_at))
        ).all()
        return [
            MealOut(
                **meal.model_dump(exclude={"updated_at"}),
                meal_ingredient=[
                    IngredientOut.model_validate(ingredient, from_attributes=True)
                    for ingredient in meal.ingredients
                ],
            )
            for meal in meals
        ]

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
