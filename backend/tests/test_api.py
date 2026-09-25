"""The API, through HTTP. A stand-in verifier maps bearer tokens to users (JWT
verification itself is tested in test_auth.py); jobs dispatch to a recorder, or
to a real local worker in the end-to-end test. A batch "finishes" the way the
worker finishes it (test_worker.py runs the worker itself)."""

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text
from sqlmodel import Session, col, select

from castle_chef import api as api_module
from castle_chef import orders
from castle_chef.api import app, create_app
from castle_chef.auth import AuthError, AuthUnavailable, Principal
from castle_chef.db import get_engine
from castle_chef.dispatch import DispatchError, LocalDispatcher, SqsDispatcher, get_dispatcher
from castle_chef.jobs import claim_job, fail_job, finish_job
from castle_chef.meals import IngredientInput, MealInput, add_meals
from castle_chef.models import GroceryOrder, Job, JobStatus, Meal
from castle_chef.settings import get_settings, load_ssm_parameters
from tests.test_worker import FAKE_AGENT

CASTLE = uuid4()  # the household of MOM and DAD
OTHER_HOUSE = uuid4()  # a separate household
MOM = uuid4()  # an invited household member
DAD = uuid4()  # another member of the same household
NEIGHBOR = uuid4()  # a member of the other household
STRANGER = uuid4()  # signed in, but never invited

JOB_ID = str(uuid4())
ZONE = "America/New_York"
# Always on different dates: 25 hours apart.
FAR_EAST = "Pacific/Kiritimati"  # UTC+14
FAR_WEST = "Pacific/Pago_Pago"  # UTC-11
UNKNOWN_ZONE = "Mars/Olympus_Mons"


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


@pytest.fixture
def grocer(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """The orders sent to the grocery service (a stub that does nothing)."""
    placed: list[UUID] = []
    monkeypatch.setattr(orders, "place_with_grocer", lambda order: placed.append(order.id))
    return placed


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
    ]:
        session.execute(
            text('INSERT INTO neon_auth."user" (id, email, name) VALUES (:id, :email, :name)'),
            {"id": user_id, "email": email, "name": name},
        )
        session.execute(
            text("INSERT INTO household_member (user_id, household_id) VALUES (:id, :household)"),
            {"id": user_id, "household": household_id},
        )
    session.execute(
        text('INSERT INTO neon_auth."user" (id, email, name) VALUES (:id, :email, :name)'),
        {"id": STRANGER, "email": "stranger@example.com", "name": "Stranger"},
    )
    session.commit()


# Helpers.


def as_user(user_id: UUID, zone: str | None = ZONE) -> dict[str, str]:
    """The headers the app sends: the member's token and the phone's time zone."""
    headers = {"Authorization": f"Bearer {user_id}"}
    if zone is not None:
        headers["Time-Zone"] = zone
    return headers


def tomorrow(zone: str = ZONE) -> date:
    return datetime.now(ZoneInfo(zone)).date() + timedelta(days=1)


def detail(response: Response, status: int) -> str:
    """The response's error message, checking that the body is only `{detail: string}`."""
    assert response.status_code == status, response.text
    body = response.json()
    assert list(body) == ["detail"], body
    assert isinstance(body["detail"], str), body
    return body["detail"]


def ok(response: Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def open_draft(client: TestClient, user: UUID = MOM) -> dict[str, Any]:
    response = client.post("/v1/grocery-orders", headers=as_user(user))
    assert response.status_code in (200, 201), response.text
    return response.json()


def get_order(client: TestClient, order_id: str, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(user)))


def next_order(client: TestClient, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get("/v1/grocery-orders/next", headers=as_user(user)))


def placed_orders(
    client: TestClient, covering_from: date, user: UUID = MOM
) -> list[dict[str, Any]]:
    return ok(
        client.get(
            "/v1/grocery-orders",
            params={"covering_from": covering_from.isoformat()},
            headers=as_user(user),
        )
    )


def request_batch(
    client: TestClient, order_id: str, user: UUID = MOM, job_id: UUID | None = None
) -> Response:
    return client.post(
        f"/v1/grocery-orders/{order_id}/jobs",
        headers=as_user(user),
        json={"job_id": str(job_id or uuid4())},
    )


def recipe(name: str) -> MealInput:
    return MealInput(
        name=name,
        description=f"{name}, the quick way.",
        servings=4,
        active_time_minutes=15,
        total_time_minutes=30,
        ingredients=[
            IngredientInput(name="tortillas", quantity=8),
            IngredientInput(name="ground beef", quantity=1, unit="lb", preparation="browned"),
        ],
        steps=["Cook.", "Serve."],
    )


def finish_batch(job_id: str, names: list[str]) -> None:
    """What the worker does with a batch whose agent succeeds: claims it (if it
    hasn't yet), saves its meals, and marks it succeeded."""
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(job_id))
        add_meals(worker_session, UUID(job_id), [recipe(name) for name in names])
        assert finish_job(worker_session, UUID(job_id))


def with_suggestions(
    client: TestClient, user: UUID = MOM, names: tuple[str, ...] = ("Tacos", "Soup")
) -> tuple[str, list[str]]:
    """The user's household draft with a finished batch: (order id, its meal ids)."""
    order_id = open_draft(client, user)["id"]
    job_id = ok(request_batch(client, order_id, user), 201)["id"]
    finish_batch(job_id, list(names))
    [batch] = [job for job in get_order(client, order_id, user)["jobs"] if job["id"] == job_id]
    return order_id, [meal["id"] for meal in batch["meals"]]


