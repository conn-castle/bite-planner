"""The API, through HTTP. A stand-in verifier maps bearer tokens to users (JWT
verification itself is tested in test_auth.py); jobs dispatch to a recorder, or
to a real local worker in the end-to-end test."""

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from castle_chef import api as api_module
from castle_chef.api import app, create_app
from castle_chef.auth import AuthError, AuthUnavailable, Principal
from castle_chef.db import get_engine
from castle_chef.dispatch import DispatchError, LocalDispatcher, SqsDispatcher, get_dispatcher
from castle_chef.jobs import (
    ACTIVE,
    GENERATE_MEALS,
    GenerateMealsParams,
    NotAMember,
    TooManyActiveJobs,
    create_job,
    submit_job,
)
from castle_chef.meals import IngredientInput, MealInput, add_meals
from castle_chef.models import Job, JobStatus, utc_now
from castle_chef.settings import get_settings, load_ssm_parameters
from tests.test_worker import FAKE_AGENT

STALE_AFTER = (timedelta(minutes=10), timedelta(minutes=20))
CASTLE = uuid4()  # the household of MOM and DAD
OTHER_HOUSE = uuid4()  # a separate household (e.g. a test household)
MOM = uuid4()  # an invited household member
DAD = uuid4()  # another member of the same household
NEIGHBOR = uuid4()  # a member of the other household
UNASSIGNED = uuid4()  # has a membership row but no household (an older release's invite)
STRANGER = uuid4()  # signed in, but never invited


class TokensAreUserIds:
    """Accepts `Bearer <user id>`; `Bearer expired` and `Bearer outage` simulate failures."""

    def verify(self, token: str) -> Principal:
        if token == "outage":
            raise AuthUnavailable("keys unreachable")
        try:
            return Principal(user_id=UUID(token))
        except ValueError as error:
            raise AuthError("bad token") from error


class RecordingDispatcher:
    def __init__(self, fail: bool = False) -> None:
        self.dispatched: list[UUID] = []
        self.fail = fail

    def dispatch(self, job_id: UUID) -> None:
        if self.fail:
            raise DispatchError("queue unavailable")
        self.dispatched.append(job_id)


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
def client(dispatcher: RecordingDispatcher) -> Iterator[TestClient]:
    app.dependency_overrides[api_module.get_verifier] = TokensAreUserIds
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def households(session: Session) -> None:
    for household_id, name in [(CASTLE, "Conn Castle"), (OTHER_HOUSE, "Test Household")]:
        session.execute(
            text("INSERT INTO household (id, name) VALUES (:id, :name)"),
            {"id": household_id, "name": name},
        )
    for user_id, email, name, household_id in [
        (MOM, "mom@example.com", "Mom", CASTLE),
        (DAD, "dad@example.com", "Dad", CASTLE),
        (NEIGHBOR, "neighbor@example.com", "Neighbor", OTHER_HOUSE),
        (UNASSIGNED, "unassigned@example.com", "Unassigned", None),
    ]:
        session.execute(
            text('INSERT INTO neon_auth."user" (id, email, name) VALUES (:id, :email, :name)'),
            {"id": user_id, "email": email, "name": name},
        )
        session.execute(
            text("INSERT INTO household_member (user_id, household_id) VALUES (:id, :household)"),
            {"id": user_id, "household": household_id},
        )
    session.commit()


def as_user(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def reload(session: Session, job_id: UUID) -> Job:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job_id)).one()


# Authentication and membership.


