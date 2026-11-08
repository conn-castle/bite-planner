"""Durable worker-stop evidence and failed dispatches, delivered through SQS.

Invalid messages raise so Lambda retries and ultimately dead-letters them.
Only the configured AWS lifecycle queue can supply purchase recovery evidence.
"""

import logging
from typing import Any
from uuid import UUID

from sqlmodel import select

from castle_chef import booking
from castle_chef.db import for_update, get_session
from castle_chef.dispatch import JOB_ID_ENV, JobMessage
from castle_chef.jobs import ACTIVE, fail_shops_of
from castle_chef.models import Job, JobKind, JobStatus, utc_now
from castle_chef.notifications import notify_booking, notify_quote
from castle_chef.settings import get_settings
from castle_chef.worker_tasks import QueueBatch, TaskStopped, WorkerStop

log = logging.getLogger(__name__)
WORKER_CONTAINER = "worker"


def handle_event(event: dict[str, Any]) -> None:
    settings = get_settings()
    for record in QueueBatch.model_validate(event).records:
        if record.queue_arn == settings.worker_stops_queue_arn:
            stopped = TaskStopped.model_validate_json(record.body)
            stopped.check_origin()
            _task_stopped(stopped)
        elif record.queue_arn == settings.jobs_dead_letters_queue_arn:
            _dead_lettered(JobMessage.model_validate_json(record.body))
        else:
            raise ValueError(f"Failure message came from an unrecognized queue: {record.queue_arn}")


def _dead_lettered(message: JobMessage) -> None:
    """A failed duplicate dispatch must never fail an already claimed job."""
    with get_session() as session:
        job = session.exec(for_update(select(Job).where(Job.id == message.job_id))).first()
        changed = job is not None and job.status == JobStatus.PENDING
        if changed and job is not None:
            job.status, job.error = (
                JobStatus.FAILED,
                "The request could not be started (no worker could be launched). Try again.",
            )
            job.finished_at = utc_now()
            session.add(job)
        settle_failed = (
            job is not None and job.status == JobStatus.FAILED and job.worker_task_arn is None
        )
        quote_failed = changed and job is not None and job.kind == JobKind.QUOTE
        session.commit()
    log.error("job.never_started", extra={"job_id": str(message.job_id), "marked_failed": changed})
    if settle_failed:
        _booking_stopped(message.job_id, "Booking couldn't be started.")
    if quote_failed:
        notify_quote(message.job_id)


def _task_stopped(event: TaskStopped) -> None:
    detail = event.detail
    workers = [container for container in detail.containers if container.name == WORKER_CONTAINER]
    if len(workers) != 1:
        raise ValueError("Stopped task does not identify exactly one worker container.")
    worker = workers[0]
    job_id = _job_id(event)
    fields: dict[str, object] = {
        "task_arn": detail.task_arn,
        "exit_code": worker.exit_code,
        "reason": detail.stopped_reason,
        "stop_code": detail.stop_code,
    }
    if job_id is None:
        # Reconnect/administrative tasks share this cluster without running a
        # Castle Chef job. Redelivering their stop cannot repair job state.
        if worker.exit_code == 0:
            log.info("worker.non_job_task_stopped", extra=fields)
        else:
            log.error("worker.non_job_task_failed", extra=fields)
        return
    fields["job_id"] = str(job_id)
    # Lock by ID alone, then inspect identity. An in-flight claim must commit
    # before deciding whether this event belongs to the winner.
    with get_session() as session:
        job = session.exec(for_update(select(Job).where(Job.id == job_id))).first()
        if job is None:
            log.info("worker.stop_for_discarded_job", extra=fields)
            return
        owned = job.worker_task_arn == detail.task_arn
        unclaimed = job.worker_task_arn is None and job.status in (
            JobStatus.PENDING,
            JobStatus.FAILED,
        )
        if not owned and not unclaimed:
            log.info("worker.stop_for_other_task", extra=fields)
            return
        if owned and job.worker_stop is None:
            job.worker_stop = WorkerStop(event=event)
            session.add(job)
        # Commit closure independently, before taking any order lock. Settlement
        # takes order -> job; holding job -> order here would permit a deadlock.
        session.commit()

    reason = detail.stopped_reason or worker.reason or detail.stop_code
    if reason is None:
        reason = "AWS did not include a stop reason"
    error = f"The worker stopped unexpectedly ({reason}; exit code {worker.exit_code})."
    with get_session() as session:
        job = session.exec(for_update(select(Job).where(Job.id == job_id))).first()
        if job is None:
            return
        owned = job.worker_task_arn == detail.task_arn
        unclaimed = job.worker_task_arn is None and job.status in (
            JobStatus.PENDING,
            JobStatus.FAILED,
        )
        if not owned and not unclaimed:
            return
        changed = (owned or unclaimed) and job.status in ACTIVE
        quote_failed = changed and job.kind == JobKind.QUOTE
        if changed:
            job.status, job.error, job.finished_at = JobStatus.FAILED, error, utc_now()
            session.add(job)
        session.commit()
        # Shopping chained from a build uses the same winning task.
        shops = fail_shops_of(session, job_id, detail.task_arn, error) if owned else 0
    if changed or shops or worker.exit_code != 0:
        log.error(
            "job.worker_stopped", extra=fields | {"marked_failed": changed, "shops_failed": shops}
        )
    else:
        log.info("worker.stop_recorded", extra=fields)
    # Also retried when an earlier delivery committed job failure but failed
    # before settling its order. The order lock makes this idempotent.
    _booking_stopped(job_id, "Booking stopped before it finished.")
    if quote_failed:
        notify_quote(job_id)


def _booking_stopped(job_id: UUID, error: str) -> None:
    with get_session() as session:
        order = booking.job_failed(session, job_id, error)
    if order is not None:
        notify_booking(order.id)


def _job_id(event: TaskStopped) -> UUID | None:
    for override in event.detail.overrides.container_overrides:
        if override.name == WORKER_CONTAINER:
            for variable in override.environment:
                if variable.name == JOB_ID_ENV:
                    try:
                        return UUID(variable.value)
                    except ValueError as error:
                        raise ValueError(
                            "Stopped worker task has an invalid job ID override."
                        ) from error
    return None
