"""The failure handler, through its `/events` route (as the Lambda Web Adapter
delivers AWS events). Event shapes follow AWS's SQS and ECS task state change
events, trimmed to the fields that matter."""

import json
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from castle_chef.api import create_app
from castle_chef.jobs import GENERATE_MEALS, create_job
from castle_chef.models import Job, JobStatus
from castle_chef.settings import get_settings


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CASTLE_CHEF_FAILURE_HANDLER", "true")
    get_settings.cache_clear()
    yield TestClient(create_app())


def reload(session: Session, job: Job) -> Job:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one()


def task_stopped(job_id: str, exit_code: int | None, reason: str | None = None) -> dict:
    container = {"name": "worker", "lastStatus": "STOPPED"}
    if exit_code is not None:
        container["exitCode"] = exit_code
    detail = {
        "clusterArn": "arn:aws:ecs:us-east-1:123456789012:cluster/castle-chef",
        "taskArn": "arn:aws:ecs:us-east-1:123456789012:task/castle-chef/abc",
        "lastStatus": "STOPPED",
        "containers": [container],
        "overrides": {
            "containerOverrides": [
                {"name": "worker", "environment": [{"name": "CASTLE_CHEF_JOB_ID", "value": job_id}]}
            ]
        },
    }
    if reason:
        detail["stoppedReason"] = reason
    return {"source": "aws.ecs", "detail-type": "ECS Task State Change", "detail": detail}


def test_the_public_api_has_no_events_route() -> None:
    assert TestClient(create_app()).post("/events", json={}).status_code == 404


def test_crashed_worker_fails_its_job(
    events: TestClient, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    job.status = JobStatus.RUNNING
    session.add(job)
    session.commit()

    with caplog.at_level(logging.ERROR):
        response = events.post("/events", json=task_stopped(str(job.id), 137, "OutOfMemoryError"))

    assert response.status_code == 200
    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "The worker stopped unexpectedly (OutOfMemoryError; exit code 137)."
    assert "job.worker_stopped" in [r.getMessage() for r in caplog.records]


def test_worker_that_never_launched_fails_its_job(events: TestClient, session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    reason = "CannotPullContainerError: pull image manifest has been retried 1 time(s)"

    events.post("/events", json=task_stopped(str(job.id), exit_code=None, reason=reason))

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "CannotPullContainerError" in job.error


def test_worker_that_finished_the_job_itself_is_left_alone(
    events: TestClient, session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    job.status = JobStatus.SUCCEEDED
    session.add(job)
    session.commit()

    with caplog.at_level(logging.ERROR):
        events.post(
            "/events", json=task_stopped(str(job.id), 0, "Essential container in task exited")
        )

    assert reload(session, job).status == JobStatus.SUCCEEDED
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_finished_job_is_never_overwritten(events: TestClient, session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    job.status = JobStatus.SUCCEEDED
    session.add(job)
    session.commit()

    events.post("/events", json=task_stopped(str(job.id), 1))

    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_dead_lettered_request_fails_its_job(events: TestClient, session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    record = {
        "messageId": "1",
        "eventSource": "aws:sqs",
        "body": json.dumps({"version": 1, "job_id": str(job.id)}),
    }

    assert events.post("/events", json={"Records": [record]}).status_code == 200

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "could not be started" in job.error


def test_unrecognized_events_are_logged_loudly(
    events: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        events.post("/events", json={"something": "else"})
        events.post(
            "/events",
            json=task_stopped(str(uuid4()), 1)
            | {
                "detail": {
                    "lastStatus": "STOPPED",
                    "containers": [{"name": "worker", "exitCode": 1}],
                }
            },
        )

    messages = [r.getMessage() for r in caplog.records]
    assert "failures.unknown_event" in messages
    assert "failures.task_without_job" in messages