def test_health_needs_no_sign_in(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer expired"}, {"Authorization": "Basic x"}]
)
def test_requests_without_a_valid_token_are_rejected(client: TestClient, headers: dict) -> None:
    response = client.get("/v1/jobs", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_sign_in_outage_is_reported_as_unavailable(client: TestClient) -> None:
    assert client.get("/v1/jobs", headers={"Authorization": "Bearer outage"}).status_code == 503


def test_me_reports_membership_household_and_account(client: TestClient) -> None:
    assert client.get("/v1/me", headers=as_user(MOM)).json() == {
        "user_id": str(MOM),
        "email": "mom@example.com",
        "name": "Mom",
        "member": True,
        "household": {"id": str(CASTLE), "name": "Conn Castle"},
    }
    assert client.get("/v1/me", headers=as_user(NEIGHBOR)).json()["household"] == {
        "id": str(OTHER_HOUSE),
        "name": "Test Household",
    }


@pytest.mark.parametrize("user", [STRANGER, UNASSIGNED])
def test_me_reports_accounts_without_a_household_as_non_members(
    client: TestClient, user: UUID
) -> None:
    me = client.get("/v1/me", headers=as_user(user)).json()

    assert (me["user_id"], me["member"], me["household"]) == (str(user), False, None)


def test_each_request_is_logged_without_ids(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = uuid4()
    with caplog.at_level(logging.INFO, logger="castle_chef.api"):
        client.get(f"/v1/jobs/{job_id}/meals", headers=as_user(MOM))
        client.get("/v1/me")

    lines = [r.__dict__ for r in caplog.records if r.getMessage() == "http.request"]
    assert [(line["method"], line["route"], line["status"]) for line in lines] == [
        ("GET", "/v1/jobs/{job_id}/meals", 404),
        ("GET", "/v1/me", 401),
    ]
    assert all(isinstance(line["ms"], int) for line in lines)
    assert not [line for line in lines if str(job_id) in str(line)]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/jobs"),
        ("post", "/v1/jobs"),
        ("get", f"/v1/jobs/{uuid4()}/meals"),
        ("post", "/v1/logs"),
    ],
)
@pytest.mark.parametrize("user", [STRANGER, UNASSIGNED])
def test_non_members_can_do_nothing_else(
    client: TestClient, session: Session, method: str, path: str, user: UUID
) -> None:
    body = {"count": 2} if path == "/v1/jobs" else {"device": {}, "entries": []}
    kwargs = {"json": body} if method == "post" else {}

    response = getattr(client, method)(path, headers=as_user(user), **kwargs)

    assert response.status_code == 403
    assert response.json()["detail"] == "This account isn't in a household."
    assert session.exec(select(Job)).all() == []


# Requesting meals.


def test_member_requests_meals(
    client: TestClient, dispatcher: RecordingDispatcher, session: Session
) -> None:
    job_id = uuid4()

    response = client.post(
        "/v1/jobs", headers=as_user(MOM), json={"job_id": str(job_id), "count": 3}
    )

    assert response.status_code == 201
    assert response.json() | {"created_at": None} == {
        "id": str(job_id),
        "kind": "generate_meals",
        "params": {"count": 3},
        "status": "pending",
        "error": None,
        "created_at": None,
        "finished_at": None,
    }
    job = reload(session, job_id)
    assert (job.requested_by, job.household_id) == (MOM, CASTLE)
    assert dispatcher.dispatched == [job_id]
    listed = client.get("/v1/jobs", headers=as_user(DAD)).json()
    assert [job["id"] for job in listed] == [str(job_id)]


