"""Operations on the `job` table: submitting, claiming, finishing."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, func, select, update

from castle_chef.models import GroceryOrder, Job, JobKind, JobStatus

log = logging.getLogger(__name__)

ACTIVE = (JobStatus.PENDING, JobStatus.RUNNING)


class BatchRunning(Exception):
    """The order already has a job waiting or running."""


class JobIdTaken(Exception):
    """The client's job id already belongs to another job (another order's, or
    someone else's request)."""


def submit_generate_meals(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    stale_after: tuple[timedelta, timedelta],
) -> tuple[Job, bool]:
    """Record a member's request for a batch of suggestions for a draft order,
    and commit it. Returns (job, created).

    The caller holds the order's row lock, which serializes the order's
    requests, so two at once can't both start a batch. The client picks the job
    id, so a retried request returns the same job instead of starting another.
    Overdue jobs are failed first, so a lost worker can't block the order.
    """
    existing = session.get(Job, job_id)
    if existing is not None:
        session.rollback()
        if (existing.grocery_order_id, existing.requested_by) != (order.id, member_id):
            raise JobIdTaken(f"Job id {job_id} is taken.")
        return existing, False
    fail_stale_jobs(session, order.id, *stale_after)
    active = session.exec(
        select(func.count())
        .select_from(Job)
        .where(Job.grocery_order_id == order.id, col(Job.status).in_(ACTIVE))
    ).one()
    if active:
        session.rollback()
        raise BatchRunning("A batch of suggestions is already being made.")
    job = Job(
        id=job_id,
        grocery_order_id=order.id,
        kind=JobKind.GENERATE_MEALS,
        requested_by=member_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={"job_id": str(job.id), "kind": job.kind, "grocery_order_id": str(order.id)},
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
    session: Session, grocery_order_id: UUID, pending_after: timedelta, running_after: timedelta
) -> int:
    """Fail an order's overdue jobs (a backstop for lost events), so they don't
    block its next batch. Only that order's rows: the caller holds only that
    order's lock. Stages the update; the caller commits."""
    stale = session.execute(
        update(Job)
        .where(col(Job.grocery_order_id) == grocery_order_id)
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
