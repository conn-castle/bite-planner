"""Daily reminders: one EventBridge Scheduler schedule per member, at their
notification time in their phone's zone, invokes the Reminders Lambda function
(the API image; the Lambda Web Adapter posts each event to `/events`). It checks
the database and sends that day's reminder if one is due. Nothing polls.

The database is the source of truth; a schedule is a copy of the member's time
and zone. The API updates it when either changes (under the member's row lock),
a firing that doesn't match the database re-creates it, and every deploy runs a
sync that rebuilds them all (`{"version": 1, "sync": true}`).

EventBridge Scheduler runs a time repeated by a daylight saving change once, and
skips a time in the hour lost in spring: that day has no reminder for it.
"""

import json
import logging
from datetime import date, datetime, time
from functools import lru_cache
from typing import Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import update
from sqlmodel import Session, col, select

from castle_chef import booking, push
from castle_chef.db import get_session
from castle_chef.dispatch import DispatchError, get_dispatcher
from castle_chef.jobs import fail_job
from castle_chef.models import AppUser
from castle_chef.notifications import notify_booking, reminder
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

SCHEDULE_PREFIX = "reminder-"
# Scheduler's retries of a failed invocation, and how late it may still deliver one.
RETRY_ATTEMPTS = 3
MAX_EVENT_AGE_SECONDS = 15 * 60


class ReminderEvent(BaseModel):
    """What a member's schedule sends: Scheduler fills in the time it was due."""

    version: Literal[1]
    user_id: UUID
    scheduled_time: datetime


class SyncEvent(BaseModel):
    version: Literal[1]
    sync: Literal[True]


class BookDueEvent(BaseModel):
    """The daily booking schedule's firing (infra: BOOKING_SCHEDULE)."""

    model_config = ConfigDict(extra="forbid")

    book_due: dict[str, Any]


class SyncResult(BaseModel):
    upserted: int
    deleted: int
    failed: int


class ScheduleError(Exception):
    pass


class ReminderNotSent(Exception):
    """The reminder reached none of the member's phones (their day is given back)."""


class ReminderScheduler(Protocol):
    def upsert(self, user_id: UUID, at: time, zone: str) -> None: ...
    def delete(self, user_id: UUID) -> None: ...
    def user_ids(self) -> set[UUID]: ...


def schedule_name(user_id: UUID) -> str:
    return f"{SCHEDULE_PREFIX}{user_id}"


