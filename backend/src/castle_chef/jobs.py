"""Operations on the `job` table: submitting, claiming, finishing."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, func, select, update

from castle_chef.models import HouseholdMember, Job, JobStatus

log = logging.getLogger(__name__)

GENERATE_MEALS = "generate_meals"
ACTIVE = (JobStatus.PENDING, JobStatus.RUNNING)


class GenerateMealsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=14)


class TooManyActiveJobs(Exception):
    pass


class JobIdTaken(Exception):
    """The client's job id already belongs to another job (someone else's, or
    a different request)."""


class NotAMember(Exception):
    pass


def create_job(
    session: Session,
    kind: str,
    params: dict,
    household_id: UUID,
    requested_by: UUID | None = None,
) -> Job:
    """Insert and commit a household's pending job (no limits; for the CLI and tests)."""
    job = Job(kind=kind, params=params, household_id=household_id, requested_by=requested_by)
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={
            "job_id": str(job.id),
            "kind": kind,
            "params": params,
            "household_id": str(household_id),
        },
    )
    return job


def submit_job(
    session: Session,
    member_id: UUID,
    household_id: UUID,
    job_id: UUID,
    params: GenerateMealsParams,
    max_active: int,
    stale_after: tuple[timedelta, timedelta],
) -> tuple[Job, bool]:
    """Record a member's meal request for their household and commit it.
    Returns (job, created).

    The client picks the job id, so a retried request returns the same job
    instead of creating another. Locking the member's row serializes that
    member's submissions, so two at once can't both pass the limit. Stale jobs
    are failed first, so a crashed task can't hold one of the member's slots.
    """
    locked = session.exec(
        select(HouseholdMember)
        .where(HouseholdMember.user_id == member_id, HouseholdMember.household_id == household_id)
        .with_for_update()
    ).first()
    if locked is None:  # removed from the household since the membership check
        session.rollback()
        raise NotAMember(f"{member_id} is not in household {household_id}.")
    existing = session.get(Job, job_id)
    if existing is not None:
        session.rollback()
        if (existing.requested_by, existing.household_id, existing.params) != (
            member_id,
            household_id,
            params.model_dump(),
        ):
            raise JobIdTaken(f"Job id {job_id} is taken.")
        return existing, False
    fail_stale_jobs(session, member_id, *stale_after)
    active = session.exec(
        select(func.count())
        .select_from(Job)
        .where(Job.requested_by == member_id, col(Job.status).in_(ACTIVE))
    ).one()
    if active >= max_active:
        session.rollback()
        raise TooManyActiveJobs(f"At most {max_active} requests can be waiting at a time.")
    job = Job(
        id=job_id,
        kind=GENERATE_MEALS,
        params=params.model_dump(),
        household_id=household_id,
        requested_by=member_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={"job_id": str(job.id), "params": job.params, "household_id": str(household_id)},
    )
    return job, True


def claim_job(session: Session, job_id: UUID) -> Job | None:
    """Mark a pending job as running and return it, or None if it was already
    claimed (a duplicate delivery). Raises LookupError if the job doesn't exist."""
    job = (
        session.execute(
            update(Job)
            .where(col(Job.id) == job_id, col(Job.status) == JobStatus.PENDING)
            .values(status=JobStatus.RUNNING, started_at=func.now(), updated_at=func.now())
            .returning(Job)
        )
        .scalars()
        .first()
    )
    session.commit()
    if job is not None:
        session.refresh(job)  # loaded, so it stays readable after the session closes
        return job
    if session.get(Job, job_id) is None:
        raise LookupError(f"Job {job_id} does not exist.")
    return None


def finish_job(session: Session, job_id: UUID, error: str | None = None) -> bool:
    """Mark a running job succeeded (no error) or failed, in the caller's
    transaction, and commit. Returns False, rolling back everything the caller
    staged (e.g. its meals), if the job is no longer running (it was failed as
    stale meanwhile)."""
    finished = session.execute(
        update(Job)
        .where(col(Job.id) == job_id, col(Job.status) == JobStatus.RUNNING)
        .values(
            status=JobStatus.FAILED if error else JobStatus.SUCCEEDED,
            error=error,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    ).rowcount
    if finished:
        session.commit()
    else:
        session.rollback()
    return bool(finished)


def fail_job(session: Session, job_id: UUID, error: str) -> bool:
    """Fail a job that hasn't finished (pending or running) and commit. Returns
    False if it had already finished."""
    failed = session.execute(
        update(Job)
        .where(col(Job.id) == job_id, col(Job.status).in_(ACTIVE))
        .values(status=JobStatus.FAILED, error=error, finished_at=func.now(), updated_at=func.now())
    ).rowcount
    session.commit()
    return bool(failed)


STALE_ERROR = "The job stopped without finishing (its worker never started or was lost)."


def is_stale(job: Job, now: datetime, pending_after: timedelta, running_after: timedelta) -> bool:
    """Whether an unfinished job is overdue: its task never started, or was lost."""
    if job.status == JobStatus.PENDING:
        return job.created_at < now - pending_after
    if job.status == JobStatus.RUNNING:
        return job.started_at is not None and job.started_at < now - running_after
    return False


def fail_stale_jobs(
    session: Session, member_id: UUID, pending_after: timedelta, running_after: timedelta
) -> int:
    """Fail a member's overdue jobs (a backstop for lost events), so they don't
    hold the member's slots. Only that member's rows: the caller holds only that
    member's lock. Stages the update; the caller commits."""
    stale = session.execute(
        update(Job)
        .where(col(Job.requested_by) == member_id)
        .where(
            (
                (col(Job.status) == JobStatus.PENDING)
                & (col(Job.created_at) < func.now() - pending_after)
            )
            | (
                (col(Job.status) == JobStatus.RUNNING)
                & (col(Job.started_at) < func.now() - running_after)
            )
        )
        .values(
            status=JobStatus.FAILED,
            error=STALE_ERROR,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    ).rowcount
    if stale:
        log.error("jobs.stale_failed", extra={"count": stale})
    return stale