def put_dates(
    client: TestClient,
    order_id: str,
    delivery: date | None,
    next_delivery: date | None,
    user: UUID = MOM,
    zone: str = ZONE,
) -> Response:
    return client.put(
        f"/v1/grocery-orders/{order_id}/dates",
        headers=as_user(user, zone),
        json={
            "delivery_date": delivery.isoformat() if delivery else None,
            "next_delivery_date": next_delivery.isoformat() if next_delivery else None,
        },
    )


def pick(client: TestClient, order_id: str, meal_id: str, user: UUID = MOM) -> Response:
    return client.put(f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=as_user(user))


def place(client: TestClient, order_id: str, user: UUID = MOM, zone: str = ZONE) -> Response:
    return client.post(f"/v1/grocery-orders/{order_id}/place", headers=as_user(user, zone))


def placed_order(
    client: TestClient, delivery: date, next_delivery: date, user: UUID = MOM
) -> dict[str, Any]:
    """A placed order of the user's household: one batch, one pick, its dates."""
    order_id, [meal_id, *_] = with_suggestions(client, user)
    ok(pick(client, order_id, meal_id, user))
    ok(put_dates(client, order_id, delivery, next_delivery, user))
    return ok(place(client, order_id, user))


def jobs_in_database(session: Session) -> list[Job]:
    session.expire_all()
    return list(session.exec(select(Job)).all())


def run_at_once(requests: list[Callable[[], Response]]) -> list[Response]:
    """Send the requests concurrently, released together."""
    barrier = threading.Barrier(len(requests))
    responses: list[Response | None] = [None] * len(requests)

    def send(index: int) -> None:
        barrier.wait()
        responses[index] = requests[index]()

    threads = [threading.Thread(target=send, args=(i,)) for i in range(len(requests))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(response is not None for response in responses)
    return [response for response in responses if response is not None]


# Authentication and membership.


def test_health_needs_no_sign_in(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "authorization", [None, "Bearer expired", "Basic x"], ids=["none", "invalid", "not bearer"]
)
def test_requests_without_a_valid_token_are_rejected(
    client: TestClient, authorization: str | None
) -> None:
    headers = {"Time-Zone": ZONE} | ({"Authorization": authorization} if authorization else {})

    response = client.post("/v1/grocery-orders", headers=headers)

    detail(response, 401)
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_sign_in_outage_is_reported_as_unavailable(client: TestClient) -> None:
    response = client.get(
        "/v1/grocery-orders/next", headers={"Authorization": "Bearer outage", "Time-Zone": ZONE}
    )

    detail(response, 503)


def test_me_reports_membership_household_and_account(client: TestClient) -> None:
    assert ok(client.get("/v1/me", headers=as_user(MOM))) == {
        "user_id": str(MOM),
        "email": "mom@example.com",
        "name": "Mom",
        "member": True,
        "household": {"id": str(CASTLE), "name": "Conn Castle"},
    }
    assert ok(client.get("/v1/me", headers=as_user(NEIGHBOR)))["household"] == {
        "id": str(OTHER_HOUSE),
        "name": "Test Household",
    }


def test_me_reports_accounts_without_a_household_as_non_members(client: TestClient) -> None:
    me = ok(client.get("/v1/me", headers=as_user(STRANGER)))

    assert (me["user_id"], me["email"], me["member"], me["household"]) == (
        str(STRANGER),
        "stranger@example.com",
        False,
        None,
    )


def test_each_request_is_logged_without_ids(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    order_id = uuid4()
    with caplog.at_level(logging.INFO, logger="castle_chef.api"):
        client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM))
        client.get("/v1/me")

    lines = [r.__dict__ for r in caplog.records if r.getMessage() == "http.request"]
    assert [(line["method"], line["route"], line["status"]) for line in lines] == [
        ("GET", "/v1/grocery-orders/{order_id}", 404),
        ("GET", "/v1/me", 401),
    ]
    assert all(isinstance(line["ms"], int) for line in lines)
    assert not [line for line in lines if str(order_id) in str(line)]


