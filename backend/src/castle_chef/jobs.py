"""Operations on the `job` table: submitting, claiming, finishing."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from castle_chef_agent.shopping import PreparedRecipes
from sqlalchemy import ColumnElement, case
from sqlmodel import Session, col, func, select, update

from castle_chef.models import GroceryOrder, Household, Job, JobKind, JobStatus, ListSource
from castle_chef.settings import get_settings

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


def retried(
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


def add_job(session: Session, job: Job, **logged: object) -> Job:
    """Add a new job and commit it (with what the caller staged), and log it."""
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={
            "job_id": str(job.id),
            "kind": job.kind,
            "grocery_order_id": str(job.grocery_order_id),
        }
        | logged,
    )
    return job


def newest_job(
    session: Session, order_id: UUID, kind: JobKind, status: JobStatus | None = None
) -> Job | None:
    """The order's newest job of a kind (with that status, if one is given)."""
    query = select(Job).where(Job.grocery_order_id == order_id, Job.kind == kind)
    if status is not None:
        query = query.where(Job.status == status)
    return session.exec(
        query.order_by(col(Job.created_at).desc(), col(Job.id).desc()).limit(1)
    ).first()


def submit_generate_meals(
    session: Session, order: GroceryOrder, member_id: UUID, job_id: UUID
) -> tuple[Job, bool]:
    """Record a member's request for a batch of suggestions for a draft order,
    and commit it. Returns (job, created).

    The caller holds the order's row lock, which serializes the order's
    requests, so two at once can't both start a batch. The client picks the job
    id, so a retried request returns the same job instead of starting another.
    Overdue jobs are failed first, so a lost worker can't block the order.
    """
    if (existing := retried(session, job_id, order, member_id, JobKind.GENERATE_MEALS)) is not None:
        return existing, False
    fail_stale_jobs(session, order.id)
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
    return add_job(session, job), True


def submit_build_list(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    source: ListSource,
) -> tuple[Job, bool]:
    """Start building the draft's shopping list from `source` (its picks now),
    and commit. Returns (job, created): a build already made, being made, or
    waiting for the same picks is returned rather than started again, and a
    retried request (the client picks the id) gets the job it made. Builds for
    other picks still waiting or running, and shopping still under way, are
    failed as superseded in the same transaction, so their results are dropped.
    The caller holds the order's row lock."""
    from castle_chef.meal_edits import require_resolved

    require_resolved(session, order)
    if (existing := retried(session, job_id, order, member_id, JobKind.BUILD_LIST)) is not None:
        return existing, False
    if not source.picks:
        session.rollback()
        raise NothingToList("Pick at least one meal.")
    fail_stale_jobs(session, order.id)
    newest = newest_job(session, order.id, JobKind.BUILD_LIST)
    if (
        newest is not None
        and newest.list_source == source
        and newest.status in (*ACTIVE, JobStatus.SUCCEEDED)
    ):
        session.rollback()
        return newest, False
    # Shopping an older list too: its products would be for items being replaced.
    superseded = _fail(
        session,
        SUPERSEDED_ERROR,
        col(Job.grocery_order_id) == order.id,
        col(Job.kind).in_((JobKind.BUILD_LIST, JobKind.SHOP)),
        col(Job.status).in_(ACTIVE),
    )
    job = Job(
        id=job_id,
        grocery_order_id=order.id,
        kind=JobKind.BUILD_LIST,
        requested_by=member_id,
        list_source=source,
    )
    return add_job(session, job, superseded=superseded), True


def claim_job(session: Session, job_id: UUID) -> Job | None:
    """Mark a pending job as running and return it, or None if it was already
    claimed (a duplicate delivery) or overdue. Raises LookupError if the job does not exist."""
    job = session.scalars(
        update(Job)
        .where(col(Job.id) == job_id, col(Job.status) == JobStatus.PENDING, ~_stale())
        .values(status=JobStatus.RUNNING, started_at=func.now(), updated_at=func.now())
        .returning(Job)
    ).first()
    session.commit()
    if job is not None:
        session.refresh(job)  # loaded, so it stays readable after the session closes
        return job
    if fail_if_stale(session, job_id):
        log.error("job.stale_failed", extra={"job_id": str(job_id)})
    if session.get(Job, job_id) is None:
        raise LookupError(f"Job {job_id} does not exist.")
    return None


def finish_job(
    session: Session,
    job_id: UUID,
    error: str | None = None,
    *,
    prepared_recipes: PreparedRecipes | None = None,
) -> bool:
    """Mark a running job succeeded (no error) or failed, in the caller's
    transaction, and commit. Returns False, rolling back everything the caller
    staged (e.g. its meals), if the job is no longer running (it was failed as
    stale meanwhile)."""
    finished = session.exec(
        update(Job)
        .where(col(Job.id) == job_id, col(Job.status) == JobStatus.RUNNING)
        .values(
            status=JobStatus.FAILED if error else JobStatus.SUCCEEDED,
            error=error,
            prepared_recipes=prepared_recipes,
            finished_at=func.now(),
            updated_at=func.now(),
        )
    ).rowcount
    if finished:
        session.commit()
    else:
        session.rollback()
    return bool(finished)