def test_retrying_a_request_returns_the_same_job(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    body = {"job_id": str(uuid4()), "count": 2}

    first = client.post("/v1/jobs", headers=as_user(MOM), json=body)
    retry = client.post("/v1/jobs", headers=as_user(MOM), json=body)

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["id"] == first.json()["id"]
    assert len(dispatcher.dispatched) == 1


def test_someone_elses_job_id_is_rejected(client: TestClient) -> None:
    body = {"job_id": str(uuid4()), "count": 2}
    client.post("/v1/jobs", headers=as_user(MOM), json=body)

    assert client.post("/v1/jobs", headers=as_user(DAD), json=body).status_code == 409
    assert client.post("/v1/jobs", headers=as_user(NEIGHBOR), json=body).status_code == 409


@pytest.mark.parametrize("count", [0, 15, 2.5, "3"])
def test_invalid_counts_are_rejected(client: TestClient, count: object) -> None:
    response = client.post("/v1/jobs", headers=as_user(MOM), json={"count": count})

    assert response.status_code == 422


def test_each_member_can_have_at_most_three_requests_waiting(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/v1/jobs", headers=as_user(MOM), json={"count": 1}).status_code == 201

    fourth = client.post("/v1/jobs", headers=as_user(MOM), json={"count": 1})

    assert fourth.status_code == 429
    assert "3 requests waiting" in fourth.json()["detail"]
    # The limit is per person.
    assert client.post("/v1/jobs", headers=as_user(DAD), json={"count": 1}).status_code == 201


def test_stale_requests_do_not_count_against_the_limit(
    client: TestClient, session: Session
) -> None:
    # Three requests whose tasks never started (or were lost) long ago.
    for status, started in [
        (JobStatus.PENDING, None),
        (JobStatus.PENDING, None),
        (JobStatus.RUNNING, utc_now() - timedelta(hours=2)),
    ]:
        job = create_job(session, GENERATE_MEALS, {"count": 1}, CASTLE, requested_by=MOM)
        job.status, job.started_at = status, started
        job.created_at = utc_now() - timedelta(hours=2)
        session.add(job)
        session.commit()

    response = client.post("/v1/jobs", headers=as_user(MOM), json={"count": 1})

    assert response.status_code == 201
    statuses = session.exec(select(Job.status).where(Job.requested_by == MOM)).all()
    assert sorted(statuses) == ["failed", "failed", "failed", "pending"]


def test_request_that_cannot_be_started_fails_visibly(client: TestClient, session: Session) -> None:
    app.dependency_overrides[get_dispatcher] = lambda: RecordingDispatcher(fail=True)
    job_id = uuid4()

    response = client.post(
        "/v1/jobs", headers=as_user(MOM), json={"job_id": str(job_id), "count": 2}
    )

    assert response.status_code == 503
    job = reload(session, job_id)
    assert job.status == JobStatus.FAILED
    assert job.error == "The request could not be started. Try again."


# Meals.


def test_meals_of_a_request_in_the_order_the_agent_added_them(
    client: TestClient, session: Session
) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 2}, CASTLE, requested_by=MOM)
    meal = MealInput(
        name="Tacos",
        servings=4,
        ingredients=[
            IngredientInput(name="tortillas", quantity=8),
            IngredientInput(name="beef", quantity=1, unit="lb"),
        ],
        steps=["Cook.", "Serve."],
    )
    add_meals(session, job.id, [meal, meal.model_copy(update={"name": "Soup"})])
    session.commit()

    meals = client.get(f"/v1/jobs/{job.id}/meals", headers=as_user(DAD)).json()

    assert [m["name"] for m in meals] == ["Tacos", "Soup"]
    assert [(i["position"], i["name"], i["unit"]) for i in meals[0]["meal_ingredient"]] == [
        (0, "tortillas", None),
        (1, "beef", "lb"),
    ]
    assert meals[0]["steps"] == ["Cook.", "Serve."]


def test_meals_of_an_unknown_request_are_not_found(client: TestClient) -> None:
    assert client.get(f"/v1/jobs/{uuid4()}/meals", headers=as_user(MOM)).status_code == 404


# Households are separate.


def add_tacos_and_soup(session: Session, job: Job) -> None:
    meal = MealInput(
        name="Tacos", servings=4, ingredients=[IngredientInput(name="beef")], steps=["Cook."]
    )
    add_meals(session, job.id, [meal, meal.model_copy(update={"name": "Soup"})])
    session.commit()


def test_members_see_only_their_households_requests(client: TestClient) -> None:
    moms = client.post("/v1/jobs", headers=as_user(MOM), json={"count": 1}).json()["id"]
    neighbors = client.post("/v1/jobs", headers=as_user(NEIGHBOR), json={"count": 1}).json()["id"]

    assert [job["id"] for job in client.get("/v1/jobs", headers=as_user(DAD)).json()] == [moms]
    assert [job["id"] for job in client.get("/v1/jobs", headers=as_user(NEIGHBOR)).json()] == [
        neighbors
    ]