MEMBER_ENDPOINTS = [
    ("get", "/v1/grocery-orders/next", None),
    ("get", "/v1/grocery-orders?covering_from=2026-09-01", None),
    ("post", "/v1/grocery-orders", None),
    ("get", "/v1/grocery-orders/{order}", None),
    ("delete", "/v1/grocery-orders/{order}", None),
    ("post", "/v1/grocery-orders/{order}/jobs", {"job_id": str(uuid4())}),
    ("put", "/v1/grocery-orders/{order}/meals/{meal}", None),
    ("delete", "/v1/grocery-orders/{order}/meals/{meal}", None),
    (
        "put",
        "/v1/grocery-orders/{order}/dates",
        {"delivery_date": None, "next_delivery_date": None},
    ),
    ("post", "/v1/grocery-orders/{order}/place", None),
    ("get", "/v1/meals/{meal}", None),
    ("post", "/v1/logs", {"device": {}, "entries": []}),
]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    MEMBER_ENDPOINTS,
    ids=[f"{method} {path}" for method, path, _ in MEMBER_ENDPOINTS],
)
def test_non_members_can_do_nothing_else(
    client: TestClient,
    session: Session,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    url = path.format(order=uuid4(), meal=uuid4())

    response = client.request(method, url, headers=as_user(STRANGER), json=body)

    assert detail(response, 403) == "This account isn't in a household."
    assert session.exec(select(GroceryOrder)).all() == []


# The draft.


def test_a_household_starts_with_its_first_order_not_yet_started(client: TestClient) -> None:
    assert next_order(client) == {
        "number": 1,
        "target_date": None,
        "decide_by_date": None,
        "earliest_delivery_date": tomorrow().isoformat(),
        "draft": None,
    }
    assert placed_orders(client, date(2000, 1, 1)) == []


def test_opening_the_flow_creates_the_households_draft_once(client: TestClient) -> None:
    created = client.post("/v1/grocery-orders", headers=as_user(MOM))

    draft = ok(created, 201)
    assert draft | {"id": None, "created_at": None} == {
        "id": None,
        "number": 1,
        "status": "draft",
        "created_by": {"user_id": str(MOM), "name": "Mom"},
        "created_at": None,
        "target_date": None,
        "decide_by_date": None,
        "earliest_delivery_date": tomorrow().isoformat(),
        "delivery_date": None,
        "next_delivery_date": None,
        "jobs": [],
        "meal_ids": [],
    }
    # Reopening it, or another member opening the flow, resumes the same draft.
    assert ok(client.post("/v1/grocery-orders", headers=as_user(MOM))) == draft
    assert ok(client.post("/v1/grocery-orders", headers=as_user(DAD))) == draft
    assert next_order(client, DAD)["draft"] == draft
    assert get_order(client, draft["id"], DAD) == draft


def test_members_opening_the_flow_at_once_share_one_draft(
    client: TestClient, session: Session
) -> None:
    responses = run_at_once(
        [
            lambda user=user: client.post("/v1/grocery-orders", headers=as_user(user))
            for user in [MOM, DAD] * 4
        ]
    )

    assert sorted(response.status_code for response in responses) == [200] * 7 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(session.exec(select(GroceryOrder)).all()) == 1


def test_an_unknown_order_is_not_found(client: TestClient) -> None:
    detail(client.get(f"/v1/grocery-orders/{uuid4()}", headers=as_user(MOM)), 404)


def test_households_see_and_change_only_their_own_orders(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    placed_order(client, tomorrow(), tomorrow() + timedelta(days=3), DAD)
    order_id, [meal_id, _] = with_suggestions(client, MOM)
    ok(pick(client, order_id, meal_id))
    moms = get_order(client, order_id)
    as_neighbor = as_user(NEIGHBOR)

    assert next_order(client, NEIGHBOR)["draft"] is None
    assert placed_orders(client, date(2000, 1, 1), NEIGHBOR) == []
    detail(client.get(f"/v1/grocery-orders/{order_id}", headers=as_neighbor), 404)
    detail(request_batch(client, order_id, NEIGHBOR), 404)
    detail(pick(client, order_id, meal_id, NEIGHBOR), 404)
    detail(
        client.delete(f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=as_neighbor), 404
    )
    detail(put_dates(client, order_id, tomorrow(), None, NEIGHBOR), 404)
    detail(place(client, order_id, NEIGHBOR), 404)
    # Discarding someone else's order looks the same as discarding one that's gone.
    discard = client.delete(f"/v1/grocery-orders/{order_id}", headers=as_neighbor)
    gone = client.delete(f"/v1/grocery-orders/{uuid4()}", headers=as_neighbor)
    assert discard.status_code == gone.status_code

    assert get_order(client, order_id) == moms
    assert len(dispatcher.dispatched) == 2  # the placed order's batch, and this draft's
    # The neighbor's household gets its own draft: its first order.
    neighbors = open_draft(client, NEIGHBOR)
    assert (neighbors["id"] != order_id, neighbors["number"]) == (True, 1)


def test_writes_to_a_discarded_draft_do_not_reach_the_new_one(client: TestClient) -> None:
    # A stale screen still showing the discarded draft.
    old_id, [meal_id, _] = with_suggestions(client)
    assert client.delete(f"/v1/grocery-orders/{old_id}", headers=as_user(DAD)).status_code == 204
    new = open_draft(client)

    detail(pick(client, old_id, meal_id), 404)
    detail(put_dates(client, old_id, tomorrow(), None), 404)
    detail(request_batch(client, old_id), 404)
    detail(place(client, old_id), 404)
    assert get_order(client, new["id"]) == new


# The Time-Zone header.

NEEDS_TODAY = [
    ("get", "/v1/grocery-orders/next", None),
    ("post", "/v1/grocery-orders", None),
    ("get", "/v1/grocery-orders/{order}", None),
    ("put", "/v1/grocery-orders/{order}/meals/{meal}", None),
    ("delete", "/v1/grocery-orders/{order}/meals/{meal}", None),
    (
        "put",
        "/v1/grocery-orders/{order}/dates",
        {"delivery_date": None, "next_delivery_date": None},
    ),
    ("post", "/v1/grocery-orders/{order}/place", None),
]


@pytest.mark.parametrize("zone", [None, UNKNOWN_ZONE], ids=["missing", "unknown"])
@pytest.mark.parametrize(
    ("method", "path", "body"), NEEDS_TODAY, ids=[f"{m} {p}" for m, p, _ in NEEDS_TODAY]
)
def test_endpoints_that_need_today_require_a_known_time_zone(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    zone: str | None,
) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    before = get_order(client, order_id)
    url = path.format(order=order_id, meal=meal_id)

    response = client.request(method, url, headers=as_user(MOM, zone), json=body)

    detail(response, 400)
    assert get_order(client, order_id) == before


@pytest.mark.parametrize("zone", [None, UNKNOWN_ZONE], ids=["missing", "unknown"])
@pytest.mark.parametrize(
    "endpoint", ["me", "logs", "meal", "discard", "jobs"], ids=lambda endpoint: endpoint
)
def test_endpoints_that_do_not_need_today_ignore_the_time_zone(
    client: TestClient, endpoint: str, zone: str | None
) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    headers = as_user(MOM, zone)
    requests: dict[str, tuple[Callable[[], Response], int]] = {
        "me": (lambda: client.get("/v1/me", headers=headers), 200),
        "logs": (
            lambda: client.post("/v1/logs", headers=headers, json={"device": {}, "entries": []}),
            204,
        ),
        "meal": (lambda: client.get(f"/v1/meals/{meal_id}", headers=headers), 200),
        "discard": (lambda: client.delete(f"/v1/grocery-orders/{order_id}", headers=headers), 204),
        "jobs": (
            lambda: client.post(
                f"/v1/grocery-orders/{order_id}/jobs",
                headers=headers,
                json={"job_id": str(uuid4())},
            ),
            201,
        ),
    }
    send, expected = requests[endpoint]

    response = send()

    assert response.status_code == expected, response.text


def test_the_earliest_delivery_date_is_tomorrow_in_the_requests_time_zone(
    client: TestClient,
) -> None:
    order_id = open_draft(client)["id"]

    east = ok(client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM, FAR_EAST)))
    west = ok(client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM, FAR_WEST)))

    assert east["earliest_delivery_date"] == tomorrow(FAR_EAST).isoformat()
    assert west["earliest_delivery_date"] == tomorrow(FAR_WEST).isoformat()
    assert east["earliest_delivery_date"] != west["earliest_delivery_date"]