class AwsReminderScheduler:
    def __init__(self, group: str, function_arn: str, role_arn: str) -> None:
        # Only the API image installs boto3.
        import boto3
        from botocore.config import Config

        self.group = group
        self.function_arn = function_arn
        self.role_arn = role_arn
        # Bounded, so a stuck call fails well within a request's time limit.
        self._scheduler = boto3.client(
            "scheduler",
            config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
        )

    def _schedule(self, user_id: UUID, at: time, zone: str) -> dict[str, Any]:
        """The whole schedule, for creating and updating alike (an update replaces
        every setting, so nothing may be left out)."""
        return {
            "Name": schedule_name(user_id),
            "GroupName": self.group,
            "ScheduleExpression": f"cron({at.minute} {at.hour} * * ? *)",
            "ScheduleExpressionTimezone": zone,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED",
            "ActionAfterCompletion": "NONE",
            "Target": {
                "Arn": self.function_arn,
                "RoleArn": self.role_arn,
                "Input": json.dumps(
                    {
                        "version": 1,
                        "user_id": str(user_id),
                        "scheduled_time": "<aws.scheduler.scheduled-time>",
                    }
                ),
                "RetryPolicy": {
                    "MaximumRetryAttempts": RETRY_ATTEMPTS,
                    "MaximumEventAgeInSeconds": MAX_EVENT_AGE_SECONDS,
                },
            },
        }

    def upsert(self, user_id: UUID, at: time, zone: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        schedule = self._schedule(user_id, at, zone)
        try:
            try:
                self._scheduler.update_schedule(**schedule)
            except ClientError as error:
                if _code(error) != "ResourceNotFoundException":
                    raise
                try:
                    self._scheduler.create_schedule(**schedule)
                except ClientError as create_error:
                    # Created meanwhile by another request: update that one.
                    if _code(create_error) != "ConflictException":
                        raise
                    self._scheduler.update_schedule(**schedule)
        except (BotoCoreError, ClientError) as error:
            raise ScheduleError(f"Could not save {schedule['Name']}: {error}") from error

    def delete(self, user_id: UUID) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        name = schedule_name(user_id)
        try:
            self._scheduler.delete_schedule(Name=name, GroupName=self.group)
        except ClientError as error:
            if _code(error) != "ResourceNotFoundException":
                raise ScheduleError(f"Could not delete {name}: {error}") from error
        except BotoCoreError as error:
            raise ScheduleError(f"Could not delete {name}: {error}") from error

    def user_ids(self) -> set[UUID]:
        from botocore.exceptions import BotoCoreError, ClientError

        ids: set[UUID] = set()
        try:
            pages = self._scheduler.get_paginator("list_schedules").paginate(
                GroupName=self.group, NamePrefix=SCHEDULE_PREFIX
            )
            for page in pages:
                for schedule in page["Schedules"]:
                    ids.add(UUID(schedule["Name"].removeprefix(SCHEDULE_PREFIX)))
        except (BotoCoreError, ClientError) as error:
            raise ScheduleError(f"Could not list the reminder schedules: {error}") from error
        return ids


def _code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    return response.get("Error", {}).get("Code") if isinstance(response, dict) else None


class LocalReminderScheduler:
    """Development: nothing is scheduled; changes are logged."""

    def upsert(self, user_id: UUID, at: time, zone: str) -> None:
        log.info(
            "reminders.schedule_skipped",
            extra={"user_id": str(user_id), "at": at.isoformat(), "zone": zone},
        )

    def delete(self, user_id: UUID) -> None:
        log.info("reminders.schedule_skipped", extra={"user_id": str(user_id), "deleted": True})

    def user_ids(self) -> set[UUID]:
        return set()


@lru_cache
def get_scheduler() -> ReminderScheduler:
    """The scheduler CASTLE_CHEF_REMINDER_SCHEDULER selects."""
    settings = get_settings()
    if settings.reminder_scheduler == "local":
        return LocalReminderScheduler()
    if not (
        settings.reminder_schedule_group
        and settings.reminder_function_arn
        and settings.reminder_role_arn
    ):
        raise RuntimeError(
            "Set CASTLE_CHEF_REMINDER_SCHEDULE_GROUP, CASTLE_CHEF_REMINDER_FUNCTION_ARN, "
            "and CASTLE_CHEF_REMINDER_ROLE_ARN."
        )
    return AwsReminderScheduler(
        settings.reminder_schedule_group,
        settings.reminder_function_arn,
        settings.reminder_role_arn,
    )


def lock_user(session: Session, user_id: UUID) -> AppUser | None:
    """The member's row, locked until the transaction ends: their settings and
    their schedule change one at a time."""
    return session.exec(select(AppUser).where(AppUser.user_id == user_id).with_for_update()).first()


def save_schedule(user: AppUser) -> None:
    """Make the member's schedule match their row (which the caller has locked):
    deleted while they have no time zone."""
    scheduler = get_scheduler()
    if user.time_zone is None:
        scheduler.delete(user.user_id)
    else:
        scheduler.upsert(user.user_id, user.notification_time, user.time_zone)


def reconcile(user_id: UUID) -> Literal["upserted", "deleted"]:
    """Make one member's schedule match the database; a user who's gone loses it."""
    with get_session() as session:
        user = lock_user(session, user_id)
        if user is None:
            get_scheduler().delete(user_id)
            return "deleted"
        save_schedule(user)
        return "deleted" if user.time_zone is None else "upserted"


def sync() -> SyncResult:
    """Rebuild every schedule from the database: each member with a time zone has
    one, and nobody else does. Run after every deploy."""
    scheduler = get_scheduler()
    with get_session() as session:
        members = set(session.exec(select(AppUser.user_id)).all())
    counts = {"upserted": 0, "deleted": 0, "failed": 0}
    for user_id in sorted(members | scheduler.user_ids()):
        try:
            counts[reconcile(user_id)] += 1
        except ScheduleError as error:
            counts["failed"] += 1
            log.error("reminders.sync_failed", extra={"user_id": str(user_id), "error": str(error)})
    result = SyncResult.model_validate(counts)
    log.info("reminders.synced", extra=result.model_dump())
    return result


def remind(event: ReminderEvent) -> None:
    """A member's schedule fired: send their reminder if one is due today (their
    day, at the time the schedule was due), at most one a day."""
    fields = {"user_id": str(event.user_id), "scheduled_time": event.scheduled_time.isoformat()}
    with get_session() as session:
        user = lock_user(session, event.user_id)
        if user is None:
            get_scheduler().delete(event.user_id)
            log.info("reminders.member_gone", extra=fields)
            return
        if user.time_zone is None:
            # Schedules exist only for members with a zone.
            get_scheduler().delete(event.user_id)
            log.error("reminders.no_time_zone", extra=fields)
            return
        local = event.scheduled_time.astimezone(ZoneInfo(user.time_zone))
        due_at = user.notification_time
        if (local.hour, local.minute) != (due_at.hour, due_at.minute):
            # The schedule is out of date: rebuild it from the database.
            save_schedule(user)
            log.error(
                "reminders.stale_schedule",
                extra=fields | {"zone": user.time_zone, "time": due_at.isoformat()},
            )
            return
        day = local.date()
        if not user.reminders_on:
            log.info("reminders.turned_off", extra=fields)
            return
        notification = reminder(session, user, day)
        if notification is None:
            log.info("reminders.not_due", extra=fields | {"day": day.isoformat()})
            return
        if user.reminded_on == day:
            log.info("reminders.already_sent", extra=fields | {"day": day.isoformat()})
            return
        # Claimed before sending (the row is locked): a retry can't send it twice.
        previous = user.reminded_on
        user.reminded_on = day
        session.commit()
    # It reached none of their phones (failed sending, or before sending): give
    # the day back and fail, so Lambda retries the firing (a phone that got it
    # never gets it twice).
    try:
        delivery = push.notify(event.user_id, notification)
    except Exception:
        _give_back(event.user_id, day, previous)
        raise
    if delivery.failed and not delivery.sent:
        _give_back(event.user_id, day, previous)
        raise ReminderNotSent(f"No phone of {event.user_id} could be reached; retried by Lambda.")


def _give_back(user_id: UUID, day: date, previous: date | None) -> None:
    """Undo the claim on `day`, so a retry sends that day's reminder."""
    with get_session() as session:
        session.execute(
            update(AppUser)
            .where(col(AppUser.user_id) == user_id, col(AppUser.reminded_on) == day)
            .values(reminded_on=previous)
        )
        session.commit()


def book_due() -> list[UUID]:
    """The daily booking run (castle_chef.booking): start booking the approved
    orders that are due, and queue their jobs. Returns the jobs queued."""
    with get_session() as session:
        started = booking.start_due(session, booking.store_now())
    queued = []
    for job_id in started:
        try:
            get_dispatcher().dispatch(job_id)
            queued.append(job_id)
        except DispatchError as error:
            log.error("booking.dispatch_failed", extra={"job_id": str(job_id), "error": str(error)})
            with get_session() as session:
                fail_job(session, job_id, "Booking couldn't be started.")
                order = booking.job_failed(session, job_id, "Booking couldn't be started.")
            if order is not None:
                notify_booking(order.id)
    log.info("booking.due_started", extra={"jobs": len(queued)})
    return queued


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Handle one Lambda event: a member's schedule firing, the daily booking
    run, or the deploy's sync."""
    try:
        if "sync" in event:
            SyncEvent.model_validate(event)
            return sync().model_dump()
        if "book_due" in event:
            BookDueEvent.model_validate(event)
            return {"queued": [str(job_id) for job_id in book_due()]}
        remind(ReminderEvent.model_validate(event))
        return {"status": "handled"}
    except ValidationError as error:
        log.error("reminders.bad_event", extra={"keys": sorted(event), "error": str(error)})
        raise
