"""The failure handler, through its `/events` route (as the Lambda Web Adapter
delivers AWS events). Event shapes follow AWS's SQS and ECS task state change
events, trimmed to the fields that matter."""

import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from bite_planner_agent.shopping import PreparedRecipes
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event
from sqlmodel import Session, select

from bite_planner.api import create_app
from bite_planner.db import get_engine
from bite_planner.jobs import claim_job
from bite_planner.models import Job, JobKind, JobStatus, ListSource, utc_now
from bite_planner.settings import get_settings

CLUSTER = "arn:aws:ecs:us-east-1:123456789012:cluster/bite-planner"
TASK = "arn:aws:ecs:us-east-1:123456789012:task/bite-planner/abc"
STOP_QUEUE = "arn:aws:sqs:us-east-1:123456789012:worker-stops"
JOBS_DLQ = "arn:aws:sqs:us-east-1:123456789012:jobs-dead-letters"


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("BITE_PLANNER_FAILURE_HANDLER", "true")
    monkeypatch.setenv("BITE_PLANNER_WORKER_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("BITE_PLANNER_WORKER_STOPS_QUEUE_ARN", STOP_QUEUE)
    monkeypatch.setenv("BITE_PLANNER_JOBS_DEAD_LETTERS_QUEUE_ARN", JOBS_DLQ)
    get_settings.cache_clear()
    yield TestClient(create_app())


def reload(session: Session, job: Job) -> Job:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one()


def task_event(
    job_id: str, exit_code: int | None, reason: str | None = None, task: str = TASK
) -> dict[str, Any]:
    container: dict[str, Any] = {"name": "worker", "lastStatus": "STOPPED"}
    if exit_code is not None:
        container["exitCode"] = exit_code
    detail: dict[str, Any] = {
        "clusterArn": CLUSTER,
        "taskArn": task,
        "stoppedAt": utc_now().isoformat(),
        "stopCode": "EssentialContainerExited",
        "stoppedReason": "Essential container in task exited",
        "version": 7,
        "lastStatus": "STOPPED",
        "containers": [container],
        "overrides": {
            "containerOverrides": [
                {
                    "name": "worker",
                    "environment": [{"name": "BITE_PLANNER_JOB_ID", "value": job_id}],
                }
            ]
        },
    }
    if reason:
        detail["stoppedReason"] = reason
    return {
        "id": str(uuid4()),
        "source": "aws.ecs",
        "account": "123456789012",
        "region": "us-east-1",
        "time": utc_now().isoformat(),
        "detail-type": "ECS Task State Change",
        "detail": detail,
    }


def queued(body: dict[str, Any], queue: str = STOP_QUEUE) -> dict[str, Any]:
    return {
        "Records": [{"eventSource": "aws:sqs", "eventSourceARN": queue, "body": json.dumps(body)}]
    }


def task_stopped(
    job_id: str, exit_code: int | None, reason: str | None = None, task: str = TASK
) -> dict[str, Any]:
    return queued(task_event(job_id, exit_code, reason, task))


def test_the_public_api_has_no_events_route() -> None:
    assert TestClient(create_app()).post("/events", json={}).status_code == 404


def test_crashed_worker_fails_its_job(
    events: TestClient, session: Session, job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    job.worker_task_arn = TASK
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


def test_worker_that_never_launched_fails_its_job(
    events: TestClient, session: Session, job: Job
) -> None:
    reason = "CannotPullContainerError: pull image manifest has been retried 1 time(s)"

    events.post("/events", json=task_stopped(str(job.id), exit_code=None, reason=reason))

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "CannotPullContainerError" in job.error


def test_worker_that_finished_the_job_itself_is_left_alone(
    events: TestClient, session: Session, job: Job, caplog: pytest.LogCaptureFixture
) -> None:
    job.worker_task_arn = TASK
    job.status = JobStatus.SUCCEEDED
    session.add(job)
    session.commit()

    with caplog.at_level(logging.ERROR):
        events.post(
            "/events", json=task_stopped(str(job.id), 0, "Essential container in task exited")
        )

    assert reload(session, job).status == JobStatus.SUCCEEDED
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_an_instacart_reconnect_stop_is_not_a_failed_purchase_job(
    events: TestClient,
    session: Session,
    job: Job,
    caplog: pytest.LogCaptureFixture,
    exit_code: int,
) -> None:
    stopped = task_event(str(job.id), exit_code)
    stopped["detail"]["overrides"] = {
        "containerOverrides": [{"name": "worker", "command": ["agent-login", "codex"]}]
    }
    with caplog.at_level(logging.INFO):
        assert events.post("/events", json=queued(stopped)).status_code == 200
    current = reload(session, job)
    assert current.status == JobStatus.PENDING and current.worker_stop is None
    expected = "worker.non_job_task_stopped" if exit_code == 0 else "worker.non_job_task_failed"
    assert expected in [record.getMessage() for record in caplog.records]
    assert bool([record for record in caplog.records if record.levelno >= logging.ERROR]) == bool(
        exit_code
    )


@pytest.mark.parametrize("container_status", [None, "PENDING"])
def test_task_stop_proof_does_not_require_optional_container_or_reason_diagnostics(
    events: TestClient, session: Session, job: Job, container_status: str | None
) -> None:
    job.worker_task_arn, job.status = TASK, JobStatus.SUCCEEDED
    session.add(job)
    session.commit()
    stopped = task_event(str(job.id), 0)
    stopped["detail"].pop("stopCode")
    stopped["detail"].pop("stoppedReason")
    container = stopped["detail"]["containers"][0]
    if container_status is None:
        container.pop("lastStatus")
    else:
        container["lastStatus"] = container_status
    assert events.post("/events", json=queued(stopped)).status_code == 200
    current = reload(session, job)
    assert current.worker_stop is not None
    assert current.worker_stop.event.detail.task_arn == TASK
    assert current.status == JobStatus.SUCCEEDED


def test_a_finished_job_is_never_overwritten(
    events: TestClient, session: Session, job: Job
) -> None:
    job.worker_task_arn = TASK
    job.status = JobStatus.SUCCEEDED
    session.add(job)
    session.commit()

    events.post("/events", json=task_stopped(str(job.id), 1))

    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_dead_lettered_request_fails_its_job(
    events: TestClient, session: Session, job: Job
) -> None:
    record = {
        "messageId": "1",
        "eventSource": "aws:sqs",
        "eventSourceARN": JOBS_DLQ,
        "body": json.dumps({"version": 1, "job_id": str(job.id)}),
    }

    assert events.post("/events", json={"Records": [record]}).status_code == 200

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "could not be started" in job.error


def test_unrecognized_or_malformed_events_fail_the_invocation(events: TestClient) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        events.post("/events", json={"something": "else"})
    with pytest.raises(ValueError, match="job ID"):
        events.post("/events", json=task_stopped("invalid", 1))
    with pytest.raises(ValueError, match="unrecognized queue"):
        events.post("/events", json=queued({}, "arn:unknown"))


def test_a_crashed_worker_fails_the_shopping_it_was_doing_for_its_list(
    events: TestClient, session: Session, job: Job
) -> None:
    # The build succeeded, and its task went on to shop its list, then crashed.
    build = Job(
        id=uuid4(),
        grocery_order_id=job.grocery_order_id,
        kind=JobKind.BUILD_LIST,
        requested_by=job.requested_by,
        status=JobStatus.SUCCEEDED,
        worker_task_arn=TASK,
        list_source=ListSource(picks=[]),
        prepared_recipes=PreparedRecipes(recipes=[]),
    )
    session.add(build)
    session.commit()
    shop = Job(
        id=uuid4(),
        grocery_order_id=job.grocery_order_id,
        kind=JobKind.SHOP,
        requested_by=job.requested_by,
        status=JobStatus.RUNNING,
        worker_task_arn=TASK,
        build_id=build.id,
    )
    session.add(shop)
    session.commit()

    events.post("/events", json=task_stopped(str(build.id), 137, "OutOfMemoryError"))

    assert reload(session, build).status == JobStatus.SUCCEEDED
    failed = reload(session, shop)
    assert failed.status == JobStatus.FAILED
    assert failed.error == "The worker stopped unexpectedly (OutOfMemoryError; exit code 137)."


@pytest.mark.parametrize("exit_code", [0, 137])
def test_only_the_winning_tasks_stop_can_record_evidence_or_fail_its_job(
    events: TestClient,
    session: Session,
    job: Job,
    exit_code: int,
) -> None:
    assert claim_job(session, job.id, TASK) is not None
    assert claim_job(session, job.id, TASK + "-duplicate") is None
    events.post("/events", json=task_stopped(str(job.id), exit_code, task=TASK + "-duplicate"))
    unchanged = reload(session, job)
    assert unchanged.status == JobStatus.RUNNING and unchanged.worker_stop is None
    message = task_stopped(str(job.id), exit_code)
    assert events.post("/events", json=message).status_code == 200
    stopped = reload(session, job)
    assert stopped.status == JobStatus.FAILED
    assert stopped.worker_stop is not None
    proof = stopped.worker_stop
    assert proof.event.detail.task_arn == TASK
    assert events.post("/events", json=message).status_code == 200
    assert reload(session, job).worker_stop == proof


@pytest.mark.parametrize(
    "field,value", [("source", "other"), ("account", "000000000000"), ("region", "us-west-2")]
)
def test_untrusted_stop_events_cannot_record_proof(
    events: TestClient,
    session: Session,
    job: Job,
    field: str,
    value: str,
) -> None:
    assert claim_job(session, job.id, TASK) is not None
    event = task_event(str(job.id), 0)
    event[field] = value
    with pytest.raises(ValueError):
        events.post("/events", json=queued(event))
    assert reload(session, job).worker_stop is None


def test_a_dead_lettered_duplicate_cannot_fail_a_running_worker(
    events: TestClient,
    session: Session,
    job: Job,
) -> None:
    assert claim_job(session, job.id, TASK) is not None
    events.post("/events", json=queued({"version": 1, "job_id": str(job.id)}, JOBS_DLQ))
    assert reload(session, job).status == JobStatus.RUNNING


def test_a_stop_event_waits_for_an_in_flight_claim_before_matching_identity(
    events: TestClient,
    session: Session,
    job: Job,
) -> None:
    job.status, job.worker_task_arn = JobStatus.RUNNING, TASK
    session.add(job)
    session.flush()  # Claim has its row lock, but has not committed its identity.
    selecting = Event()

    def observe(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if "FOR UPDATE" in statement and "FROM job" in statement:
            selecting.set()

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(events.post, "/events", json=task_stopped(str(job.id), 0))
        try:
            assert selecting.wait(5), "The event handler did not reach the job lock."
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        finally:
            session.commit()
            event.remove(engine, "before_cursor_execute", observe)
        assert future.result(timeout=5).status_code == 200
    proof = reload(session, job).worker_stop
    assert proof is not None and proof.event.detail.task_arn == TASK