# Dates.


def test_both_dates_are_set_each_time(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    first = tomorrow()

    set_both = ok(put_dates(client, order_id, first, first + timedelta(days=4)))
    only_delivery = ok(put_dates(client, order_id, first + timedelta(days=1), None, DAD))
    cleared = ok(put_dates(client, order_id, None, None))

    assert (set_both["delivery_date"], set_both["next_delivery_date"]) == (
        first.isoformat(),
        (first + timedelta(days=4)).isoformat(),
    )
    assert (only_delivery["delivery_date"], only_delivery["next_delivery_date"]) == (
        (first + timedelta(days=1)).isoformat(),
        None,
    )
    assert (cleared["delivery_date"], cleared["next_delivery_date"]) == (None, None)
    assert get_order(client, order_id) == cleared


def test_the_delivery_date_cannot_be_before_the_earliest_date(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    before = get_order(client, order_id)
    today = tomorrow() - timedelta(days=1)

    detail(put_dates(client, order_id, today, today + timedelta(days=3)), 422)
    detail(put_dates(client, order_id, today, None), 422)
    assert get_order(client, order_id) == before


def test_the_earliest_delivery_date_follows_the_requests_time_zone(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    # Tomorrow in the far west is still today (or earlier) in the far east.
    west_tomorrow = tomorrow(FAR_WEST)

    detail(put_dates(client, order_id, west_tomorrow, None, zone=FAR_EAST), 422)
    accepted = ok(put_dates(client, order_id, west_tomorrow, None, zone=FAR_WEST))

    assert accepted["delivery_date"] == west_tomorrow.isoformat()


@pytest.mark.parametrize("days_after", [0, -1], ids=["same day", "day before"])
def test_the_next_delivery_date_must_be_after_the_delivery_date(
    client: TestClient, days_after: int
) -> None:
    order_id = open_draft(client)["id"]
    delivery = tomorrow() + timedelta(days=2)

    detail(put_dates(client, order_id, delivery, delivery + timedelta(days=days_after)), 422)
    accepted = ok(put_dates(client, order_id, delivery, delivery + timedelta(days=1)))

    assert accepted["next_delivery_date"] == (delivery + timedelta(days=1)).isoformat()


# Suggestions: batches of meals.


def test_member_requests_a_batch_of_suggestions(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id = open_draft(client)["id"]
    job_id = uuid4()

    batch = ok(request_batch(client, order_id, MOM, job_id), 201)

    assert batch | {"created_at": None} == {
        "id": str(job_id),
        "kind": "generate_meals",
        "status": "pending",
        "error": None,
        "requested_by": {"user_id": str(MOM), "name": "Mom"},
        "created_at": None,
        "meals": [],
    }
    assert dispatcher.dispatched == [job_id]
    assert get_order(client, order_id, DAD)["jobs"] == [batch]


def test_retrying_a_batch_request_returns_the_same_batch(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id = open_draft(client)["id"]
    job_id = uuid4()

    first = request_batch(client, order_id, MOM, job_id)
    retry = request_batch(client, order_id, MOM, job_id)

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert dispatcher.dispatched == [job_id]
    assert len(get_order(client, order_id)["jobs"]) == 1


def test_another_households_job_id_is_rejected(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    job_id = uuid4()
    ok(request_batch(client, open_draft(client, MOM)["id"], MOM, job_id), 201)
    neighbors = open_draft(client, NEIGHBOR)["id"]

    detail(request_batch(client, neighbors, NEIGHBOR, job_id), 409)

    assert get_order(client, neighbors, NEIGHBOR)["jobs"] == []
    assert dispatcher.dispatched == [job_id]


def test_a_job_id_from_a_previous_order_is_rejected(client: TestClient) -> None:
    first = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    [old_batch] = first["jobs"]
    draft = open_draft(client)["id"]

    detail(request_batch(client, draft, MOM, UUID(old_batch["id"])), 409)

    assert get_order(client, draft)["jobs"] == []
    assert get_order(client, first["id"])["jobs"] == [old_batch]


def test_one_batch_at_a_time(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    first = ok(request_batch(client, order_id), 201)["id"]

    detail(request_batch(client, order_id), 429)  # pending
    detail(request_batch(client, order_id, DAD), 429)  # for anyone in the household
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(first))
    detail(request_batch(client, order_id), 429)  # running
    finish_batch(first, ["Tacos"])

    # "Generate more": another batch once the first is done.
    second = ok(request_batch(client, order_id, DAD), 201)
    assert [job["id"] for job in get_order(client, order_id)["jobs"]] == [first, second["id"]]


def test_a_failed_batch_does_not_block_the_next(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    failed = ok(request_batch(client, order_id), 201)["id"]
    with Session(get_engine()) as worker_session:
        fail_job(worker_session, UUID(failed), "The agent gave up.")

    retried = request_batch(client, order_id)

    assert retried.status_code == 201, retried.text
    batches = get_order(client, order_id)["jobs"]
    assert [(job["id"], job["status"], job["error"]) for job in batches] == [
        (failed, "failed", "The agent gave up."),
        (retried.json()["id"], "pending", None),
    ]


@pytest.mark.parametrize("lost_while", ["pending", "running"])
def test_an_overdue_batch_reads_as_failed_and_does_not_block_the_next(
    client: TestClient, session: Session, lost_while: str
) -> None:
    # Its worker never started, or was lost: well past the pending timeout, or
    # past the agent's timeout while running.
    order_id = open_draft(client)["id"]
    lost = ok(request_batch(client, order_id), 201)["id"]
    if lost_while == "running":
        with Session(get_engine()) as worker_session:
            claim_job(worker_session, UUID(lost))
    session.execute(
        text(
            "UPDATE job SET created_at = created_at - interval '3 hours', "
            "started_at = started_at - interval '3 hours' WHERE id = :id"
        ),
        {"id": lost},
    )
    session.commit()

    [shown] = get_order(client, order_id)["jobs"]
    assert (shown["status"], isinstance(shown["error"], str)) == ("failed", True)
    [stored] = jobs_in_database(session)
    assert stored.status == lost_while  # a read writes nothing

    next_batch = request_batch(client, order_id)

    assert next_batch.status_code == 201, next_batch.text
    statuses = {str(job.id): job.status for job in jobs_in_database(session)}
    assert statuses == {lost: JobStatus.FAILED, next_batch.json()["id"]: JobStatus.PENDING}


def test_a_batch_that_cannot_be_started_fails_visibly(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    app.dependency_overrides[get_dispatcher] = lambda: RecordingDispatcher(fail=True)
    job_id = uuid4()

    detail(request_batch(client, order_id, MOM, job_id), 503)

    [batch] = get_order(client, order_id)["jobs"]
    assert (batch["id"], batch["status"]) == (str(job_id), "failed")
    assert isinstance(batch["error"], str)
    app.dependency_overrides[get_dispatcher] = RecordingDispatcher
    assert request_batch(client, order_id).status_code == 201


def test_local_worker_that_cannot_start_fails_the_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = open_draft(client)["id"]

    def cannot_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("too many processes")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    app.dependency_overrides[get_dispatcher] = LocalDispatcher

    detail(request_batch(client, order_id), 503)

    [batch] = get_order(client, order_id)["jobs"]
    assert batch["status"] == "failed"


def test_concurrent_batch_requests_start_one_batch(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id = open_draft(client)["id"]

    responses = run_at_once(
        [lambda user=user: request_batch(client, order_id, user) for user in [MOM, DAD] * 3]
    )

    assert sorted(response.status_code for response in responses) == [201] + [429] * 5
    assert len(get_order(client, order_id)["jobs"]) == 1
    assert len(dispatcher.dispatched) == 1


def test_suggestions_are_the_batches_meals_oldest_batch_first(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    first = ok(request_batch(client, order_id), 201)["id"]
    finish_batch(first, ["Tacos", "Soup"])
    second = ok(request_batch(client, order_id, DAD), 201)["id"]
    finish_batch(second, ["Chili", "Stir Fry"])

    order = get_order(client, order_id)

    assert [(job["id"], job["status"]) for job in order["jobs"]] == [
        (first, "succeeded"),
        (second, "succeeded"),
    ]
    assert [{meal["name"] for meal in job["meals"]} for job in order["jobs"]] == [
        {"Tacos", "Soup"},
        {"Chili", "Stir Fry"},
    ]
    tacos = next(meal for meal in order["jobs"][0]["meals"] if meal["name"] == "Tacos")
    assert tacos | {"id": None} == {
        "id": None,
        "name": "Tacos",
        "description": "Tacos, the quick way.",
        "active_time_minutes": 15,
        "total_time_minutes": 30,
    }
    assert get_order(client, order_id, DAD)["jobs"] == order["jobs"]  # in a fixed order


# Picking.


def test_members_pick_and_unpick_the_drafts_suggestions(client: TestClient) -> None:
    order_id, [first, second] = with_suggestions(client)

    assert ok(pick(client, order_id, first))["meal_ids"] == [first]
    assert sorted(ok(pick(client, order_id, second, DAD))["meal_ids"]) == sorted([first, second])
    assert sorted(ok(pick(client, order_id, first))["meal_ids"]) == sorted([first, second])
    unpicked = client.delete(f"/v1/grocery-orders/{order_id}/meals/{first}", headers=as_user(DAD))

    assert ok(unpicked)["meal_ids"] == [second]
    assert get_order(client, order_id)["meal_ids"] == [second]


def test_only_the_drafts_own_suggestions_can_be_picked(client: TestClient) -> None:
    previous = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    previous_meal = previous["jobs"][0]["meals"][0]["id"]
    _, [neighbors_meal, _] = with_suggestions(client, NEIGHBOR)
    order_id, _ = with_suggestions(client)

    for meal_id in [str(uuid4()), neighbors_meal, previous_meal]:
        detail(pick(client, order_id, meal_id), 404)

    assert get_order(client, order_id)["meal_ids"] == []


# Placing.


def test_placing_needs_picks_and_both_dates(client: TestClient, grocer: list[UUID]) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    delivery = tomorrow()

    no_picks = detail(place(client, order_id), 409)
    ok(pick(client, order_id, meal_id))
    no_delivery = detail(place(client, order_id), 409)
    ok(put_dates(client, order_id, delivery, None))
    no_next_delivery = detail(place(client, order_id), 409)

    # Each says what's missing.
    assert "meal" in no_picks.lower()
    assert "delivery date" in no_delivery.lower()
    assert "next delivery date" in no_next_delivery.lower()
    assert len({no_picks, no_delivery, no_next_delivery}) == 3
    assert get_order(client, order_id)["status"] == "draft"
    assert grocer == []


def test_placing_rechecks_that_the_delivery_date_is_still_available(
    client: TestClient, grocer: list[UUID]
) -> None:
    # Valid when chosen (tomorrow in the far west), but before the earliest
    # delivery date by the time it's placed (in the far east, a day or more ahead).
    order_id, [meal_id, _] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    delivery = tomorrow(FAR_WEST)
    ok(put_dates(client, order_id, delivery, delivery + timedelta(days=3), zone=FAR_WEST))

    reason = detail(place(client, order_id, zone=FAR_EAST), 409)

    assert "delivery date" in reason.lower()
    assert get_order(client, order_id)["status"] == "draft"
    assert grocer == []


def test_a_member_places_the_draft(client: TestClient, grocer: list[UUID]) -> None:
    order_id, [_, meal_id] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    delivery = tomorrow()
    ok(put_dates(client, order_id, delivery, delivery + timedelta(days=2)))

    before = datetime.now(UTC)
    placed = ok(place(client, order_id, DAD))
    after = datetime.now(UTC)

    assert get_order(client, order_id) == placed
    assert (placed["status"], placed["meal_ids"]) == ("placed", [meal_id])
    assert (placed["delivery_date"], placed["next_delivery_date"]) == (
        delivery.isoformat(),
        (delivery + timedelta(days=2)).isoformat(),
    )
    assert grocer == [UUID(order_id)]
    # Mom started it; Dad placed it, just now.
    [listed] = placed_orders(client, delivery)
    assert (listed["id"], listed["placed_by"]) == (order_id, {"user_id": str(DAD), "name": "Dad"})
    assert before <= datetime.fromisoformat(listed["placed_at"]) <= after
    # The household has no draft until someone opens the flow again.
    assert next_order(client)["draft"] is None


def test_placing_a_placed_order_again_returns_it_unchanged(
    client: TestClient, grocer: list[UUID]
) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))

    [listed] = placed_orders(client, tomorrow())

    retry = ok(place(client, placed["id"], DAD))

    assert retry == placed
    assert grocer == [UUID(placed["id"])]  # sent to the grocery service once
    # Still placed by Mom, at the same time.
    assert placed_orders(client, tomorrow()) == [listed]


def test_a_running_batch_does_not_block_placing(client: TestClient) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=1)))
    ok(request_batch(client, order_id), 201)  # "generate more", still pending

    assert ok(place(client, order_id))["status"] == "placed"


def test_a_placed_order_cannot_be_changed(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    order_id = placed["id"]
    [picked] = placed["meal_ids"]
    unpicked = next(m["id"] for m in placed["jobs"][0]["meals"] if m["id"] != picked)

    detail(request_batch(client, order_id), 409)
    detail(pick(client, order_id, unpicked), 409)
    detail(
        client.delete(f"/v1/grocery-orders/{order_id}/meals/{picked}", headers=as_user(MOM)), 409
    )
    detail(put_dates(client, order_id, tomorrow() + timedelta(days=1), None), 409)
    detail(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(DAD)), 409)

    assert get_order(client, order_id) == placed
    assert len(dispatcher.dispatched) == 1


# Discarding.


def test_discarding_the_draft_deletes_it_and_its_batches_but_keeps_their_meals(
    client: TestClient, session: Session
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))

    discarded = client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(DAD))

    assert (discarded.status_code, discarded.content) == (204, b"")
    detail(client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM)), 404)
    assert next_order(client)["draft"] is None
    assert jobs_in_database(session) == []
    kept = session.exec(select(Meal).where(col(Meal.id).in_([UUID(tacos), UUID(soup)]))).all()
    assert sorted((meal.name, meal.job_id) for meal in kept) == [("Soup", None), ("Tacos", None)]
    # Discarding it again, or an order that never existed, is fine.
    assert client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM)).status_code == 204
    assert client.delete(f"/v1/grocery-orders/{uuid4()}", headers=as_user(MOM)).status_code == 204