def _fail(session: Session, error: str, *where: ColumnElement[bool]) -> int:
    """Stage failing the jobs that match (the caller commits); how many it fails."""
    return session.exec(
        update(Job)
        .where(*where)
        .values(status=JobStatus.FAILED, error=error, finished_at=func.now(), updated_at=func.now())
    ).rowcount


def fail_job(session: Session, job_id: UUID, error: str) -> bool:
    """Fail a job that hasn't finished (pending or running) and commit. Returns
    False if it had already finished."""
    failed = _fail(session, error, col(Job.id) == job_id, col(Job.status).in_(ACTIVE))
    session.commit()
    return bool(failed)


STALE_ERROR = "The job stopped without finishing (its worker never started or was lost)."

# How much longer than the agent's limit a job may run before it counts as lost
# (its task died): for the work around the agent.
RUNNING_MARGIN = timedelta(minutes=5)
# How long a worker can run: docker/Dockerfile's ENTRYPOINT stops it after 2400 s
# (`timeout`) and kills it 30 s after that (`--kill-after`).
WORKER_LIMIT = timedelta(seconds=2400 + 30)
# A booking counts as lost only once its worker can't still be running (it may yet
# press Place order, or be pressing it): past the worker's limit, with a margin for
# the clocks of whoever checks.
BOOKING_MARGIN = timedelta(minutes=5)


def _pending_limit() -> timedelta:
    """How long a job may wait for its task to start before it counts as lost."""
    return timedelta(seconds=get_settings().pending_timeout_seconds)


def _running_limit(kind: JobKind) -> timedelta:
    """How long a job of this kind may run before it counts as lost."""
    if kind == JobKind.BOOK:
        return WORKER_LIMIT + BOOKING_MARGIN
    return timedelta(seconds=get_settings().agent_timeout_seconds) + RUNNING_MARGIN


def is_stale(job: Job, now: datetime) -> bool:
    """Whether an unfinished job is overdue: its task never started, or was lost."""
    if job.status == JobStatus.PENDING:
        return job.created_at < now - _pending_limit()
    if job.status == JobStatus.RUNNING:
        return job.started_at is not None and job.started_at < now - _running_limit(job.kind)
    return False


def _stale() -> ColumnElement[bool]:
    """is_stale, in SQL, by the database's clock."""
    running_limit = case({kind: _running_limit(kind) for kind in JobKind}, value=col(Job.kind))
    pending = (col(Job.status) == JobStatus.PENDING) & (
        col(Job.created_at) < func.now() - _pending_limit()
    )
    running = (col(Job.status) == JobStatus.RUNNING) & (
        col(Job.started_at) < func.now() - running_limit
    )
    return pending | running


def fail_stale_jobs(session: Session, grocery_order_id: UUID) -> int:
    """Fail an order's overdue jobs (is_stale; a backstop for lost events), so
    they don't block its next batch. Only that order's rows: the caller holds
    only that order's lock. Stages the update; the caller commits."""
    stale = _fail(session, STALE_ERROR, col(Job.grocery_order_id) == grocery_order_id, _stale())
    if stale:
        log.error("jobs.stale_failed", extra={"count": stale})
    return stale


def fail_if_stale(session: Session, job_id: UUID) -> bool:
    """Fail the job if it's overdue (is_stale), and commit. Judged in the update
    itself, so a job claimed or finished meanwhile is left alone. Returns whether
    it failed it."""
    failed = _fail(session, STALE_ERROR, col(Job.id) == job_id, _stale())
    session.commit()
    return bool(failed)


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
    order = session.get_one(GroceryOrder, build.grocery_order_id)
    household = session.get_one(Household, order.household_id)
    job = Job(
        id=job_id,
        grocery_order_id=build.grocery_order_id,
        kind=JobKind.SHOP,
        requested_by=member_id,
        build_id=build.id,
        delivery_address=household.delivery_address,
    )
    session.add(job)
    return job


def submit_shop(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    build: Job | None,
) -> tuple[Job, bool]:
    """Shop the order's current list again (after a failure), and commit. Returns
    (job, created): a shop already waiting, running, or done for this list is
    returned rather than started again, and a retried request gets the job it
    made. `build` is the order's current list (None: not ready). The caller holds
    the order's row lock."""
    from castle_chef.meal_edits import require_resolved

    require_resolved(session, order)
    if (existing := retried(session, job_id, order, member_id, JobKind.SHOP)) is not None:
        return existing, False
    if build is None:
        session.rollback()
        raise ListNotReady("The shopping list isn't ready yet.")
    fail_stale_jobs(session, order.id)
    current = shop_of(session, build.id)
    household = session.get_one(Household, order.household_id)
    if (
        current is not None
        and current.status in (*ACTIVE, JobStatus.SUCCEEDED)
        and current.delivery_address == household.delivery_address
    ):
        session.rollback()
        return current, False
    return add_job(session, create_shop(session, build, job_id, member_id)), True


def fail_shops_of(session: Session, build_id: UUID, error: str) -> int:
    """Fail the build's shop jobs that haven't finished, and commit (their worker
    stopped with the build's task)."""
    failed = _fail(session, error, col(Job.build_id) == build_id, col(Job.status).in_(ACTIVE))
    session.commit()
    return failed