def test_another_households_meals_are_not_found(client: TestClient, session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 2}, OTHER_HOUSE, requested_by=NEIGHBOR)
    add_tacos_and_soup(session, job)

    response = client.get(f"/v1/jobs/{job.id}/meals", headers=as_user(MOM))

    assert (response.status_code, response.json()["detail"]) == (404, "No such request.")
    assert len(client.get(f"/v1/jobs/{job.id}/meals", headers=as_user(NEIGHBOR)).json()) == 2


def test_listed_requests_name_their_meals(client: TestClient, session: Session) -> None:
    done = create_job(session, GENERATE_MEALS, {"count": 2}, CASTLE, requested_by=MOM)
    add_tacos_and_soup(session, done)
    waiting = create_job(session, GENERATE_MEALS, {"count": 1}, CASTLE, requested_by=DAD)
    meal_ids = client.get(f"/v1/jobs/{done.id}/meals", headers=as_user(MOM)).json()

    listed = {job["id"]: job for job in client.get("/v1/jobs", headers=as_user(MOM)).json()}

    assert listed[str(done.id)]["meals"] == [
        {"id": meal["id"], "name": meal["name"]} for meal in meal_ids
    ]
    assert [meal["name"] for meal in listed[str(done.id)]["meals"]] == ["Tacos", "Soup"]
    assert listed[str(waiting.id)]["meals"] == []
    assert set(listed[str(waiting.id)]) == {
        "id",
        "kind",
        "params",
        "status",
        "error",
        "created_at",
        "finished_at",
        "meals",
    }


def test_the_50_newest_requests_are_listed_newest_first(
    client: TestClient, session: Session
) -> None:
    # Created 1 to 51 minutes ago, in random id order.
    rows = session.execute(
        text(
            "INSERT INTO job (kind, params, household_id, created_at) "
            "SELECT 'generate_meals', '{\"count\": 1}', :household, "
            "now() - make_interval(mins => n) FROM generate_series(1, 51) AS n "
            "RETURNING id, created_at"
        ),
        {"household": CASTLE},
    ).all()
    session.commit()
    newest_first = [str(row.id) for row in sorted(rows, key=lambda row: row.created_at)][::-1]

    listed = client.get("/v1/jobs", headers=as_user(MOM)).json()

    assert [job["id"] for job in listed] == newest_first[:50]


# App logs.