# Order numbers and target dates.


def test_order_numbers_and_target_dates_follow_the_households_orders(client: TestClient) -> None:
    start = tomorrow()

    first = placed_order(client, start, start + timedelta(days=3))
    assert (first["number"], first["target_date"]) == (1, None)

    # A discarded draft isn't an order.
    discarded = open_draft(client)
    assert (discarded["number"], discarded["target_date"]) == (2, first["next_delivery_date"])
    client.delete(f"/v1/grocery-orders/{discarded['id']}", headers=as_user(MOM))

    second = placed_order(client, start + timedelta(days=3), start + timedelta(days=7), DAD)
    assert (second["number"], second["target_date"]) == (2, first["next_delivery_date"])

    third = open_draft(client)
    assert (third["number"], third["target_date"]) == (3, second["next_delivery_date"])
    # Each order keeps its own number and target date.
    again = get_order(client, first["id"])
    assert (again["number"], again["target_date"]) == (1, None)
    # Another household's orders are numbered on their own.
    neighbors = open_draft(client, NEIGHBOR)
    assert (neighbors["number"], neighbors["target_date"]) == (1, None)


def test_the_next_order_is_decided_by_its_target_less_the_households_offset(
    client: TestClient, session: Session
) -> None:
    first = placed_order(client, tomorrow(), tomorrow() + timedelta(days=4))
    target = date.fromisoformat(first["next_delivery_date"])

    # Before anyone starts it.
    assert next_order(client, DAD) == {
        "number": 2,
        "target_date": target.isoformat(),
        "decide_by_date": (target - timedelta(days=1)).isoformat(),
        "earliest_delivery_date": tomorrow().isoformat(),
        "draft": None,
    }

    # Once started, its draft agrees.
    draft = open_draft(client)
    shown = next_order(client, DAD)
    assert shown["draft"] == draft
    assert (shown["number"], shown["target_date"], shown["decide_by_date"]) == (
        draft["number"],
        draft["target_date"],
        draft["decide_by_date"],
    )

    # A household that decides three days ahead.
    session.execute(
        text("UPDATE household SET decide_by_offset_days = 3 WHERE id = :id"), {"id": CASTLE}
    )
    session.commit()
    decide_by = (target - timedelta(days=3)).isoformat()
    assert next_order(client)["decide_by_date"] == decide_by
    assert get_order(client, draft["id"])["decide_by_date"] == decide_by
    # The first order had nothing before it to decide by.
    assert get_order(client, first["id"])["decide_by_date"] is None


