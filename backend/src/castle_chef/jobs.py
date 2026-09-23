"""Queue operations on the `job` table."""

import logging
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, func, select, update

from castle_chef.models import Job, JobStatus, utc_now

log = logging.getLogger(__name__)

GENERATE_MEALS = "generate_meals"


class GenerateMealsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=14)


def create_job(session: Session, kind: str, params: dict) -> Job:
    job = Job(kind=kind, params=params)
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info("job.created", extra={"job_id": str(job.id), "kind": kind, "params": params})
    return job


def claim_next_job(session: Session) -> Job | None:
    """Mark the oldest pending job as running and return it.

    `SKIP LOCKED` keeps concurrent workers (the daemon and a manual
    `worker run --once`) from claiming the same job.
    """
    job = session.exec(
        select(Job)
        .where(Job.status == JobStatus.PENDING)
        .order_by(col(Job.created_at))
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        session.rollback()
        return None
    job.status = JobStatus.RUNNING
    job.started_at = utc_now()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def finish_job(session: Session, job_id: UUID, error: str | None = None) -> None:
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(f"Job {job_id} does not exist.")
    job.status = JobStatus.FAILED if error else JobStatus.SUCCEEDED
    job.error = error
    job.finished_at = utc_now()
    session.add(job)
    session.commit()


def fail_stale_jobs(session: Session, older_than: timedelta) -> int:
    """Fail running jobs whose worker died before finishing them."""
    result = session.exec(
        update(Job)
        .where(col(Job.status) == JobStatus.RUNNING)
        .where(col(Job.started_at) < func.now() - older_than)
        .values(
            status=JobStatus.FAILED,
            error=f"Job was still running after {older_than}; the worker likely stopped mid-run.",
            finished_at=func.now(),
            updated_at=func.now(),
        )
    )
    session.commit()
    return result.rowcount
