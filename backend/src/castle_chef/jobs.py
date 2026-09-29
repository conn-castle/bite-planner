"""Operations on the `job` table: submitting, claiming, finishing."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import Session, col, func, select, update

from castle_chef.models import GroceryOrder, Job, JobKind, JobStatus, ListSource

log = logging.getLogger(__name__)

ACTIVE = (JobStatus.PENDING, JobStatus.RUNNING)


class BatchRunning(Exception):
    """The order already has a job waiting or running."""


class JobIdTaken(Exception):
    """The client's job id already belongs to another job (another order's, or
    someone else's request)."""


class NothingToList(Exception):
    """A shopping list needs picks."""


# A build for other picks, failed as a new one starts.
SUPERSEDED_ERROR = "The picks changed, so a new list is being made."


def _retried(
    session: Session, job_id: UUID, order: GroceryOrder, member_id: UUID, kind: JobKind
) -> Job | None:
    """The job a retried request already made (the client picks the id), or
    None; JobIdTaken if the id is someone else's or another order's."""
    existing = session.get(Job, job_id)
    if existing is None:
        return None
    session.rollback()
    if (existing.grocery_order_id, existing.requested_by, existing.kind) != (
        order.id,
        member_id,
        kind,
    ):
        raise JobIdTaken(f"Job id {job_id} is taken.")
    return existing


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
    if (
        existing := _retried(session, job_id, order, member_id, JobKind.GENERATE_MEALS)
    ) is not None:
        return existing, False
    fail_stale_jobs(session, order.id, *stale_after)
    active = session.exec(
        select(func.count())
        .select_from(Job)
        .where(
            Job.grocery_order_id == order.id,
            Job.kind == JobKind.GENERATE_MEALS,
            col(Job.status).in_(ACTIVE),
        )
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


def submit_build_list(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    source: ListSource,
    stale_after: tuple[timedelta, timedelta],
) -> tuple[Job, bool]:
    """Start building the draft's shopping list from `source` (its picks now),
    and commit. Returns (job, created): a build already made, being made, or
    waiting for the same picks is returned rather than started again, and a
    retried request (the client picks the id) gets the job it made. Builds for
    other picks still waiting or running, and shopping still under way, are
    failed as superseded in the same transaction, so their results are dropped.
    The caller holds the order's row lock."""
    if (existing := _retried(session, job_id, order, member_id, JobKind.BUILD_LIST)) is not None:
        return existing, False
    if not source.picks:
        session.rollback()
        raise NothingToList("Pick at least one meal.")
    fail_stale_jobs(session, order.id, *stale_after)
    builds = session.exec(
        select(Job)
        .where(Job.grocery_order_id == order.id, Job.kind == JobKind.BUILD_LIST)
        .order_by(col(Job.created_at).desc(), col(Job.id).desc())
    ).all()
    newest = builds[0] if builds else None
    if (
        newest is not None
        and newest.list_source == source
        and newest.status in (*ACTIVE, JobStatus.SUCCEEDED)
    ):
        session.rollback()
        return newest, False
    superseded = [build.id for build in builds if build.status in ACTIVE]
    # Shopping an older list too: its products would be for items being replaced.
    superseded += session.exec(
        select(Job.id).where(
            Job.grocery_order_id == order.id,
            Job.kind == JobKind.SHOP,
            col(Job.status).in_(ACTIVE),
        )
    ).all()
    if superseded:
        session.execute(
            update(Job)
            .where(col(Job.id).in_(superseded), col(Job.status).in_(ACTIVE))
            .values(
                status=JobStatus.FAILED,
                error=SUPERSEDED_ERROR,
                finished_at=func.now(),
                updated_at=func.now(),
            )
        )
    job = Job(
        id=job_id,
        grocery_order_id=order.id,
        kind=JobKind.BUILD_LIST,
        requested_by=member_id,
        list_source=source,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={
            "job_id": str(job.id),
            "kind": job.kind,
            "grocery_order_id": str(order.id),
            "superseded": len(superseded),
        },
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


# Shopping a list (a `shop` job for a build's items).


class ListNotReady(Exception):
    """There's no finished list for the order's picks to shop for."""


def shop_of(session: Session, build_id: UUID) -> Job | None:
    """The newest shop job for a build."""
    return session.exec(
        select(Job)
        .where(Job.build_id == build_id, Job.kind == JobKind.SHOP)
        .order_by(col(Job.created_at).desc(), col(Job.id).desc())
        .limit(1)
    ).first()


def create_shop(session: Session, build: Job, job_id: UUID, member_id: UUID) -> Job:
    """Stage a shop job for the build's list (the caller commits)."""
    job = Job(
        id=job_id,
        grocery_order_id=build.grocery_order_id,
        kind=JobKind.SHOP,
        requested_by=member_id,
        build_id=build.id,
    )
    session.add(job)
    return job


def submit_shop(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    build: Job | None,
    stale_after: tuple[timedelta, timedelta],
) -> tuple[Job, bool]:
    """Shop the order's current list again (after a failure), and commit. Returns
    (job, created): a shop already waiting, running, or done for this list is
    returned rather than started again, and a retried request gets the job it
    made. `build` is the order's current list (None: not ready). The caller holds
    the order's row lock."""
    if (existing := _retried(session, job_id, order, member_id, JobKind.SHOP)) is not None:
        return existing, False
    if build is None:
        session.rollback()
        raise ListNotReady("The shopping list isn't ready yet.")
    fail_stale_jobs(session, order.id, *stale_after)
    current = shop_of(session, build.id)
    if current is not None and current.status in (*ACTIVE, JobStatus.SUCCEEDED):
        session.rollback()
        return current, False
    job = create_shop(session, build, job_id, member_id)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={"job_id": str(job.id), "kind": job.kind, "grocery_order_id": str(order.id)},
    )
    return job, True


def fail_shops_of(session: Session, build_id: UUID, error: str) -> int:
    """Fail the build's shop jobs that haven't finished, and commit (their worker
    stopped with the build's task)."""
    failed = session.execute(
        update(Job)
        .where(col(Job.build_id) == build_id, col(Job.status).in_(ACTIVE))
        .values(status=JobStatus.FAILED, error=error, finished_at=func.now(), updated_at=func.now())
    ).rowcount
    session.commit()
    return failed