def test_placed_orders_are_listed_from_the_day_they_cover(client: TestClient) -> None:
    start = tomorrow()
    first = placed_order(client, start, start + timedelta(days=3))
    second = placed_order(client, start + timedelta(days=3), start + timedelta(days=5), DAD)
    open_draft(client)  # the next order: a draft, not listed
    placed_order(client, start, start + timedelta(days=2), NEIGHBOR)  # another household's

    listed = placed_orders(client, start)

    assert [order["id"] for order in listed] == [first["id"], second["id"]]
    [meal_id] = second["meal_ids"]
    [meal] = [m for m in second["jobs"][0]["meals"] if m["id"] == meal_id]
    assert listed[1] | {"placed_at": None} == {
        "id": second["id"],
        "number": 2,
        "placed_at": None,
        "placed_by": {"user_id": str(DAD), "name": "Dad"},
        "delivery_date": second["delivery_date"],
        "next_delivery_date": second["next_delivery_date"],
        "meals": [meal],
    }
    # An order covers up to the day before its next delivery.
    last_of_first = start + timedelta(days=2)
    assert [order["id"] for order in placed_orders(client, last_of_first)] == [
        first["id"],
        second["id"],
    ]
    assert [order["id"] for order in placed_orders(client, last_of_first + timedelta(days=1))] == [
        second["id"]
    ]
    assert placed_orders(client, start + timedelta(days=5)) == []


