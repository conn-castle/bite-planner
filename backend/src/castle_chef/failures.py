"""The failure handler: marks jobs failed when their worker never started or
died, from AWS events, so nothing has to poll. Runs as its own Lambda function
(the API image, no public URL); the Lambda Web Adapter posts each event to
`/events`.

Error-level log lines here trip the CloudWatch alarm that emails the owner.
Expected failures (the agent's output was bad) exit 0 and are handled by the
worker itself, so they never reach this.
"""

import logging
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from castle_chef import booking
from castle_chef.db import get_session
from castle_chef.dispatch import JOB_ID_ENV, JobMessage
from castle_chef.jobs import fail_job, fail_shops_of
from castle_chef.notifications import notify_booking

log = logging.getLogger(__name__)

WORKER_CONTAINER = "worker"


def handle_event(event: dict[str, Any]) -> None:
    """Handle one Lambda event: an SQS batch from the jobs dead-letter queue, or
    an ECS task state change from EventBridge."""
    if event.get("detail-type") == "ECS Task State Change":
        _task_stopped(event["detail"])
    elif "Records" in event:
        for record in event["Records"]:
            _dead_lettered(record)
    else:
        log.error("failures.unknown_event", extra={"keys": sorted(event)})


def _dead_lettered(record: dict[str, Any]) -> None:
    """A job message that repeatedly failed to start a task."""
    try:
        message = JobMessage.model_validate_json(record["body"])
    except (KeyError, ValidationError) as error:
        log.error("failures.bad_dead_letter", extra={"error": str(error)})
        return
    error = "The request could not be started (no worker could be launched). Try again."
    with get_session() as session:
        changed = fail_job(session, message.job_id, error)
    log.error("job.never_started", extra={"job_id": str(message.job_id), "marked_failed": changed})
    _booking_stopped(message.job_id, "Booking couldn't be started.")


def _task_stopped(detail: dict[str, Any]) -> None:
    """A worker task stopped. Exit code 0 means the worker finished the job
    (succeeded or failed it) itself; anything else means it didn't."""
    if detail.get("lastStatus") != "STOPPED":
        return
    worker = next(
        (c for c in detail.get("containers", []) if c.get("name") == WORKER_CONTAINER), {}
    )
    exit_code = worker.get("exitCode")
    if exit_code == 0:
        return
    job_id = _job_id(detail)
    reason = detail.get("stoppedReason") or worker.get("reason") or "unknown"
    fields = {"job_id": str(job_id) if job_id else None, "exit_code": exit_code, "reason": reason}
    if job_id is None:
        log.error("failures.task_without_job", extra=fields | {"task": detail.get("taskArn")})
        return
    error = f"The worker stopped unexpectedly ({reason}; exit code {exit_code})."
    with get_session() as session:
        changed = fail_job(session, job_id, error)
        # A list's shopping runs in its build's task, right after it.
        shops = fail_shops_of(session, job_id, error)
    log.error(
        "job.worker_stopped", extra=fields | {"marked_failed": changed, "shops_failed": shops}
    )
    _booking_stopped(job_id, "Booking stopped before it finished.")


def _booking_stopped(job_id: UUID, error: str) -> None:
    """If the job was booking an order, settle it (not booked, or check Instacart
    once Place order was pressed) and say so."""
    with get_session() as session:
        order = booking.job_failed(session, job_id, error)
    if order is not None:
        notify_booking(order.id)


def _job_id(detail: dict[str, Any]) -> UUID | None:
    for override in detail.get("overrides", {}).get("containerOverrides", []):
        if override.get("name") != WORKER_CONTAINER:
            continue
        for variable in override.get("environment", []):
            if variable.get("name") == JOB_ID_ENV:
                try:
                    return UUID(variable["value"])
                except KeyError, TypeError, ValueError:
                    return None
    return None