def test_app_logs_are_written_as_structured_log_lines(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    entries = [
        {"event": "app.start", "level": "info"},
        {"event": "jobs.load_failed", "error": "boom"},
    ]

    with caplog.at_level(logging.INFO, logger="castle_chef.client"):
        response = client.post(
            "/v1/logs",
            headers=as_user(MOM),
            json={"device": {"platform": "ios"}, "entries": entries},
        )

    assert response.status_code == 204
    batch, *lines = [r for r in caplog.records if r.name == "castle_chef.client"]
    assert (batch.getMessage(), batch.email, batch.device, batch.entries) == (
        "client.batch",
        "mom@example.com",
        {"platform": "ios"},
        2,
    )
    assert [line.entry for line in lines] == entries
    assert {line.batch_id for line in lines} == {batch.batch_id}


def test_only_app_errors_are_logged_at_error_level(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Error-level lines email an alarm (infra/), so only the app's errors may be one.
    entries = [
        {"event": "render.crashed", "level": "error"},
        {"event": "query.failed", "level": "warn"},
        {"event": "app.start", "level": "info"},
        {"event": "no.level"},
    ]

    with caplog.at_level(logging.INFO, logger="castle_chef.client"):
        client.post("/v1/logs", headers=as_user(MOM), json={"device": {}, "entries": entries})

    lines = [r for r in caplog.records if r.getMessage() == "client.log"]
    assert [(line.entry["event"], line.levelname) for line in lines] == [
        ("render.crashed", "ERROR"),
        ("query.failed", "WARNING"),
        ("app.start", "INFO"),
        ("no.level", "INFO"),
    ]


def test_too_many_log_entries_are_rejected(client: TestClient) -> None:
    body = {"device": {}, "entries": [{"event": "x"}] * 2001}

    assert client.post("/v1/logs", headers=as_user(MOM), json=body).status_code == 422


# Browser access.


def test_configured_origins_may_call_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASTLE_CHEF_CORS_ORIGINS", '["capacitor://localhost"]')
    get_settings.cache_clear()
    cors_client = TestClient(create_app())
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }

    allowed = cors_client.options(
        "/v1/jobs", headers={"Origin": "capacitor://localhost"} | preflight
    )
    other = cors_client.options("/v1/jobs", headers={"Origin": "https://evil.example"} | preflight)

    assert allowed.headers["access-control-allow-origin"] == "capacitor://localhost"
    assert "access-control-allow-origin" not in other.headers


# End to end in development: the API starts a real local worker.


def test_request_runs_to_completion_with_a_local_worker(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_JOB_DISPATCH", "local")
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {FAKE_AGENT} 2 end_turn 0 0")
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    app.dependency_overrides[api_module.get_verifier] = TokensAreUserIds
    try:
        client = TestClient(app)
        job_id = client.post("/v1/jobs", headers=as_user(MOM), json={"count": 2}).json()["id"]

        deadline = time.monotonic() + 30
        while (job := reload(session, UUID(job_id))).status in ACTIVE:
            assert time.monotonic() < deadline, "the local worker did not finish"
            time.sleep(0.2)

        assert job.status == JobStatus.SUCCEEDED, job.error
        meals = client.get(f"/v1/jobs/{job_id}/meals", headers=as_user(MOM)).json()
        assert len(meals) == 2
    finally:
        app.dependency_overrides.clear()


# Review follow-ups: stale scoping, derived status, idempotency, concurrency, limits.


def make_job(session: Session, member: UUID, status: JobStatus, hours_ago: float) -> Job:
    job = create_job(session, GENERATE_MEALS, {"count": 1}, CASTLE, requested_by=member)
    job.status = status
    job.created_at = utc_now() - timedelta(hours=hours_ago)
    job.started_at = job.created_at if status == JobStatus.RUNNING else None
    session.add(job)
    session.commit()
    return job


def test_submitting_fails_only_the_requesters_stale_jobs(
    client: TestClient, session: Session
) -> None:
    dads = make_job(session, DAD, JobStatus.RUNNING, hours_ago=2)

    assert client.post("/v1/jobs", headers=as_user(MOM), json={"count": 1}).status_code == 201

    assert reload(session, dads.id).status == JobStatus.RUNNING


def test_overdue_jobs_are_listed_as_failed_without_changing_them(
    client: TestClient, session: Session
) -> None:
    lost = make_job(session, MOM, JobStatus.PENDING, hours_ago=1)
    fresh = make_job(session, MOM, JobStatus.RUNNING, hours_ago=0)

    listed = {job["id"]: job for job in client.get("/v1/jobs", headers=as_user(MOM)).json()}

    assert listed[str(lost.id)]["status"] == "failed"
    assert "stopped without finishing" in listed[str(lost.id)]["error"]
    assert listed[str(fresh.id)]["status"] == "running"
    assert reload(session, lost.id).status == JobStatus.PENDING  # a read writes nothing


def test_reusing_a_job_id_for_a_different_request_is_rejected(client: TestClient) -> None:
    job_id = str(uuid4())
    client.post("/v1/jobs", headers=as_user(MOM), json={"job_id": job_id, "count": 2})

    response = client.post("/v1/jobs", headers=as_user(MOM), json={"job_id": job_id, "count": 5})

    assert response.status_code == 409


def test_concurrent_requests_cannot_exceed_the_limit(session: Session) -> None:
    results: list[str] = []

    def submit() -> None:
        with Session(get_engine()) as own:
            try:
                submit_job(own, MOM, CASTLE, uuid4(), GenerateMealsParams(count=1), 3, STALE_AFTER)
                results.append("created")
            except TooManyActiveJobs:
                results.append("refused")

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["created"] * 3 + ["refused"] * 3
    assert len(session.exec(select(Job).where(Job.requested_by == MOM)).all()) == 3


def test_a_job_id_from_a_members_previous_household_is_rejected(
    client: TestClient, session: Session
) -> None:
    body = {"job_id": str(uuid4()), "count": 2}
    client.post("/v1/jobs", headers=as_user(MOM), json=body)
    session.execute(
        text("UPDATE household_member SET household_id = :house WHERE user_id = :id"),
        {"house": OTHER_HOUSE, "id": MOM},
    )
    session.commit()

    assert client.post("/v1/jobs", headers=as_user(MOM), json=body).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM household_member WHERE user_id = :id",
        "UPDATE household_member SET household_id = NULL WHERE user_id = :id",
        f"UPDATE household_member SET household_id = '{OTHER_HOUSE}' WHERE user_id = :id",
    ],
    ids=["removed", "household cleared", "moved"],
)
def test_submitting_after_leaving_the_household_is_refused(session: Session, change: str) -> None:
    # The membership changes between the API's check and the submission's lock.
    session.execute(text(change), {"id": MOM})
    session.commit()

    with pytest.raises(NotAMember):
        submit_job(session, MOM, CASTLE, uuid4(), GenerateMealsParams(count=1), 3, STALE_AFTER)
    assert session.exec(select(Job)).all() == []