# Recipes.


def test_a_suggestion_opens_as_its_full_recipe(client: TestClient) -> None:
    _, [tacos] = with_suggestions(client, names=("Tacos",))

    meal = ok(client.get(f"/v1/meals/{tacos}", headers=as_user(DAD)))

    assert meal == {
        "id": tacos,
        "name": "Tacos",
        "description": "Tacos, the quick way.",
        "active_time_minutes": 15,
        "total_time_minutes": 30,
        "servings": 4,
        "steps": ["Cook.", "Serve."],
        "ingredients": [
            {"name": "tortillas", "quantity": 8, "unit": None, "preparation": None},
            {"name": "ground beef", "quantity": 1, "unit": "lb", "preparation": "browned"},
        ],
    }


def test_picks_of_placed_orders_stay_readable(client: TestClient) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    open_draft(client)  # the next order

    for meal_id in placed["meal_ids"]:
        assert ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))["id"] == meal_id


def test_meals_outside_the_households_orders_are_not_found(client: TestClient) -> None:
    _, [neighbors_meal, _] = with_suggestions(client, NEIGHBOR)
    discarded_id, [discarded_meal, _] = with_suggestions(client, MOM)
    client.delete(f"/v1/grocery-orders/{discarded_id}", headers=as_user(MOM))

    for meal_id in [neighbors_meal, discarded_meal, str(uuid4())]:
        detail(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)), 404)
    assert ok(client.get(f"/v1/meals/{neighbors_meal}", headers=as_user(NEIGHBOR)))