def test_oversized_log_batches_are_rejected(client: TestClient) -> None:
    body = {"device": {}, "entries": [{"event": "x", "blob": "y" * 600_000}] * 2}

    assert client.post("/v1/logs", headers=as_user(MOM), json=body).status_code == 422


def test_local_worker_that_cannot_start_fails_the_request(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("too many processes")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    app.dependency_overrides[api_module.get_verifier] = TokensAreUserIds
    app.dependency_overrides[get_dispatcher] = LocalDispatcher
    try:
        job_id = uuid4()
        response = TestClient(app).post(
            "/v1/jobs", headers=as_user(MOM), json={"job_id": str(job_id), "count": 1}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert reload(session, job_id).status == JobStatus.FAILED


def test_sqs_dispatcher_sends_the_job_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/castle-chef-jobs"
    dispatcher = SqsDispatcher(queue_url)
    job_id = uuid4()

    with Stubber(dispatcher._sqs) as sqs:
        sqs.add_response(
            "send_message",
            {"MessageId": "1"},
            {"QueueUrl": queue_url, "MessageBody": f'{{"version":1,"job_id":"{job_id}"}}'},
        )
        dispatcher.dispatch(job_id)

        sqs.add_client_error("send_message", "AWS.SimpleQueueService.NonExistentQueue")
        with pytest.raises(DispatchError):
            dispatcher.dispatch(job_id)


def test_secrets_are_loaded_from_ssm_where_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv(
        "CASTLE_CHEF_SSM_PARAMETERS",
        '{"CASTLE_CHEF_TEST_SECRET": "/castle-chef/test/secret", "DATABASE_URL": "/unused"}',
    )
    monkeypatch.delenv("CASTLE_CHEF_TEST_SECRET", raising=False)
    ssm = boto3.client("ssm")
    monkeypatch.setattr(boto3, "client", lambda service: ssm)

    with Stubber(ssm) as stub:
        # DATABASE_URL is already set, so only the missing variable is fetched.
        stub.add_response(
            "get_parameter",
            {"Parameter": {"Value": "s3cret"}},
            {"Name": "/castle-chef/test/secret", "WithDecryption": True},
        )
        load_ssm_parameters()

    assert os.environ["CASTLE_CHEF_TEST_SECRET"] == "s3cret"
    monkeypatch.delenv("CASTLE_CHEF_TEST_SECRET")