# Error bodies.

INVALID_REQUESTS = {
    "no job id": ("post", "/v1/grocery-orders/{order}/jobs", {}),
    "job id not a uuid": ("post", "/v1/grocery-orders/{order}/jobs", {"job_id": "x"}),
    "unknown field": ("post", "/v1/grocery-orders/{order}/jobs", {"job_id": JOB_ID, "count": 3}),
    "order id not a uuid": ("get", "/v1/grocery-orders/x", None),
    "meal id not a uuid": ("put", "/v1/grocery-orders/{order}/meals/x", None),
    "not a date": (
        "put",
        "/v1/grocery-orders/{order}/dates",
        {"delivery_date": "2026-13-01", "next_delivery_date": None},
    ),
    "a date missing": ("put", "/v1/grocery-orders/{order}/dates", {"delivery_date": None}),
    "no covering_from": ("get", "/v1/grocery-orders", None),
    "covering_from not a date": ("get", "/v1/grocery-orders?covering_from=soon", None),
    "too many log entries": (
        "post",
        "/v1/logs",
        {"device": {}, "entries": [{"event": "x"}] * 2001},
    ),
}


@pytest.mark.parametrize(
    ("method", "path", "body"), INVALID_REQUESTS.values(), ids=INVALID_REQUESTS.keys()
)
def test_invalid_requests_get_a_single_message(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    order_id = open_draft(client)["id"]

    response = client.request(method, path.format(order=order_id), headers=as_user(MOM), json=body)

    assert detail(response, 422)


# App logs.


def test_app_logs_are_written_as_structured_log_lines(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    entries = [
        {"event": "app.start", "level": "info"},
        {"event": "orders.load_failed", "error": "boom"},
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


def test_oversized_log_batches_are_rejected(client: TestClient) -> None:
    body = {"device": {}, "entries": [{"event": "x", "blob": "y" * 600_000}] * 2}

    detail(client.post("/v1/logs", headers=as_user(MOM), json=body), 422)


# Browser access.


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_configured_origins_may_call_the_api(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    monkeypatch.setenv("CASTLE_CHEF_CORS_ORIGINS", '["capacitor://localhost"]')
    get_settings.cache_clear()
    cors_client = TestClient(create_app())
    preflight = {
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "authorization, content-type, time-zone",
    }
    path = f"/v1/grocery-orders/{uuid4()}/dates"

    allowed = cors_client.options(path, headers={"Origin": "capacitor://localhost"} | preflight)
    other = cors_client.options(path, headers={"Origin": "https://evil.example"} | preflight)

    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["access-control-allow-origin"] == "capacitor://localhost"
    assert method in allowed.headers["access-control-allow-methods"]
    assert "time-zone" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-origin" not in other.headers


# End to end in development: the API starts a real local worker.


def test_an_order_runs_to_completion_with_a_local_worker(
    session: Session, monkeypatch: pytest.MonkeyPatch, grocer: list[UUID]
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_JOB_DISPATCH", "local")
    monkeypatch.setenv("CASTLE_CHEF_MEALS_PER_BATCH", "2")
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {FAKE_AGENT} 2 end_turn 0 0")
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    app.dependency_overrides[api_module.get_verifier] = TokensAreUserIds
    try:
        client = TestClient(app)
        order_id = open_draft(client)["id"]
        job_id = ok(request_batch(client, order_id), 201)["id"]

        deadline = time.monotonic() + 30
        while (batch := get_order(client, order_id)["jobs"][0])["status"] in ("pending", "running"):
            assert time.monotonic() < deadline, "the local worker did not finish"
            time.sleep(0.2)

        assert (batch["id"], batch["status"]) == (job_id, "succeeded"), batch["error"]
        assert len(batch["meals"]) == 2
        meal_id = batch["meals"][0]["id"]
        ok(pick(client, order_id, meal_id))
        ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=1)))
        placed = ok(place(client, order_id))
        assert (placed["status"], placed["meal_ids"]) == ("placed", [meal_id])
        assert ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))["servings"] == 4
    finally:
        app.dependency_overrides.clear()


# AWS.


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
