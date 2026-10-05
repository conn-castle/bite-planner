"""The API, through HTTP. A stand-in verifier maps bearer tokens to users (JWT
verification itself is tested in test_auth.py); jobs dispatch to a recorder, or
to a real local worker in the end-to-end test. A batch "finishes" the way the
worker finishes it (test_worker.py runs the worker itself)."""

import logging
import os
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

import pytest
from botocore.stub import Stubber
from castle_chef_agent.checkout import Checkout, QuoteLine
from castle_chef_agent.instacart import Product
from castle_chef_agent.meals import IngredientInput, MealInput, StepInput
from castle_chef_agent.products import ItemChoice, ProductCount
from castle_chef_agent.shopping import Aisle
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text
from sqlmodel import Session, col, select
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from castle_chef import api as api_module
from castle_chef import booking, household, orders, products
from castle_chef.api import app, create_app
from castle_chef.auth import AuthError, AuthUnavailable, Principal, WebhookRejected
from castle_chef.checkout import SIGNED_OUT_ERROR, Quote
from castle_chef.db import get_engine, get_request_session
from castle_chef.dispatch import DispatchError, LocalDispatcher, SqsDispatcher, get_dispatcher
from castle_chef.jobs import SUPERSEDED_ERROR, claim_job, create_shop, fail_job, finish_job
from castle_chef.meals import add_meals
from castle_chef.models import (
    BookState,
    GroceryOrder,
    Household,
    Job,
    JobStatus,
    Meal,
    OrderStep,
    ShoppingListItem,
)
from castle_chef.settings import get_settings, load_ssm_parameters
from castle_chef.shopping import ListEntry, add_items

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
    """The orders whose booking at the store started."""
    started: list[UUID] = []
    start = booking.start

    def recorded(session: Session, order: GroceryOrder) -> Job:
        started.append(order.id)
        return start(session, order)

    monkeypatch.setattr(booking, "start", recorded)
    return started


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
            text("INSERT INTO app_user (user_id, household_id) VALUES (:id, :household)"),
            {"id": user_id, "household": household_id},
        )
    session.execute(
        text('INSERT INTO neon_auth."user" (id, email, name) VALUES (:id, :email, :name)'),
        {"id": STRANGER, "email": "stranger@example.com", "name": "Stranger"},
    )
    session.commit()


# Helpers.


def phone(user_id: UUID) -> UUID:
    """The ordering session of the member's phone: its order flow, open."""
    return uuid5(NAMESPACE_OID, str(user_id))


def as_user(user_id: UUID, zone: str | None = ZONE, session: UUID | None = None) -> dict[str, str]:
    """The headers the app sends: the member's token, the phone's time zone, and
    the ordering session (by default, their phone's)."""
    headers = {
        "Authorization": f"Bearer {user_id}",
        "Order-Session": str(session or phone(user_id)),
    }
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


def no_content(response: Response) -> None:
    assert (response.status_code, response.content) == (204, b""), response.text


def open_draft(client: TestClient, user: UUID = MOM) -> dict[str, Any]:
    response = client.post("/v1/grocery-orders", headers=as_user(user))
    assert response.status_code in (200, 201), response.text
    return response.json()


def close_flow(client: TestClient, order_id: str, user: UUID = MOM) -> None:
    """The member closes the order flow on their phone, releasing the lock."""
    no_content(client.delete(f"/v1/grocery-orders/{order_id}/lock", headers=as_user(user)))


def get_order(client: TestClient, order_id: str, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(user)))


def set_servings(
    client: TestClient, order_id: str, meal_id: str, servings: Any, user: UUID = MOM
) -> Response:
    return client.put(
        f"/v1/grocery-orders/{order_id}/meals/{meal_id}/servings",
        headers=as_user(user),
        json={"servings": servings},
    )


def without_lock(order: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in order.items() if key != "lock"}


def servings_of(order: dict[str, Any]) -> dict[str, int]:
    """How many to make of each of an order's picks, by meal id."""
    return {pick["meal_id"]: pick["servings"] for pick in order["picks"]}


def picked(order: dict[str, Any]) -> list[str]:
    """The ids of an order's picked meals."""
    return [pick["meal_id"] for pick in order["picks"]]


def next_order(client: TestClient, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get("/v1/grocery-orders/next", headers=as_user(user)))


def placed_orders(
    client: TestClient, covering_from: date | None = None, user: UUID = MOM
) -> list[dict[str, Any]]:
    """The household's placed orders: those covering `covering_from` on, or all of them."""
    params = {"covering_from": covering_from.isoformat()} if covering_from else {}
    return ok(client.get("/v1/grocery-orders", params=params, headers=as_user(user)))


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
        steps=[
            StepInput(text="Brown the beef.", ingredients=["Ground beef"]),
            StepInput(text="Simmer until thick.", timer_minutes=10),
            StepInput(text="Fill the tortillas.", ingredients=["tortillas", "ground beef"]),
        ],
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
    by: str | None = "18:00",
) -> Response:
    return client.put(
        f"/v1/grocery-orders/{order_id}/dates",
        headers=as_user(user, zone),
        json={
            "delivery_date": delivery.isoformat() if delivery else None,
            "delivery_by": by,
            "next_delivery_date": next_delivery.isoformat() if next_delivery else None,
        },
    )


def pick(client: TestClient, order_id: str, meal_id: str, user: UUID = MOM) -> Response:
    return client.put(f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=as_user(user))


def rate(client: TestClient, meal_id: str, rating: str, user: UUID = MOM) -> Response:
    return client.put(f"/v1/meals/{meal_id}/rating", headers=as_user(user), json={"rating": rating})


def clear_rating(client: TestClient, meal_id: str, user: UUID = MOM) -> Response:
    return client.delete(f"/v1/meals/{meal_id}/rating", headers=as_user(user))


def get_meal(client: TestClient, meal_id: str, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(user)))


def request_list(
    client: TestClient, order_id: str, user: UUID = MOM, job_id: UUID | None = None
) -> Response:
    return client.post(
        f"/v1/grocery-orders/{order_id}/list",
        headers=as_user(user),
        json={"job_id": str(job_id or uuid4())},
    )


def finish_list(job_id: str, items: list[ListEntry] | None = None, shop: bool = True) -> None:
    """What the worker does with a list build whose agent succeeds, then (unless
    `shop` is False) shopping it."""
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(job_id))
        job = worker_session.get_one(Job, UUID(job_id))
        assert job.list_source is not None
        meals = list(range(1, len(job.list_source.picks) + 1))
        add_items(
            worker_session,
            UUID(job_id),
            items
            or [
                ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=meals),
                ListEntry(name="salt", aisle=Aisle.PANTRY, meals=meals, on_hand=True),
            ],
            job.list_source,
        )
        assert finish_job(worker_session, UUID(job_id))
    if shop:
        shop_list(job_id)


ONIONS = Product(
    product_id="17499438",
    name="Wegmans Onions, Yellow",
    size="2 lb",
    price_cents=229,
    price_per_lb_cents=None,
    estimated_lb=None,
    image_url="https://example.com/onions.jpg",
    bought_before=True,
)
SWEET = ONIONS.model_copy(
    update={
        "product_id": "3371870",
        "name": "Sweet Onions",
        "size": "per lb",
        "price_cents": 172,
        "price_per_lb_cents": 229,
        "estimated_lb": 0.75,
        "bought_before": False,
    }
)


def shop_list(build_id: str, missing: frozenset[str] = frozenset()) -> None:
    """What the worker does next: shops the build's items (onions for each, sweet
    onions as the alternative; items named in `missing`, not found)."""
    with Session(get_engine()) as worker_session:
        build = worker_session.get_one(Job, UUID(build_id))
        shop = create_shop(worker_session, build, uuid4(), build.requested_by)
        worker_session.commit()
        claim_job(worker_session, shop.id)
        items = products.items_to_buy(worker_session, build.id)
        choices = {
            number: ItemChoice(
                item=number,
                chosen=None if item.name in missing else ProductCount(product=ONIONS, count=1),
                alternatives=[ProductCount(product=SWEET, count=2)],
                skippable_reason="Only for garnish." if item.name in missing else None,
            )
            for number, item in enumerate(items, start=1)
        }
        products.save_choices(
            worker_session,
            {number: item.id for number, item in enumerate(items, start=1)},
            choices,
        )
        assert finish_job(worker_session, shop.id)


def ready_list(client: TestClient, order_id: str, user: UUID = MOM) -> dict[str, Any]:
    """The draft's shopping list, built for its picks now (as the list step waits for)."""
    job = ok(request_list(client, order_id, user), 201)
    finish_list(job["id"])
    return get_order(client, order_id, user)["shopping_list"]


CHECKOUT = Checkout(
    items_cents=2290,
    fees=[QuoteLine(label="Service fee", cents=249)],
    tip_cents=229,
    total_cents=2768,
    address="1 Main St Rochester, NY 14604",
    card="Visa ending 4242",
    replacements=["Replace with best match"],
)


def request_quote(
    client: TestClient, order_id: str, user: UUID = MOM, job_id: UUID | None = None
) -> Response:
    return client.post(
        f"/v1/grocery-orders/{order_id}/quote",
        headers=as_user(user),
        json={"job_id": str(job_id or uuid4())},
    )


def finish_quote(job_id: str, quote: Quote | None = None) -> None:
    """What the worker does with a quote job: checkout read (CHECKOUT by default)."""
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(job_id))
        job = worker_session.get_one(Job, UUID(job_id))
        job.quote = quote or Quote(checkout=CHECKOUT)
        worker_session.add(job)
        assert finish_job(worker_session, UUID(job_id))


def ready_quote(client: TestClient, order_id: str, user: UUID = MOM) -> str:
    """The draft's current quote, read (as the final review waits for); its id."""
    job = ok(request_quote(client, order_id, user), 201)
    finish_quote(job["id"])
    return str(job["id"])


def place(
    client: TestClient,
    order_id: str,
    user: UUID = MOM,
    zone: str = ZONE,
    dates: tuple[date, date] | None = None,
    with_list: bool = True,
) -> Response:
    """Place the order with these dates, or the ones it has (as the app sends
    the dates it shows; any valid ones for an order the user can't see), after
    its list is built for its picks (as the flow does), unless `with_list` is
    False or it has no picks to list."""
    shown = client.get(f"/v1/grocery-orders/{order_id}", headers=as_user(user, zone))
    quote_id = str(uuid4())  # none it could be: refused for another reason first
    if shown.status_code == 200 and shown.json()["status"] == "draft":
        order = shown.json()
        listed = order["shopping_list"]
        if with_list and order["picks"] and not (listed and listed["current"]):
            ready_list(client, order_id, user)
        # Its total, as the final review waits for (when it can have one).
        quoted = get_order(client, order_id, user)["quote"]
        if quoted and quoted["current"] and quoted["job"]["status"] == "succeeded":
            quote_id = quoted["job"]["id"]
        else:
            requested = request_quote(client, order_id, user)
            if requested.status_code == 201:
                finish_quote(requested.json()["id"])
                quote_id = requested.json()["id"]
    if dates is None and shown.status_code == 200:
        body = {key: shown.json()[key] for key in ("delivery_date", "next_delivery_date")}
    else:
        dates = dates or (tomorrow(), tomorrow() + timedelta(days=1))
        body = {"delivery_date": dates[0].isoformat(), "next_delivery_date": dates[1].isoformat()}
    body |= {"delivery_by": "18:00", "quote_id": quote_id}
    return client.post(
        f"/v1/grocery-orders/{order_id}/place", headers=as_user(user, zone), json=body
    )


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
        "settings": {"notification_time": "08:00:00", "time_zone": None, "reminders_on": True},
    }
    assert ok(client.get("/v1/me", headers=as_user(NEIGHBOR)))["household"] == {
        "id": str(OTHER_HOUSE),
        "name": "Test Household",
    }


def test_me_reports_accounts_without_a_household_as_non_members(client: TestClient) -> None:
    me = ok(client.get("/v1/me", headers=as_user(STRANGER)))

    assert (me["user_id"], me["email"], me["member"], me["household"], me["settings"]) == (
        str(STRANGER),
        "stranger@example.com",
        False,
        None,
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


MEMBER_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
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
    ("put", "/v1/meals/{meal}/rating", {"rating": "up"}),
    ("delete", "/v1/meals/{meal}/rating", None),
    ("post", "/v1/logs", {"device": {}, "entries": []}),
    ("get", "/v1/household", None),
    ("patch", "/v1/household", {"reminder_lead_days": 3}),
    ("patch", "/v1/me/settings", {"notification_time": "07:30"}),
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
        "reminders_start_date": None,
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
        "reminders_start_date": None,
        "earliest_delivery_date": tomorrow().isoformat(),
        "delivery_date": None,
        "delivery_by": None,
        # The household's delivery time (6:00 PM) until one is chosen.
        "best_window": {"start": "16:00:00", "end": "18:00:00"},
        "fallback_window": {"start": "17:00:00", "end": "19:00:00"},
        "next_delivery_date": None,
        "reached_step": "pick",
        "jobs": [],
        "picks": [],
        "lock": draft["lock"],
        "shopping_list": None,
        "quote": None,
        "book_state": None,
        "book_on": None,
        "booking": None,
        "book_issue": None,
    }
    # Opening it takes its lock for Mom's phone.
    assert draft["lock"] | {"since": None, "expires_at": None} == {
        "member": {"user_id": str(MOM), "name": "Mom"},
        "session": str(phone(MOM)),
        "since": None,
        "step": "pick",
        "expires_at": None,
    }
    # Reopening it, or another member opening the flow once she's closed it,
    # resumes the same draft.
    close_flow(client, draft["id"])
    reopened = ok(client.post("/v1/grocery-orders", headers=as_user(MOM)))
    close_flow(client, draft["id"])
    dads = ok(client.post("/v1/grocery-orders", headers=as_user(DAD)))
    assert without_lock(reopened) == without_lock(dads) == without_lock(draft)
    assert next_order(client, DAD)["draft"] == dads
    assert get_order(client, draft["id"], DAD) == dads


def test_members_opening_the_flow_at_once_share_one_draft(
    client: TestClient, session: Session
) -> None:
    responses = run_at_once(
        [
            partial(client.post, "/v1/grocery-orders", headers=as_user(user))
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
    # The placed order's batch, list, total, and booking (it's due: delivery is
    # tomorrow), and this draft's batch.
    assert len(dispatcher.dispatched) == 5
    # The neighbor's household gets its own draft: its first order.
    neighbors = open_draft(client, NEIGHBOR)
    assert (neighbors["id"] != order_id, neighbors["number"]) == (True, 1)


def test_writes_to_a_discarded_draft_do_not_reach_the_new_one(client: TestClient) -> None:
    # A stale screen still showing the discarded draft.
    old_id, [meal_id, _] = with_suggestions(client)
    close_flow(client, old_id)
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
    "endpoint",
    [
        "me",
        "logs",
        "meal",
        "rate",
        "clear rating",
        "discard",
        "jobs",
        "household",
        "household settings",
        "personal settings",
    ],
    ids=lambda endpoint: endpoint,
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
        "rate": (
            lambda: client.put(
                f"/v1/meals/{meal_id}/rating", headers=headers, json={"rating": "up"}
            ),
            204,
        ),
        "clear rating": (
            lambda: client.delete(f"/v1/meals/{meal_id}/rating", headers=headers),
            204,
        ),
        "discard": (lambda: client.delete(f"/v1/grocery-orders/{order_id}", headers=headers), 204),
        "household": (lambda: client.get("/v1/household", headers=headers), 200),
        "household settings": (
            lambda: client.patch("/v1/household", headers=headers, json={"default_servings": 2}),
            200,
        ),
        "personal settings": (
            lambda: client.patch(
                "/v1/me/settings", headers=headers, json={"notification_time": "07:30"}
            ),
            200,
        ),
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
    only_delivery = ok(put_dates(client, order_id, first + timedelta(days=1), None))
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
    [old_batch, old_list, old_shop, old_quote, old_booking] = first["jobs"]
    kinds = [job["kind"] for job in first["jobs"]]
    assert kinds == ["generate_meals", "build_list", "shop", "quote", "book"]
    draft = open_draft(client)["id"]

    detail(request_batch(client, draft, MOM, UUID(old_batch["id"])), 409)

    assert get_order(client, draft)["jobs"] == []
    assert get_order(client, first["id"])["jobs"] == [
        old_batch,
        old_list,
        old_shop,
        old_quote,
        old_booking,
    ]


def test_one_batch_at_a_time(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    first = ok(request_batch(client, order_id), 201)["id"]

    detail(request_batch(client, order_id), 429)  # pending
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(first))
    detail(request_batch(client, order_id), 429)  # running
    finish_batch(first, ["Tacos"])

    # "Generate more": another batch once the first is done.
    second = ok(request_batch(client, order_id), 201)
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

    responses = run_at_once([lambda: request_batch(client, order_id) for _ in range(6)])

    assert sorted(response.status_code for response in responses) == [201] + [429] * 5
    assert len(get_order(client, order_id)["jobs"]) == 1
    assert len(dispatcher.dispatched) == 1


def test_suggestions_are_the_batches_meals_oldest_batch_first(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    first = ok(request_batch(client, order_id), 201)["id"]
    finish_batch(first, ["Tacos", "Soup"])
    second = ok(request_batch(client, order_id), 201)["id"]
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
        "rating": None,
    }
    assert get_order(client, order_id, DAD)["jobs"] == order["jobs"]  # in a fixed order


# Picking.


def test_a_member_picks_and_unpicks_the_drafts_suggestions(client: TestClient) -> None:
    order_id, [first, second] = with_suggestions(client)

    assert picked(ok(pick(client, order_id, first))) == [first]
    assert sorted(picked(ok(pick(client, order_id, second)))) == sorted([first, second])
    assert sorted(picked(ok(pick(client, order_id, first)))) == sorted([first, second])
    unpicked = client.delete(f"/v1/grocery-orders/{order_id}/meals/{first}", headers=as_user(MOM))

    assert picked(ok(unpicked)) == [second]
    assert picked(get_order(client, order_id)) == [second]


def test_only_the_drafts_own_suggestions_can_be_picked(client: TestClient) -> None:
    previous = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    previous_meal = previous["jobs"][0]["meals"][0]["id"]
    _, [neighbors_meal, _] = with_suggestions(client, NEIGHBOR)
    order_id, _ = with_suggestions(client)

    for meal_id in [str(uuid4()), neighbors_meal, previous_meal]:
        detail(pick(client, order_id, meal_id), 404)

    assert picked(get_order(client, order_id)) == []


# Placing.


def test_placing_needs_picks_and_both_dates(client: TestClient, grocer: list[UUID]) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    dates = (tomorrow(), tomorrow() + timedelta(days=2))

    no_picks = detail(place(client, order_id, dates=dates), 409)
    ok(pick(client, order_id, meal_id))
    no_dates = client.post(
        f"/v1/grocery-orders/{order_id}/place",
        headers=as_user(MOM),
        json={"delivery_date": dates[0].isoformat()},
    )

    assert "meal" in no_picks.lower()
    detail(no_dates, 422)
    order = get_order(client, order_id)
    assert (order["status"], order["delivery_date"]) == ("draft", None)
    assert grocer == []


def test_placing_saves_the_dates_the_member_saw(client: TestClient, grocer: list[UUID]) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    ok(pick(client, order_id, meal_id))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=1)))
    shown = (tomorrow() + timedelta(days=1), tomorrow() + timedelta(days=4))

    placed = ok(place(client, order_id, dates=shown))

    assert (placed["delivery_date"], placed["next_delivery_date"]) == tuple(
        day.isoformat() for day in shown
    )
    # Dates that break a rule leave another draft as it was.
    other_id, [other_meal, _] = with_suggestions(client, NEIGHBOR)
    ok(pick(client, other_id, other_meal, NEIGHBOR))
    refused = place(client, other_id, NEIGHBOR, dates=(tomorrow(), tomorrow()))
    detail(refused, 409)
    other = get_order(client, other_id, NEIGHBOR)
    assert (other["status"], other["delivery_date"]) == ("draft", None)


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

    # Mom closes it; Dad opens it and places it.
    close_flow(client, order_id)
    open_draft(client, DAD)
    before = datetime.now(UTC)
    placed = ok(place(client, order_id, DAD))
    after = datetime.now(UTC)

    assert get_order(client, order_id) == placed
    assert (placed["status"], picked(placed), placed["lock"]) == ("placed", [meal_id], None)
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

    # A retry after a lost response, even with other dates, changes nothing.
    retry = ok(place(client, placed["id"], DAD, dates=(tomorrow(), tomorrow() + timedelta(days=9))))

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
    [picked_meal] = picked(placed)
    unpicked = next(m["id"] for m in placed["jobs"][0]["meals"] if m["id"] != picked_meal)

    detail(request_batch(client, order_id), 409)
    detail(pick(client, order_id, unpicked), 409)
    detail(
        client.delete(f"/v1/grocery-orders/{order_id}/meals/{picked_meal}", headers=as_user(MOM)),
        409,
    )
    detail(put_dates(client, order_id, tomorrow() + timedelta(days=1), None), 409)
    detail(set_servings(client, order_id, picked_meal, 6), 409)
    detail(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(DAD)), 409)

    assert get_order(client, order_id) == placed
    # Its batch, its list, its total, and its booking (delivery is tomorrow).
    assert len(dispatcher.dispatched) == 4


# Servings.


def test_a_pick_starts_at_the_households_default_servings_and_keeps_its_own(
    client: TestClient,
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(client.patch("/v1/household", headers=as_user(DAD), json={"default_servings": 6}))

    assert servings_of(ok(pick(client, order_id, tacos))) == {tacos: 6}
    changed = ok(set_servings(client, order_id, tacos, 2))
    # Picking it again, or a new default, leaves the servings it has.
    ok(client.patch("/v1/household", headers=as_user(MOM), json={"default_servings": 3}))
    ok(pick(client, order_id, tacos))
    ok(pick(client, order_id, soup))

    assert servings_of(changed) == {tacos: 2}
    assert servings_of(get_order(client, order_id)) == {tacos: 2, soup: 3}


def test_placed_orders_list_each_meals_servings(client: TestClient) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(pick(client, order_id, soup))
    ok(set_servings(client, order_id, soup, 8))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=2)))
    ok(place(client, order_id))

    [listed] = placed_orders(client)

    assert {meal["id"]: meal["servings"] for meal in listed["meals"]} == {tacos: 4, soup: 8}


def test_servings_are_set_only_on_a_pick(client: TestClient) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))

    # Not picked (e.g. just unpicked on another phone): refused, not picked by it.
    assert detail(set_servings(client, order_id, soup, 3), 404) == "That meal isn't picked."
    # Another household's draft is no one's here.
    detail(set_servings(client, order_id, tacos, 3, NEIGHBOR), 404)
    for servings in (0, 13, None, 2.5):
        detail(set_servings(client, order_id, tacos, servings), 422)

    assert servings_of(get_order(client, order_id)) == {tacos: 4}


# Discarding.


def test_discarding_the_draft_deletes_it_and_its_batches_but_keeps_their_meals(
    client: TestClient, session: Session
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    close_flow(client, order_id)

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
        # Two days (the default reminder lead) before that.
        "reminders_start_date": (target - timedelta(days=3)).isoformat(),
        "earliest_delivery_date": tomorrow().isoformat(),
        "draft": None,
    }

    # Once started, its draft agrees.
    draft = open_draft(client)
    shown = next_order(client, DAD)
    assert without_lock(shown["draft"]) == without_lock(draft)
    assert (
        shown["number"],
        shown["target_date"],
        shown["decide_by_date"],
        shown["reminders_start_date"],
    ) == (
        draft["number"],
        draft["target_date"],
        draft["decide_by_date"],
        draft["reminders_start_date"],
    )

    # A household that decides three days ahead.
    ok(client.patch("/v1/household", headers=as_user(DAD), json={"decide_by_offset_days": 3}))
    decide_by = (target - timedelta(days=3)).isoformat()
    assert next_order(client)["decide_by_date"] == decide_by
    assert get_order(client, draft["id"])["decide_by_date"] == decide_by
    # The first order had nothing before it to decide by.
    assert get_order(client, first["id"])["decide_by_date"] is None
    # Reminders start the household's lead before the decide-by date: moved by both.
    assert next_order(client)["reminders_start_date"] == (target - timedelta(days=5)).isoformat()
    ok(client.patch("/v1/household", headers=as_user(DAD), json={"reminder_lead_days": 0}))
    assert next_order(client)["reminders_start_date"] == decide_by


# Settings.


def household_settings(client: TestClient, user: UUID = MOM) -> dict[str, Any]:
    return ok(client.get("/v1/household", headers=as_user(user)))


def patch_household(client: TestClient, body: dict[str, Any], user: UUID = MOM) -> Response:
    return client.patch("/v1/household", headers=as_user(user), json=body)


def patch_personal(client: TestClient, body: dict[str, Any], user: UUID = MOM) -> Response:
    return client.patch("/v1/me/settings", headers=as_user(user), json=body)


def test_a_household_starts_with_the_default_settings_and_lists_its_members(
    client: TestClient, session: Session
) -> None:
    # Dad was invited first.
    session.execute(
        text("UPDATE app_user SET invited_at = now() - interval '1 day' WHERE user_id = :id"),
        {"id": DAD},
    )
    session.commit()

    assert household_settings(client) == {
        "id": str(CASTLE),
        "name": "Conn Castle",
        "decide_by_offset_days": 1,
        "reminder_lead_days": 2,
        "default_servings": 4,
        "delivery_time": "18:00:00",
        "tip_percent": 10,
        "tip_max_cents": 1500,
        "product_gone": "use_alternative",
        "members": [
            {"user_id": str(DAD), "name": "Dad", "email": "dad@example.com"},
            {"user_id": str(MOM), "name": "Mom", "email": "mom@example.com"},
        ],
        "invites": [],
    }
    assert [m["name"] for m in household_settings(client, NEIGHBOR)["members"]] == ["Neighbor"]


def test_household_settings_are_shared_and_only_those_sent_change(client: TestClient) -> None:
    changed = ok(patch_household(client, {"reminder_lead_days": 0}))
    # Dad changing another setting meanwhile doesn't undo Mom's.
    ok(patch_household(client, {"decide_by_offset_days": 7, "default_servings": 12}, DAD))

    seen_by_mom = household_settings(client)
    assert changed["reminder_lead_days"] == 0
    assert (
        seen_by_mom["decide_by_offset_days"],
        seen_by_mom["reminder_lead_days"],
        seen_by_mom["default_servings"],
    ) == (7, 0, 12)
    assert household_settings(client, DAD) == seen_by_mom
    # Another household's are its own.
    neighbors = household_settings(client, NEIGHBOR)
    assert (neighbors["decide_by_offset_days"], neighbors["reminder_lead_days"]) == (1, 2)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reminder_lead_days": None},
        {"decide_by_offset_days": 0},
        {"decide_by_offset_days": 8},
        {"reminder_lead_days": -1},
        {"reminder_lead_days": 8},
        {"default_servings": 0},
        {"default_servings": 13},
        {"tip_percent": -1},
        {"tip_percent": 31},
        {"tip_max_cents": -1},
        {"tip_max_cents": 10_001},
        {"delivery_time": "18:00:30"},
        {"delivery_time": "18:00-04:00"},
        {"product_gone": "skip_it"},
        {"id": "00000000-0000-0000-0000-000000000000"},
    ],
    ids=[
        "nothing",
        "null",
        "offset 0",
        "offset 8",
        "lead -1",
        "lead 8",
        "servings 0",
        "servings 13",
        "tip -1%",
        "tip 31%",
        "max tip -1",
        "max tip over $100",
        "time with seconds",
        "time with a zone",
        "unknown product-gone choice",
        "not a setting",
    ],
)
def test_household_settings_out_of_range_are_refused(
    client: TestClient, body: dict[str, Any]
) -> None:
    before = household_settings(client)

    detail(patch_household(client, body), 422)

    assert household_settings(client) == before


def test_the_delivery_time_tip_and_product_gone_choice_are_household_settings(
    client: TestClient,
) -> None:
    body = {
        "delivery_time": "15:30",
        "tip_percent": 15,
        "tip_max_cents": 2000,
        "product_gone": "ask",
    }

    changed = ok(patch_household(client, body, DAD))

    assert {key: changed[key] for key in body} == body | {"delivery_time": "15:30:00"}
    assert household_settings(client) == changed


def test_members_rename_their_household(client: TestClient) -> None:
    renamed = ok(patch_household(client, {"name": "  The Conns  "}, DAD))

    assert renamed["name"] == "The Conns"
    assert ok(client.get("/v1/me", headers=as_user(MOM)))["household"]["name"] == "The Conns"


def test_a_household_name_is_unique_and_not_empty(client: TestClient) -> None:
    taken = patch_household(client, {"name": "Test Household", "reminder_lead_days": 5})

    assert detail(taken, 409) == "Another household is called that."
    for name in ("", "   ", "x" * 61):
        detail(patch_household(client, {"name": name}), 422)
    # Nothing sent with a refused name was saved.
    settings = household_settings(client)
    assert (settings["name"], settings["reminder_lead_days"]) == ("Conn Castle", 2)


def test_members_set_their_own_notification_time_and_time_zone(client: TestClient) -> None:
    assert ok(patch_personal(client, {"notification_time": "06:45"})) == {
        "notification_time": "06:45:00",
        "time_zone": None,
        "reminders_on": True,
    }
    # The zone is set on its own (the app reports it), leaving the time.
    assert ok(patch_personal(client, {"time_zone": FAR_EAST})) == {
        "notification_time": "06:45:00",
        "time_zone": FAR_EAST,
        "reminders_on": True,
    }
    # Daily reminders switched off, leaving the rest.
    assert ok(patch_personal(client, {"reminders_on": False}))["reminders_on"] is False

    me = ok(client.get("/v1/me", headers=as_user(MOM)))
    dad = ok(client.get("/v1/me", headers=as_user(DAD)))
    assert me["settings"] == {
        "notification_time": "06:45:00",
        "time_zone": FAR_EAST,
        "reminders_on": False,
    }
    assert dad["settings"] == {
        "notification_time": "08:00:00",
        "time_zone": None,
        "reminders_on": True,
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"time_zone": None},
        {"time_zone": UNKNOWN_ZONE},
        {"time_zone": "../etc/passwd"},
        {"notification_time": "07:30:15"},
        {"notification_time": "07:30+02:00"},
        {"notification_time": "25:00"},
        {"reminders_on": None},
        {"reminders_on": "sometimes"},
    ],
    ids=[
        "nothing",
        "null",
        "unknown zone",
        "not a zone name",
        "seconds",
        "with a zone",
        "not a time",
        "reminders null",
        "reminders not a switch",
    ],
)
def test_personal_settings_that_are_not_valid_are_refused(
    client: TestClient, body: dict[str, Any]
) -> None:
    detail(patch_personal(client, body), 422)

    assert ok(client.get("/v1/me", headers=as_user(MOM)))["settings"] == {
        "notification_time": "08:00:00",
        "time_zone": None,
        "reminders_on": True,
    }


def test_placed_orders_are_listed_from_the_day_they_cover(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Midday in the store's time zone: today's booking run has been.
    noon = datetime.now(ZoneInfo("America/New_York")).replace(hour=12, minute=0)
    monkeypatch.setattr(booking, "store_now", lambda: noon)
    start = tomorrow()
    first = placed_order(client, start, start + timedelta(days=3))
    second = placed_order(client, start + timedelta(days=3), start + timedelta(days=5), DAD)
    open_draft(client)  # the next order: a draft, not listed
    placed_order(client, start, start + timedelta(days=2), NEIGHBOR)  # another household's

    listed = placed_orders(client, start)

    assert [order["id"] for order in listed] == [first["id"], second["id"]]
    [meal_id] = picked(second)
    [meal] = [m for m in second["jobs"][0]["meals"] if m["id"] == meal_id]
    assert listed[1] | {"placed_at": None} == {
        "id": second["id"],
        "number": 2,
        "placed_at": None,
        "placed_by": {"user_id": str(DAD), "name": "Dad"},
        "delivery_date": second["delivery_date"],
        "delivery_by": "18:00:00",
        "next_delivery_date": second["next_delivery_date"],
        "meals": [meal | {"servings": 4}],
        "shopping_list": second["shopping_list"]["items"],
        # Delivery is within four days: it's booking.
        "book_state": "booking",
        "book_on": (start + timedelta(days=3 - 4)).isoformat(),
        "booking": None,
        "book_issue": None,
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


def test_without_a_day_every_placed_order_is_listed(client: TestClient) -> None:
    start = tomorrow()
    first = placed_order(client, start, start + timedelta(days=3))
    second = placed_order(client, start + timedelta(days=3), start + timedelta(days=5), DAD)
    open_draft(client)  # the next order: a draft, not listed
    placed_order(client, start, start + timedelta(days=2), NEIGHBOR)  # another household's

    # Even those that cover none of the days from `covering_from` on.
    assert placed_orders(client, start + timedelta(days=5)) == []
    assert [order["id"] for order in placed_orders(client)] == [first["id"], second["id"]]


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
        "rating": None,
        "servings": 4,
        # Each step with its timer and the ingredients it uses (by position).
        "steps": [
            {"text": "Brown the beef.", "timer_minutes": None, "ingredients": [1]},
            {"text": "Simmer until thick.", "timer_minutes": 10, "ingredients": []},
            {"text": "Fill the tortillas.", "timer_minutes": None, "ingredients": [0, 1]},
        ],
        "ingredients": [
            {"name": "tortillas", "quantity": 8, "unit": None, "preparation": None},
            {"name": "ground beef", "quantity": 1, "unit": "lb", "preparation": "browned"},
        ],
    }


def test_picks_of_placed_orders_stay_readable(client: TestClient) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    open_draft(client)  # the next order

    for meal_id in picked(placed):
        assert ok(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)))["id"] == meal_id


def test_meals_outside_the_households_orders_are_not_found(client: TestClient) -> None:
    _, [neighbors_meal, _] = with_suggestions(client, NEIGHBOR)
    discarded_id, [discarded_meal, _] = with_suggestions(client, MOM)
    client.delete(f"/v1/grocery-orders/{discarded_id}", headers=as_user(MOM))

    for meal_id in [neighbors_meal, discarded_meal, str(uuid4())]:
        detail(client.get(f"/v1/meals/{meal_id}", headers=as_user(MOM)), 404)
    assert ok(client.get(f"/v1/meals/{neighbors_meal}", headers=as_user(NEIGHBOR)))


# Ratings.


def test_members_rate_the_households_meals(client: TestClient) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    [meal_id] = picked(placed)
    assert get_meal(client, meal_id)["rating"] is None

    no_content(rate(client, meal_id, "up"))
    assert get_meal(client, meal_id, DAD)["rating"] == "up"

    no_content(rate(client, meal_id, "favorite", DAD))
    assert get_meal(client, meal_id)["rating"] == "favorite"

    no_content(clear_rating(client, meal_id))
    assert get_meal(client, meal_id)["rating"] is None
    # Clearing it again changes nothing.
    no_content(clear_rating(client, meal_id, DAD))
    assert get_meal(client, meal_id)["rating"] is None


def test_meals_are_listed_with_their_ratings(client: TestClient) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    [placed_meal] = picked(placed)
    order_id, [liked, disliked] = with_suggestions(client, names=("Chili", "Liver"))
    no_content(rate(client, placed_meal, "down"))
    no_content(rate(client, liked, "favorite"))
    no_content(rate(client, disliked, "not_for_us", DAD))

    [listed] = placed_orders(client)
    assert [(meal["id"], meal["rating"]) for meal in listed["meals"]] == [(placed_meal, "down")]
    draft = next_order(client)["draft"]
    assert draft["id"] == order_id
    assert {meal["id"]: meal["rating"] for job in draft["jobs"] for meal in job["meals"]} == {
        liked: "favorite",
        disliked: "not_for_us",
    }
    assert get_meal(client, placed_meal)["rating"] == "down"
    # A batch requested again is returned with its meals' ratings too.
    [job] = draft["jobs"]
    retried = ok(request_batch(client, order_id, job_id=UUID(job["id"])))
    assert retried["meals"] == job["meals"]


def test_households_rate_only_their_own_meals(client: TestClient) -> None:
    _, [neighbors_meal, _] = with_suggestions(client, NEIGHBOR)
    discarded_id, [discarded_meal, _] = with_suggestions(client, MOM)
    client.delete(f"/v1/grocery-orders/{discarded_id}", headers=as_user(MOM))
    no_content(rate(client, neighbors_meal, "up", NEIGHBOR))

    for meal_id in [neighbors_meal, discarded_meal, str(uuid4())]:
        assert detail(rate(client, meal_id, "down"), 404) == "No such meal."
        assert detail(clear_rating(client, meal_id), 404) == "No such meal."

    assert get_meal(client, neighbors_meal, NEIGHBOR)["rating"] == "up"


def test_not_for_us_unpicks_the_meal_and_keeps_it_from_being_picked(client: TestClient) -> None:
    order_id, [first, second] = with_suggestions(client)
    ok(pick(client, order_id, first))
    ok(pick(client, order_id, second))

    no_content(rate(client, first, "not_for_us"))

    assert picked(get_order(client, order_id)) == [second]
    message = detail(pick(client, order_id, first), 409)
    assert message == "You marked this meal 'don't show meals like this'."
    assert picked(get_order(client, order_id)) == [second]
    # Undone, it can be picked again.
    no_content(clear_rating(client, first))
    assert sorted(picked(ok(pick(client, order_id, first)))) == sorted([first, second])


def test_other_ratings_leave_the_picks_alone(client: TestClient) -> None:
    order_id, [first, second] = with_suggestions(client)
    ok(pick(client, order_id, first))

    no_content(rate(client, first, "down"))
    no_content(rate(client, second, "down"))

    assert picked(get_order(client, order_id)) == [first]
    assert sorted(picked(ok(pick(client, order_id, second)))) == sorted([first, second])


def test_not_for_us_leaves_placed_orders_as_they_were(client: TestClient) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    [meal_id] = picked(placed)
    open_draft(client)  # the next order

    no_content(rate(client, meal_id, "not_for_us"))

    assert picked(get_order(client, placed["id"])) == [meal_id]
    [listed] = placed_orders(client)
    assert [meal["id"] for meal in listed["meals"]] == [meal_id]


def test_a_pick_and_a_not_for_us_mark_at_once_never_leave_the_meal_picked(
    client: TestClient,
) -> None:
    order_id, meal_ids = with_suggestions(client, names=("A", "B", "C", "D", "E"))

    for meal_id in meal_ids:
        picking, marked = run_at_once(
            [
                partial(pick, client, order_id, meal_id),
                partial(rate, client, meal_id, "not_for_us"),
            ]
        )
        assert picking.status_code in (200, 409), picking.text
        assert marked.status_code == 204, marked.text

    assert picked(get_order(client, order_id)) == []


# The order lock.


def lock_url(order_id: str) -> str:
    return f"/v1/grocery-orders/{order_id}/lock"


def take(client: TestClient, order_id: str, user: UUID, session: UUID | None = None) -> Response:
    """The flow opening a given order (a notification's) takes its lock."""
    return client.post(
        lock_url(order_id), headers=as_user(user, session=session), json={"step": "pick"}
    )


def renew(
    client: TestClient,
    order_id: str,
    user: UUID = MOM,
    step: str = "pick",
    session: UUID | None = None,
) -> Response:
    return client.put(
        lock_url(order_id), headers=as_user(user, session=session), json={"step": step}
    )


def expire_lock(session: Session, order_id: str) -> None:
    """Two minutes pass without a renewal (the phone went away)."""
    session.execute(
        text(
            "UPDATE grocery_order SET lock_renewed_at = now() - interval '2 minutes 1 second' "
            "WHERE id = :id"
        ),
        {"id": order_id},
    )
    session.commit()


def dads_changes(client: TestClient, order_id: str, meal_id: str) -> dict[str, Response]:
    """Every change Dad's phone could make to the draft."""
    dad = as_user(DAD)
    return {
        "pick": pick(client, order_id, meal_id, DAD),
        "unpick": client.delete(f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=dad),
        "servings": set_servings(client, order_id, meal_id, 2, DAD),
        "dates": put_dates(client, order_id, tomorrow(), None, DAD),
        "batch": request_batch(client, order_id, DAD),
        "not for us": rate(client, meal_id, "not_for_us", DAD),
        "place": place(
            client,
            order_id,
            DAD,
            dates=(tomorrow(), tomorrow() + timedelta(days=2)),
            with_list=False,
        ),
        "discard": client.delete(f"/v1/grocery-orders/{order_id}", headers=dad),
    }


def test_while_one_member_orders_the_others_see_who_and_can_change_nothing(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    before = get_order(client, order_id)

    # Dad opens the flow: the draft, showing Mom's lock, which he doesn't get.
    dads = ok(client.post("/v1/grocery-orders", headers=as_user(DAD)))
    refused = dads_changes(client, order_id, tacos)

    assert dads["lock"] | {"since": None, "expires_at": None} == {
        "member": {"user_id": str(MOM), "name": "Mom"},
        "session": None,  # Mom's session is hers
        "since": None,
        "step": "pick",
        "expires_at": None,
    }
    assert next_order(client, DAD)["draft"]["lock"] == dads["lock"]
    for change, response in refused.items():
        assert detail(response, 423) == "Someone else in your household is placing this order.", (
            change
        )
    detail(take(client, order_id, DAD), 423)
    detail(renew(client, order_id, DAD), 423)
    assert without_lock(get_order(client, order_id)) == without_lock(before)
    assert len(dispatcher.dispatched) == 1  # Mom's batch only


def test_renewing_keeps_the_lock_and_says_where_the_member_is(
    client: TestClient, session: Session
) -> None:
    opened = open_draft(client)
    order_id = opened["id"]

    renewed = ok(renew(client, order_id, step="delivery"))

    assert renewed["since"] == opened["lock"]["since"]
    assert renewed["expires_at"] > opened["lock"]["expires_at"]
    assert renewed["step"] == "delivery"
    assert get_order(client, order_id, DAD)["lock"]["step"] == "delivery"
    # Renewed after it lapsed, it's still hers while no one else has taken it.
    expire_lock(session, order_id)
    assert get_order(client, order_id, DAD)["lock"] is None
    assert ok(renew(client, order_id))["member"]["user_id"] == str(MOM)
    assert get_order(client, order_id, DAD)["lock"]["member"]["user_id"] == str(MOM)


def test_the_draft_keeps_the_furthest_step_its_flow_reached_for_whoever_opens_it_next(
    client: TestClient,
) -> None:
    order_id = open_draft(client)["id"]
    assert get_order(client, order_id)["reached_step"] == "pick"

    # Mom goes through the list to the delivery date, then back to the servings.
    for step in ("servings", "list", "delivery", "list", "servings"):
        ok(renew(client, order_id, step=step))
    close_flow(client, order_id)

    # Dad opens it where she got to; his flow going further moves it on.
    assert open_draft(client, DAD)["reached_step"] == "delivery"
    ok(renew(client, order_id, DAD, step="review"))
    assert get_order(client, order_id, DAD)["reached_step"] == "review"


def test_a_lock_two_minutes_old_goes_to_whoever_opens_the_order_next(
    client: TestClient, session: Session
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    expire_lock(session, order_id)

    dads = open_draft(client, DAD)
    ok(pick(client, order_id, soup, DAD))

    assert dads["lock"]["member"]["user_id"] == str(DAD)
    assert dads["lock"]["session"] == str(phone(DAD))
    # Mom's phone comes back: its changes and renewals are refused, and nothing it
    # sends takes the lock back.
    assert detail(pick(client, order_id, tacos), 423) == (
        "Someone else in your household is placing this order."
    )
    detail(renew(client, order_id), 423)
    close_flow(client, order_id)  # releases only her own
    assert get_order(client, order_id)["lock"]["member"]["user_id"] == str(DAD)
    assert picked(get_order(client, order_id)) == [soup]


def test_a_phone_that_slept_past_two_minutes_carries_on_if_no_one_took_over(
    client: TestClient, session: Session
) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    expire_lock(session, order_id)

    # A change doesn't renew the lock; the app's renewal does.
    assert picked(ok(pick(client, order_id, tacos))) == [tacos]
    assert get_order(client, order_id)["lock"] is None


def test_closing_the_flow_releases_the_lock_for_the_others(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)

    close_flow(client, order_id)

    assert get_order(client, order_id, DAD)["lock"] is None
    # A renewal or change sent before closing but arriving after it takes nothing.
    detail(renew(client, order_id), 423)
    assert detail(pick(client, order_id, tacos), 423) == (
        "This order isn't open here any more. Open it again."
    )
    assert get_order(client, order_id, DAD)["lock"] is None
    # Dad can open it now.
    assert ok(take(client, order_id, DAD))["member"]["user_id"] == str(DAD)
    ok(pick(client, order_id, tacos, DAD))


def test_opening_the_order_on_another_phone_moves_the_members_lock_there(
    client: TestClient,
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ipad = uuid4()

    moved = ok(client.post("/v1/grocery-orders", headers=as_user(MOM, session=ipad)))
    ok(
        client.put(
            f"/v1/grocery-orders/{order_id}/meals/{soup}", headers=as_user(MOM, session=ipad)
        )
    )

    assert moved["lock"]["session"] == str(ipad)
    assert detail(pick(client, order_id, tacos), 423) == (
        "You're placing this order on another phone."
    )
    detail(renew(client, order_id), 423)  # her phone's renewal doesn't take it back
    # Taking it (opening the order on the phone again) moves it back.
    assert ok(take(client, order_id, MOM))["session"] == str(phone(MOM))
    ok(pick(client, order_id, tacos))
    assert sorted(picked(get_order(client, order_id))) == sorted([tacos, soup])


def test_placing_releases_the_lock(client: TestClient, grocer: list[UUID]) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))

    assert placed["lock"] is None
    detail(renew(client, placed["id"]), 409)
    detail(take(client, placed["id"], DAD), 409)
    no_content(client.delete(lock_url(placed["id"]), headers=as_user(MOM)))
    # The next draft is Dad's to open.
    assert open_draft(client, DAD)["lock"]["member"]["user_id"] == str(DAD)


def test_the_lock_of_a_discarded_or_unknown_order_is_not_found(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    no_content(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM)))

    for missing in (order_id, str(uuid4())):
        detail(take(client, missing, MOM), 404)
        detail(renew(client, missing), 404)
        no_content(client.delete(lock_url(missing), headers=as_user(MOM)))
    # Another household's order is no one's here.
    neighbors = open_draft(client, NEIGHBOR)["id"]
    detail(take(client, neighbors, MOM), 404)


def test_discarding_waits_for_the_member_ordering(client: TestClient, session: Session) -> None:
    order_id = open_draft(client)["id"]

    detail(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(DAD)), 423)
    expire_lock(session, order_id)
    no_content(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(DAD)))
    assert next_order(client)["draft"] is None


def test_a_draft_another_member_opens_just_as_it_is_discarded_is_kept(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = open_draft(client)["id"]
    close_flow(client, order_id)
    discard = orders.discard

    def opened_then_discarded(session: Session, household_id: UUID, *args: Any) -> None:
        # Dad opens the order after this request read it, before it's locked.
        with Session(get_engine()) as other:
            dad = orders.Orderer(user_id=DAD, session=phone(DAD))
            orders.take_lock(other, household_id, UUID(order_id), dad, OrderStep.PICK)
        discard(session, household_id, *args)

    monkeypatch.setattr(orders, "discard", opened_then_discarded)

    detail(client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM)), 423)
    assert next_order(client)["draft"]["id"] == order_id


def test_not_for_us_outside_the_flow_leaves_a_pick_to_the_member_ordering(
    client: TestClient,
) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    no_session = {"Authorization": f"Bearer {MOM}", "Time-Zone": ZONE}

    refused = client.put(
        f"/v1/meals/{tacos}/rating", headers=no_session, json={"rating": "not_for_us"}
    )
    not_picked = client.put(
        f"/v1/meals/{soup}/rating", headers=no_session, json={"rating": "not_for_us"}
    )

    assert detail(refused, 423) == "It's picked for the next order: open the order to change it."
    no_content(not_picked)
    assert picked(get_order(client, order_id)) == [tacos]
    assert get_meal(client, tacos)["rating"] is None


def test_members_opening_the_flow_at_once_get_one_lock(client: TestClient) -> None:
    order_id = open_draft(client)["id"]
    close_flow(client, order_id)

    responses = run_at_once(
        [
            partial(client.post, "/v1/grocery-orders", headers=as_user(user))
            for user in [MOM, DAD] * 2
        ]
    )

    holders = {ok(response)["lock"]["member"]["user_id"] for response in responses}
    assert len(holders) == 1
    [holder] = holders
    sessions = [ok(response)["lock"]["session"] for response in responses]
    assert sessions.count(str(phone(UUID(holder)))) == 2 and sessions.count(None) == 2


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/grocery-orders", None),
        ("post", "/v1/grocery-orders/{order}/lock", {"step": "pick"}),
        ("put", "/v1/grocery-orders/{order}/lock", {"step": "pick"}),
        ("delete", "/v1/grocery-orders/{order}/lock", None),
        ("put", "/v1/grocery-orders/{order}/meals/{meal}", None),
        ("delete", "/v1/grocery-orders/{order}/meals/{meal}", None),
        ("put", "/v1/grocery-orders/{order}/meals/{meal}/servings", {"servings": 2}),
        (
            "put",
            "/v1/grocery-orders/{order}/dates",
            {"delivery_date": None, "next_delivery_date": None},
        ),
        ("post", "/v1/grocery-orders/{order}/jobs", {"job_id": JOB_ID}),
        ("post", "/v1/grocery-orders/{order}/list", {"job_id": JOB_ID}),
        # Any item: the session is checked first.
        ("patch", f"/v1/grocery-orders/{{order}}/list/items/{UUID(int=1)}", {"removed": True}),
        (
            "post",
            "/v1/grocery-orders/{order}/place",
            {"delivery_date": "2030-01-01", "next_delivery_date": "2030-01-02"},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_changes_from_the_flow_need_its_ordering_session(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    order_id, [meal_id, _] = with_suggestions(client)
    before = get_order(client, order_id)
    no_session = {"Authorization": f"Bearer {MOM}", "Time-Zone": ZONE}

    response = client.request(
        method, path.format(order=order_id, meal=meal_id), headers=no_session, json=body
    )

    assert detail(response, 400) == "Send the ordering session in the Order-Session header."
    assert get_order(client, order_id) == before


# The shopping list.


def change_item(
    client: TestClient, order_id: str, item_id: str, change: dict[str, Any], user: UUID = MOM
) -> Response:
    return client.patch(
        f"/v1/grocery-orders/{order_id}/list/items/{item_id}", headers=as_user(user), json=change
    )


def listed(order: dict[str, Any]) -> list[tuple[str, bool]]:
    """The list's items to buy, and whether each was taken off it."""
    return [(i["name"], i["removed"]) for i in order["shopping_list"]["items"] if not i["on_hand"]]


def test_leaving_servings_starts_one_list_for_the_picks(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    job_id = uuid4()

    started = ok(request_list(client, order_id, job_id=job_id), 201)
    retried = ok(request_list(client, order_id, job_id=job_id))
    again = ok(request_list(client, order_id))  # the same picks: that build, not another

    assert (started["id"], started["kind"], started["status"]) == (
        str(job_id),
        "build_list",
        "pending",
    )
    assert retried == again == started
    assert dispatcher.dispatched[1:] == [job_id]  # after the meal batch
    assert get_order(client, order_id)["shopping_list"] | {"job": None} == {
        "job": None,
        "current": False,
        "shop": None,
        "store": "Wegmans",
        "items": [],
    }
    # Its job isn't a batch of meal ideas.
    assert [job["kind"] for job in get_order(client, order_id)["jobs"]] == [
        "generate_meals",
        "build_list",
    ]


def test_a_built_list_is_its_items_by_aisle(client: TestClient) -> None:
    order_id, [tacos, soup] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(pick(client, order_id, soup))
    job = ok(request_list(client, order_id), 201)
    finish_list(
        job["id"],
        [
            ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1]),
            ListEntry(name="yellow onions", quantity=2, aisle=Aisle.PRODUCE, meals=[1, 2]),
            ListEntry(name="salt", aisle=Aisle.PANTRY, meals=[2], on_hand=True),
        ],
    )

    shown = get_order(client, order_id)["shopping_list"]

    assert (shown["current"], shown["job"]["status"]) == (True, "succeeded")
    source = sorted([tacos, soup])  # meals are numbered in the list's order
    assert [
        (item["name"], item["quantity"], item["aisle"], item["meal_ids"], item["on_hand"])
        for item in shown["items"]
    ] == [
        ("yellow onions", 2, "produce", source, False),
        ("tortillas", 8, "pantry", [source[0]], False),
        ("salt", None, "pantry", [source[1]], True),
    ]


def test_changing_the_picks_needs_a_new_list_before_placing(
    client: TestClient, grocer: list[UUID]
) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=2)))
    first = ready_list(client, order_id)
    [tortillas] = [item for item in first["items"] if not item["on_hand"]]

    ok(set_servings(client, order_id, tacos, 6))
    stale = get_order(client, order_id)

    # The old list still shows, but it isn't the one to place, or to change.
    assert (stale["shopping_list"]["current"], listed(stale)) == (False, [("tortillas", False)])
    assert "other meals or servings" in detail(place(client, order_id, with_list=False), 409)
    detail(change_item(client, order_id, tortillas["id"], {"removed": True}), 409)
    # A new list for the new servings replaces it.
    second = ok(request_list(client, order_id), 201)
    finish_list(
        second["id"],
        [ListEntry(name="corn tortillas", quantity=12, aisle=Aisle.PANTRY, meals=[1])],
    )
    assert listed(get_order(client, order_id)) == [("corn tortillas", False)]
    assert ok(place(client, order_id, with_list=False))["status"] == "placed"
    assert grocer == [UUID(order_id)]


def test_a_list_still_being_made_for_older_picks_gives_way(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    first = ok(request_list(client, order_id), 201)

    ok(set_servings(client, order_id, tacos, 2))
    second = ok(request_list(client, order_id), 201)

    jobs = {job["id"]: job for job in get_order(client, order_id)["jobs"]}
    assert (jobs[first["id"]]["status"], jobs[first["id"]]["error"]) == ("failed", SUPERSEDED_ERROR)
    assert jobs[second["id"]]["status"] == "pending"
    assert get_order(client, order_id)["shopping_list"]["job"]["id"] == second["id"]


def test_placing_needs_a_list(client: TestClient, grocer: list[UUID]) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=2)))

    assert (
        detail(place(client, order_id, with_list=False), 409)
        == "The shopping list isn't ready yet."
    )
    ok(request_list(client, order_id), 201)  # still being made
    assert (
        detail(place(client, order_id, with_list=False), 409)
        == "The shopping list isn't ready yet."
    )
    assert grocer == []


def test_a_list_needs_picks(client: TestClient) -> None:
    order_id, _ = with_suggestions(client)

    assert detail(request_list(client, order_id), 409) == "Pick at least one meal."


def test_members_take_items_off_the_list_and_change_how_much(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    items = ready_list(client, order_id)["items"]
    [tortillas] = [item for item in items if not item["on_hand"]]
    [salt] = [item for item in items if item["on_hand"]]

    removed = ok(change_item(client, order_id, tortillas["id"], {"removed": True}))
    restored = ok(
        change_item(client, order_id, tortillas["id"], {"removed": False, "quantity": 12})
    )

    assert listed(removed) == [("tortillas", True)]
    [changed] = [item for item in restored["shopping_list"]["items"] if not item["on_hand"]]
    assert (changed["removed"], changed["quantity"], changed["recipe_quantity"]) == (False, 12, 8)
    # Back to the amount worked out for the recipes.
    reset = ok(change_item(client, order_id, tortillas["id"], {"quantity": 8}))
    [back] = [item for item in reset["shopping_list"]["items"] if not item["on_hand"]]
    assert (back["quantity"], back["recipe_quantity"]) == (8, 8)
    ok(change_item(client, order_id, tortillas["id"], {"quantity": 12}))
    reset_session = Session(get_engine())
    [stored] = reset_session.exec(
        select(ShoppingListItem).where(ShoppingListItem.name == "tortillas")
    ).all()
    assert (stored.quantity, stored.changed_quantity) == (8, 12)
    ok(change_item(client, order_id, tortillas["id"], {"quantity": 8}))
    reset_session.expire_all()
    assert reset_session.get_one(ShoppingListItem, stored.id).changed_quantity is None
    reset_session.close()
    # On-hand items aren't bought, so there's nothing to change.
    detail(change_item(client, order_id, salt["id"], {"removed": True}), 404)
    detail(change_item(client, order_id, str(uuid4()), {"removed": True}), 404)
    for bad in ({}, {"quantity": 0}, {"quantity": None}, {"removed": None}):
        detail(change_item(client, order_id, tortillas["id"], bad), 422)
    # Only the member ordering can.
    detail(change_item(client, order_id, tortillas["id"], {"removed": True}, DAD), 423)


def test_an_item_the_recipes_give_no_amount_for_has_none_to_change(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    job = ok(request_list(client, order_id), 201)
    finish_list(
        job["id"],
        [
            ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1]),
            ListEntry(name="hot sauce", aisle=Aisle.PANTRY, meals=[1]),
        ],
    )
    [hot_sauce] = [
        i for i in get_order(client, order_id)["shopping_list"]["items"] if i["name"] == "hot sauce"
    ]

    problem = detail(change_item(client, order_id, hot_sauce["id"], {"quantity": 1}), 409)

    assert problem == "The recipes give no amount for this item, so there's none to change."
    # It can still come off the list.
    ok(change_item(client, order_id, hot_sauce["id"], {"removed": True}))


def test_a_placed_order_keeps_its_list(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=2)))
    job = ok(request_list(client, order_id), 201)
    finish_list(
        job["id"],
        [
            ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1]),
            ListEntry(name="cotija", quantity=4, unit="oz", aisle=Aisle.DAIRY_EGGS, meals=[1]),
        ],
    )
    items = get_order(client, order_id)["shopping_list"]["items"]
    [tortillas] = [item for item in items if item["name"] == "tortillas"]
    ok(change_item(client, order_id, tortillas["id"], {"removed": True}))
    ok(place(client, order_id))

    [placed] = placed_orders(client)

    assert [(item["name"], item["removed"]) for item in placed["shopping_list"]] == [
        ("cotija", False),
        ("tortillas", True),
    ]
    detail(change_item(client, order_id, tortillas["id"], {"removed": False}), 409)
    detail(request_list(client, order_id), 409)


def test_meal_ideas_and_the_list_are_made_side_by_side(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))

    ok(request_list(client, order_id), 201)
    ok(request_batch(client, order_id), 201)  # "generate more" while the list is made
    ok(set_servings(client, order_id, tacos, 2))
    ok(request_list(client, order_id), 201)  # and a new list while more ideas are made


# Shopping the list (stage 3).


def shopped_draft(
    client: TestClient, shop: bool = True, missing: frozenset[str] = frozenset()
) -> tuple[str, str]:
    """A draft with dates and a list built for its picks: (order id, build id),
    shopped unless `shop` is False (items named in `missing` not found)."""
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))
    ok(put_dates(client, order_id, tomorrow(), tomorrow() + timedelta(days=2)))
    build = ok(request_list(client, order_id), 201)
    finish_list(
        build["id"],
        [
            ListEntry(name="tortillas", quantity=8, aisle=Aisle.PANTRY, meals=[1]),
            ListEntry(name="cotija", quantity=4, unit="oz", aisle=Aisle.DAIRY_EGGS, meals=[1]),
            ListEntry(name="salt", aisle=Aisle.PANTRY, meals=[1], on_hand=True),
        ],
        shop=False,
    )
    if shop:
        shop_list(build["id"], missing)
    return order_id, build["id"]


def item_named(order: dict[str, Any], name: str) -> dict[str, Any]:
    [item] = [i for i in order["shopping_list"]["items"] if i["name"] == name]
    return item


def test_a_shopped_list_shows_each_items_product_and_alternatives(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)

    shopping_list = get_order(client, order_id)["shopping_list"]

    assert shopping_list["shop"]["kind"] == "shop"
    assert shopping_list["shop"]["status"] == "succeeded"
    tortillas = item_named(get_order(client, order_id), "tortillas")
    assert tortillas["product"] == {
        "product_id": "17499438",
        "name": "Wegmans Onions, Yellow",
        "size": "2 lb",
        "price_cents": 229,
        "price_per_lb_cents": None,
        "estimated_lb": None,
        "image_url": "https://example.com/onions.jpg",
        "bought_before": True,
        "count": 1,
    }
    assert [(a["name"], a["count"]) for a in tortillas["alternatives"]] == [("Sweet Onions", 2)]
    assert item_named(get_order(client, order_id), "salt")["product"] is None


def test_placing_waits_for_the_list_to_be_shopped(client: TestClient) -> None:
    order_id, build_id = shopped_draft(client, shop=False)

    assert detail(place(client, order_id), 409) == "The shopping list isn't shopped yet."
    shop_list(build_id)
    ok(place(client, order_id))


def test_placing_waits_for_a_decision_on_what_the_store_doesnt_have(client: TestClient) -> None:
    order_id, _ = shopped_draft(client, missing=frozenset({"cotija", "tortillas"}))
    order = get_order(client, order_id)
    cotija, tortillas = item_named(order, "cotija"), item_named(order, "tortillas")
    assert (cotija["not_found"], cotija["product"], cotija["skippable_reason"]) == (
        True,
        None,
        "Only for garnish.",
    )

    problem = detail(place(client, order_id), 409)

    assert problem == "Decide on what Wegmans doesn't have: cotija, tortillas."  # by aisle
    ok(change_item(client, order_id, cotija["id"], {"removed": True}))  # skip it
    swapped = ok(change_item(client, order_id, tortillas["id"], {"alternative": "3371870"}))
    # Found, then: the store has what the member picked instead.
    tortillas = item_named(swapped, "tortillas")
    assert (tortillas["not_found"], tortillas["skippable_reason"]) == (False, None)
    ok(place(client, order_id))


def test_members_change_how_many_of_a_product_to_buy(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    tortillas = item_named(get_order(client, order_id), "tortillas")

    order = ok(change_item(client, order_id, tortillas["id"], {"count": 3}))

    assert item_named(order, "tortillas")["product"]["count"] == 3
    detail(change_item(client, order_id, tortillas["id"], {"count": 0}), 422)


def test_switching_to_an_alternative_keeps_the_product_it_had_as_one(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    tortillas = item_named(get_order(client, order_id), "tortillas")

    order = ok(change_item(client, order_id, tortillas["id"], {"alternative": "3371870"}))

    switched = item_named(order, "tortillas")
    assert (switched["product"]["name"], switched["product"]["count"]) == ("Sweet Onions", 2)
    assert [a["name"] for a in switched["alternatives"]] == ["Wegmans Onions, Yellow"]
    # The same switch again (a retried request whose answer was lost): done already.
    again = ok(change_item(client, order_id, tortillas["id"], {"alternative": "3371870"}))
    assert item_named(again, "tortillas") == switched
    # And back.
    order = ok(change_item(client, order_id, tortillas["id"], {"alternative": "17499438"}))
    assert item_named(order, "tortillas")["product"]["name"] == "Wegmans Onions, Yellow"
    problem = detail(change_item(client, order_id, tortillas["id"], {"alternative": "1"}), 409)
    assert problem == "That product isn't one of the item's alternatives."


def test_items_cant_change_while_the_list_is_shopped(client: TestClient) -> None:
    order_id, build_id = shopped_draft(client, shop=False)
    with Session(get_engine()) as worker_session:
        create_shop(worker_session, worker_session.get_one(Job, UUID(build_id)), uuid4(), MOM)
        worker_session.commit()
    tortillas = item_named(get_order(client, order_id), "tortillas")

    problem = detail(change_item(client, order_id, tortillas["id"], {"removed": True}), 409)

    assert problem == "The list is being shopped; wait for it to finish."


def test_an_item_without_a_product_has_no_count_to_change(client: TestClient) -> None:
    order_id, _ = shopped_draft(client, missing=frozenset({"cotija"}))
    cotija = item_named(get_order(client, order_id), "cotija")

    problem = detail(change_item(client, order_id, cotija["id"], {"count": 2}), 409)

    assert problem == "The item has no product to buy more or fewer of."


def shop_again(client: TestClient, order_id: str, job_id: UUID | None = None) -> Response:
    return client.post(
        f"/v1/grocery-orders/{order_id}/list/shop",
        headers=as_user(MOM),
        json={"job_id": str(job_id or uuid4())},
    )


def test_shopping_that_failed_can_be_started_again(
    client: TestClient, dispatcher: RecordingDispatcher
) -> None:
    order_id, build_id = shopped_draft(client, shop=False)
    with Session(get_engine()) as worker_session:
        failed = create_shop(
            worker_session, worker_session.get_one(Job, UUID(build_id)), uuid4(), MOM
        )
        worker_session.commit()
        fail_job(worker_session, failed.id, "Instacart needs you to sign in again.")
    job_id = uuid4()

    started = ok(shop_again(client, order_id, job_id), 201)
    retried = ok(shop_again(client, order_id, job_id))

    assert (started["id"], started["kind"], started["status"]) == (str(job_id), "shop", "pending")
    assert retried == started
    assert dispatcher.dispatched[-1] == job_id
    assert get_order(client, order_id)["shopping_list"]["shop"]["id"] == str(job_id)
    # Shopping already on its way is returned, not started again.
    assert ok(shop_again(client, order_id))["id"] == str(job_id)


def test_shopping_needs_a_list_for_the_picks(client: TestClient) -> None:
    order_id, [tacos, _] = with_suggestions(client)
    ok(pick(client, order_id, tacos))

    assert detail(shop_again(client, order_id), 409) == "The shopping list isn't ready yet."


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
    "covering_from not a date": ("get", "/v1/grocery-orders?covering_from=soon", None),
    "no rating": ("put", f"/v1/meals/{JOB_ID}/rating", {}),
    "not a rating": ("put", f"/v1/meals/{JOB_ID}/rating", {"rating": "meh"}),
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
    assert (batch.getMessage(), *(vars(batch)[key] for key in ("email", "device", "entries"))) == (
        "client.batch",
        "mom@example.com",
        {"platform": "ios"},
        2,
    )
    assert [vars(line)["entry"] for line in lines] == entries
    assert {vars(line)["batch_id"] for line in lines} == {vars(batch)["batch_id"]}


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
    assert [(vars(line)["entry"]["event"], line.levelname) for line in lines] == [
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


# The final review's total: a quote (stage 3).


def test_the_delivery_date_and_its_latest_time_are_saved_together(client: TestClient) -> None:
    order_id = open_draft(client)["id"]

    saved = ok(put_dates(client, order_id, tomorrow(), None, by="15:00"))

    assert (saved["delivery_date"], saved["delivery_by"]) == (tomorrow().isoformat(), "15:00:00")
    assert (saved["best_window"], saved["fallback_window"]) == (
        {"start": "13:00:00", "end": "15:00:00"},
        {"start": "14:00:00", "end": "16:00:00"},
    )
    # No window ends by 6:00 AM or includes it: nothing to book.
    early = ok(put_dates(client, order_id, tomorrow(), None, by="06:00"))
    assert (early["best_window"], early["fallback_window"]) == (None, None)
    # Not chosen yet: the app shows the household's delivery time.
    cleared = ok(put_dates(client, order_id, tomorrow(), None, by=None))
    assert cleared["delivery_by"] is None
    detail(put_dates(client, order_id, tomorrow(), None, by="15:00:30"), 422)


def test_a_quote_needs_the_list_shopped_and_decided(client: TestClient) -> None:
    unshopped, _ = shopped_draft(client, shop=False)
    assert "isn't shopped" in detail(request_quote(client, unshopped), 409)
    close_flow(client, unshopped)
    no_content(client.delete(f"/v1/grocery-orders/{unshopped}", headers=as_user(MOM)))

    undecided, _ = shopped_draft(client, missing=frozenset({"cotija"}))
    assert "Decide on what Wegmans doesn't have: cotija" in detail(
        request_quote(client, undecided), 409
    )


def test_a_quote_prices_the_lists_products_and_counts_with_the_households_tip(
    client: TestClient, session: Session, dispatcher: RecordingDispatcher
) -> None:
    order_id, _ = shopped_draft(client)
    ok(patch_household(client, {"tip_percent": 12, "tip_max_cents": 900}))

    job = ok(request_quote(client, order_id), 201)

    stored = session.get_one(Job, UUID(job["id"]))
    assert stored.quote_request is not None
    # Both items were shopped as the same onions: the cart holds them once.
    assert [(line.product_id, line.quantity) for line in stored.quote_request.lines] == [
        (ONIONS.product_id, 2)
    ]
    assert (stored.quote_request.tip_percent, stored.quote_request.tip_max_cents) == (12, 900)
    assert dispatcher.dispatched[-1] == UUID(job["id"])
    shown = get_order(client, order_id)["quote"]
    assert (shown["job"]["id"], shown["job"]["kind"], shown["current"]) == (
        job["id"],
        "quote",
        True,
    )
    assert (shown["checkout"], shown["blocked_because"]) == (None, None)


def test_a_quote_for_the_same_products_is_reused_and_a_change_needs_a_new_one(
    client: TestClient,
) -> None:
    order_id, _ = shopped_draft(client)
    first = ok(request_quote(client, order_id), 201)
    finish_quote(first["id"])

    again = ok(request_quote(client, order_id), 200)
    assert again["id"] == first["id"]
    shown = get_order(client, order_id)["quote"]
    assert (shown["current"], shown["checkout"]) == (True, CHECKOUT.model_dump(mode="json"))

    # One more tortilla product: the total read is no longer the order's.
    tortillas = item_named(get_order(client, order_id), "tortillas")
    ok(change_item(client, order_id, tortillas["id"], {"count": 2}))
    assert get_order(client, order_id)["quote"]["current"] is False
    # So is a new tip rule.
    second = ok(request_quote(client, order_id), 201)
    finish_quote(second["id"])
    ok(patch_household(client, {"tip_percent": 20}))
    assert get_order(client, order_id)["quote"]["current"] is False


def test_placing_needs_the_current_quote(client: TestClient, grocer: list[UUID]) -> None:
    order_id, _ = shopped_draft(client)
    first = ok(request_quote(client, order_id), 201)
    finish_quote(first["id"])
    dates = {
        "delivery_date": tomorrow().isoformat(),
        "delivery_by": "15:00",
        "next_delivery_date": (tomorrow() + timedelta(days=2)).isoformat(),
    }

    def place_on(quote_id: str) -> Response:
        return client.post(
            f"/v1/grocery-orders/{order_id}/place",
            headers=as_user(MOM),
            json=dates | {"quote_id": quote_id},
        )

    tortillas = item_named(get_order(client, order_id), "tortillas")
    ok(change_item(client, order_id, tortillas["id"], {"count": 2}))
    assert "total changed" in detail(place_on(first["id"]), 409)
    second = ok(request_quote(client, order_id), 201)
    assert "isn't ready" in detail(place_on(second["id"]), 409)
    finish_quote(second["id"])

    placed = ok(place_on(second["id"]))

    assert (placed["status"], placed["delivery_by"]) == ("placed", "15:00:00")
    assert grocer == [UUID(order_id)]


def test_a_tip_rule_changed_just_as_the_order_is_placed_needs_the_new_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, grocer: list[UUID]
) -> None:
    order_id, _ = shopped_draft(client)
    quote_id = ready_quote(client, order_id)
    place = orders.place

    def tip_changed_then_placed(session: Session, household_id: UUID, *args: Any) -> Any:
        # Another member changes the tip after this request found its household.
        with Session(get_engine()) as other:
            household = other.get_one(Household, household_id)
            household.tip_percent = 20
            other.add(household)
            other.commit()
        return place(session, household_id, *args)

    monkeypatch.setattr(orders, "place", tip_changed_then_placed)

    response = client.post(
        f"/v1/grocery-orders/{order_id}/place",
        headers=as_user(MOM),
        json={
            "delivery_date": tomorrow().isoformat(),
            "delivery_by": "15:00",
            "next_delivery_date": (tomorrow() + timedelta(days=2)).isoformat(),
            "quote_id": quote_id,
        },
    )

    assert detail(response, 409) == "The total changed; check the new one."
    assert get_order(client, order_id)["status"] == "draft"
    assert grocer == []


def test_an_order_below_the_stores_minimum_cant_be_placed(
    client: TestClient, grocer: list[UUID]
) -> None:
    order_id, _ = shopped_draft(client)
    job = ok(request_quote(client, order_id), 201)
    finish_quote(job["id"], Quote(blocked_because="$10 Min. to checkout"))

    shown = get_order(client, order_id)["quote"]
    assert (shown["blocked_because"], shown["checkout"]) == ("$10 Min. to checkout", None)
    assert detail(place(client, order_id), 409) == "$10 Min. to checkout"
    assert grocer == []


def test_a_quote_that_cant_be_started_fails_visibly(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    app.dependency_overrides[get_dispatcher] = lambda: RecordingDispatcher(fail=True)

    detail(request_quote(client, order_id), 503)

    shown = get_order(client, order_id)["quote"]
    assert (shown["job"]["status"], shown["job"]["error"]) == (
        "failed",
        "The total could not be started. Try again.",
    )


def test_a_quote_that_failed_because_instacart_signed_out_says_so(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    job = ok(request_quote(client, order_id), 201)
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(job["id"]))
        assert finish_job(worker_session, UUID(job["id"]), error=SIGNED_OUT_ERROR)

    shown = get_order(client, order_id)["quote"]

    assert (shown["job"]["status"], shown["signed_out"]) == ("failed", True)
    # Any other failure isn't that.
    tortillas = item_named(get_order(client, order_id), "tortillas")
    ok(change_item(client, order_id, tortillas["id"], {"count": 3}))
    other = ok(request_quote(client, order_id), 201)
    with Session(get_engine()) as worker_session:
        claim_job(worker_session, UUID(other["id"]))
        assert finish_job(worker_session, UUID(other["id"]), error="Checkout changed.")
    assert get_order(client, order_id)["quote"]["signed_out"] is False


def test_a_renewal_whose_lock_was_released_meanwhile_says_the_order_isnt_open_here(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = open_draft(client)["id"]
    renew = orders.renew_lock

    def renewed_then_closed(
        session: Session, household_id: UUID, order_id: UUID, by: orders.Orderer, step: Any
    ) -> GroceryOrder:
        # The flow closes (releasing the lock) just as the renewal commits.
        order = renew(session, household_id, order_id, by, step)
        with Session(get_engine()) as other:
            orders.release_lock(other, household_id, order_id, by.session)
        session.refresh(order)
        return order

    monkeypatch.setattr(orders, "renew_lock", renewed_then_closed)

    response = client.put(
        f"/v1/grocery-orders/{order_id}/lock", headers=as_user(MOM), json={"step": "pick"}
    )

    assert "isn't open here any more" in detail(response, 423)


# Booking: an approved order waits to book (four days before delivery), or books
# now; until it books, it can be changed (back into the draft) or cancelled.


def book_state_of(session: Session, order_id: str, state: str, clicked: bool = False) -> None:
    """The order's booking ended that way (as the worker settles it)."""
    session.execute(
        text(
            "UPDATE grocery_order SET book_state = :state, book_job_id = NULL, "
            "clicked_at = CASE WHEN :clicked THEN now() END WHERE id = :id"
        ),
        {"state": state, "clicked": clicked, "id": order_id},
    )
    session.commit()


def change(client: TestClient, order_id: str, user: UUID = MOM) -> Response:
    return client.post(f"/v1/grocery-orders/{order_id}/change", headers=as_user(user))


def cancel(client: TestClient, order_id: str, user: UUID = MOM) -> Response:
    return client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(user))


def test_an_order_approved_before_its_day_waits_to_book_and_one_due_books_now(
    client: TestClient, dispatcher: RecordingDispatcher, grocer: list[UUID]
) -> None:
    later = tomorrow() + timedelta(days=5)
    waiting = placed_order(client, later, later + timedelta(days=3))
    soon = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3), NEIGHBOR)

    assert (waiting["book_state"], waiting["book_on"]) == (
        "waiting",
        (later - timedelta(days=4)).isoformat(),
    )
    assert soon["book_state"] == "booking"
    assert grocer == [UUID(soon["id"])]
    [book] = [job for job in soon["jobs"] if job["kind"] == "book"]
    assert book["requested_by"]["user_id"] == str(NEIGHBOR)
    assert dispatcher.dispatched[-1] == UUID(book["id"])
    # Approved, it's ordered for the rest of the app: the next order follows it.
    assert next_order(client)["target_date"] == (later + timedelta(days=3)).isoformat()


def test_a_waiting_order_goes_back_into_the_draft_to_change_it_then_is_approved_again(
    client: TestClient, grocer: list[UUID]
) -> None:
    later = tomorrow() + timedelta(days=5)
    waiting = placed_order(client, later, later + timedelta(days=3))

    reopened = ok(change(client, waiting["id"], DAD))

    assert (reopened["status"], reopened["book_state"], reopened["number"]) == ("draft", None, 1)
    assert next_order(client)["draft"]["id"] == waiting["id"]
    assert next_order(client)["target_date"] is None  # nothing approved now
    open_draft(client)
    assert ok(place(client, waiting["id"]))["book_state"] == "waiting"
    assert grocer == []


def test_a_waiting_order_can_only_be_cancelled_once_the_next_order_is_started(
    client: TestClient,
) -> None:
    later = tomorrow() + timedelta(days=5)
    waiting = placed_order(client, later, later + timedelta(days=3))
    following = open_draft(client)["id"]

    assert detail(change(client, waiting["id"]), 409) == (
        "The next order has been started, so this one can only be cancelled now."
    )
    no_content(cancel(client, waiting["id"], DAD))

    detail(client.get(f"/v1/grocery-orders/{waiting['id']}", headers=as_user(MOM)), 404)
    # As if it had never been approved: the started draft is the first order again.
    assert (next_order(client)["number"], next_order(client)["draft"]["id"]) == (1, following)


def test_a_waiting_order_can_only_be_cancelled_once_a_later_order_is_placed(
    client: TestClient,
) -> None:
    # As the draft again, it would come before an order already placed.
    later = tomorrow() + timedelta(days=5)
    first = placed_order(client, later, later + timedelta(days=3))
    second = placed_order(client, later + timedelta(days=3), later + timedelta(days=6), DAD)

    assert detail(change(client, first["id"]), 409) == (
        "The next order has been placed, so this one can only be cancelled now."
    )
    unchanged = get_order(client, first["id"])
    assert (unchanged["status"], unchanged["book_state"]) == ("placed", "waiting")
    # The newest one can still be changed.
    assert ok(change(client, second["id"], DAD))["status"] == "draft"


def test_an_order_being_booked_or_booked_cant_be_changed_or_cancelled(
    client: TestClient, session: Session
) -> None:
    booking_now = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    order_id = booking_now["id"]

    for refused in (change(client, order_id), cancel(client, order_id)):
        assert detail(refused, 409) == "It's being booked at the store right now."
    book_state_of(session, order_id, "booked")
    for refused in (change(client, order_id), cancel(client, order_id)):
        assert detail(refused, 409) == "It's booked at the store: change it in Instacart."
    book_state_of(session, order_id, "check_instacart", clicked=True)
    assert detail(cancel(client, order_id), 409) == "Say first whether Instacart has it."
    assert get_order(client, order_id)["status"] == "placed"


def test_an_order_that_wasnt_booked_is_booked_again_on_request(
    client: TestClient, session: Session, dispatcher: RecordingDispatcher, grocer: list[UUID]
) -> None:
    later = tomorrow() + timedelta(days=5)
    waiting = placed_order(client, later, later + timedelta(days=3))
    # Waiting for its day: Try again doesn't book it early.
    queued = list(dispatcher.dispatched)
    detail(client.post(f"/v1/grocery-orders/{waiting['id']}/book", headers=as_user(MOM)), 409)
    assert (grocer, dispatcher.dispatched) == ([], queued)
    assert get_order(client, waiting["id"])["book_state"] == "waiting"
    book_state_of(session, waiting["id"], "not_booked")

    again = ok(client.post(f"/v1/grocery-orders/{waiting['id']}/book", headers=as_user(DAD)))

    assert again["book_state"] == "booking"
    [book] = [job for job in again["jobs"] if job["kind"] == "book"]
    # For whoever approved it, who hears how it goes.
    assert book["requested_by"]["user_id"] == str(MOM)
    assert dispatcher.dispatched[-1] == UUID(book["id"])


def store_time(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(ZONE))


def test_an_order_still_waiting_two_hours_after_its_booking_time_is_shown_not_booked(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch, grocer: list[UUID]
) -> None:
    later = tomorrow() + timedelta(days=5)
    order_id = placed_order(client, later, later + timedelta(days=3))["id"]
    book_day = later - timedelta(days=4)

    def shown_at(hour: int, minute: int = 0) -> list[dict[str, Any]]:
        """The order as it's shown then, alone and in the placed orders."""
        monkeypatch.setattr(booking, "store_now", lambda: store_time(book_day, hour, minute))
        [listed] = placed_orders(client)
        return [get_order(client, order_id), listed]

    # Its 9:00 run, and the one at 10:00, may still book it.
    for shown in shown_at(10, 59):
        assert (shown["book_state"], shown["book_issue"]) == ("waiting", None)
    for shown in shown_at(11):
        assert (shown["book_state"], shown["book_on"], shown["book_issue"]) == (
            "not_booked",
            book_day.isoformat(),
            {
                "message": f"It didn't book on {book_day:%a, %b} {book_day.day} as planned.",
                "item_ids": [],
            },
        )
    # Only shown so: nothing was written or started, and the next run may still book it.
    session.expire_all()
    assert session.get_one(GroceryOrder, UUID(order_id)).book_state == BookState.WAITING
    assert grocer == []


def test_try_again_books_an_order_whose_booking_is_late(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, dispatcher: RecordingDispatcher
) -> None:
    later = tomorrow() + timedelta(days=5)
    order_id = placed_order(client, later, later + timedelta(days=3))["id"]
    book_day = later - timedelta(days=4)
    monkeypatch.setattr(booking, "store_now", lambda: store_time(book_day, 15))

    again = ok(client.post(f"/v1/grocery-orders/{order_id}/book", headers=as_user(DAD)))

    assert (again["book_state"], again["book_issue"]) == ("booking", None)
    [book] = [job for job in again["jobs"] if job["kind"] == "book"]
    assert book["requested_by"]["user_id"] == str(MOM)
    assert dispatcher.dispatched[-1] == UUID(book["id"])
    queued = list(dispatcher.dispatched)
    # Being booked now: a second Try again is refused, and starts nothing.
    assert detail(
        client.post(f"/v1/grocery-orders/{order_id}/book", headers=as_user(MOM)), 409
    ) == ("It's being booked at the store right now.")
    assert dispatcher.dispatched == queued


def test_someone_says_whether_instacart_has_an_order_booking_couldnt_confirm(
    client: TestClient, session: Session
) -> None:
    order_id = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))["id"]
    book_state_of(session, order_id, "check_instacart", clicked=True)
    settle = f"/v1/grocery-orders/{order_id}/settle"

    wasnt = ok(client.post(settle, headers=as_user(DAD), json={"placed": False}))

    assert (wasnt["book_state"], wasnt["book_issue"]) == (
        "not_booked",
        {"message": "It wasn't placed at Instacart.", "item_ids": []},
    )
    # It can be booked again now, and nothing else needs settling.
    detail(client.post(settle, headers=as_user(DAD), json={"placed": True}), 409)
    book_state_of(session, order_id, "check_instacart", clicked=True)
    was = ok(client.post(settle, headers=as_user(MOM), json={"placed": True}))
    assert (was["book_state"], was["booking"]) == (
        "booked",
        {"order_number": None, "window": None, "total_cents": None, "swaps": []},
    )


# Members: invited by email, joining on their first sign-in; removed by anyone in
# the household, but never the last.


def invite(client: TestClient, email: str, name: str = "Sam", user: UUID = MOM) -> Response:
    return client.post(
        "/v1/household/invites", headers=as_user(user), json={"email": email, "name": name}
    )


def remove_member(client: TestClient, who: UUID, user: UUID = MOM) -> Response:
    return client.delete(f"/v1/household/members/{who}", headers=as_user(user))


def account(session: Session, email: str, name: str) -> UUID:
    """An account, as Neon Auth makes one on a first sign-in with an emailed code."""
    user_id = uuid4()
    session.execute(
        text('INSERT INTO neon_auth."user" (id, email, name) VALUES (:id, :email, :name)'),
        {"id": user_id, "email": email, "name": name},
    )
    session.commit()
    return user_id


def me(client: TestClient, user: UUID) -> dict[str, Any]:
    return ok(client.get("/v1/me", headers=as_user(user)))


def test_someone_invited_joins_the_household_when_they_first_sign_in(
    client: TestClient, session: Session
) -> None:
    invited = ok(invite(client, "Sam@Example.com"), 201)["invites"]
    assert [(i["name"], i["email"], i["invited_by"]["name"]) for i in invited] == [
        ("Sam", "Sam@Example.com", "Mom")
    ]
    # Everyone in the household sees the invitation.
    assert household_settings(client, DAD)["invites"] == invited

    # Signing in with the emailed code makes the account, unnamed.
    sam = account(session, "sam@example.com", "")
    signed_in = me(client, sam)

    assert (signed_in["member"], signed_in["household"]["name"]) == (True, "Conn Castle")
    # Called what the invitation said, listed after the members invited before.
    assert signed_in["name"] == "Sam"
    household = household_settings(client, sam)
    names_ = [m["name"] for m in household["members"]]
    assert (sorted(names_[:2]), names_[2]) == (["Dad", "Mom"], "Sam")
    assert household["invites"] == []


def test_someone_signed_in_but_never_invited_joins_nothing(client: TestClient) -> None:
    signed_in = me(client, STRANGER)

    assert (signed_in["member"], signed_in["household"]) == (False, None)
    detail(client.get("/v1/household", headers=as_user(STRANGER)), 403)


def test_an_invitation_can_be_withdrawn_before_its_used(
    client: TestClient, session: Session
) -> None:
    [invited] = ok(invite(client, "sam@example.com"), 201)["invites"]

    after = ok(client.delete(f"/v1/household/invites/{invited['id']}", headers=as_user(DAD)))

    assert after["invites"] == []
    assert me(client, account(session, "sam@example.com", "Sam"))["member"] is False
    # Another household's members can't withdraw it.
    [again] = ok(invite(client, "kid@example.com"), 201)["invites"]
    ok(client.delete(f"/v1/household/invites/{again['id']}", headers=as_user(NEIGHBOR)))
    assert [i["email"] for i in household_settings(client)["invites"]] == ["kid@example.com"]


def test_invitations_that_cant_be_are_refused(client: TestClient) -> None:
    assert (
        detail(invite(client, "dad@example.com"), 409)
        == "dad@example.com is already in this household."
    )
    assert detail(invite(client, "neighbor@example.com"), 409) == (
        "neighbor@example.com is in another household."
    )
    ok(invite(client, "sam@example.com", user=NEIGHBOR), 201)
    assert detail(invite(client, "sam@example.com"), 409) == (
        "sam@example.com is invited to another household."
    )
    assert detail(invite(client, "sam"), 422) == "sam isn't an email address."
    detail(invite(client, "kid@example.com", name=" "), 422)


def test_a_member_removed_loses_access_at_once_and_their_lock_goes(
    client: TestClient, session: Session
) -> None:
    order_id = open_draft(client, DAD)["id"]  # Dad has its lock

    no_content(remove_member(client, DAD))

    assert [m["name"] for m in household_settings(client)["members"]] == ["Mom"]
    detail(client.get("/v1/household", headers=as_user(DAD)), 403)
    assert me(client, DAD)["member"] is False
    # Mom can order now.
    assert get_order(client, order_id)["lock"] is None
    ok(take(client, order_id, MOM))


def test_a_member_can_leave_but_the_last_one_cant(client: TestClient) -> None:
    no_content(remove_member(client, MOM, user=MOM))  # Mom leaves

    assert detail(remove_member(client, DAD, user=DAD), 409) == (
        "They're the household's only member: a household can't be left empty."
    )
    assert [m["name"] for m in household_settings(client, DAD)["members"]] == ["Dad"]


def test_only_the_households_own_members_can_be_removed(client: TestClient) -> None:
    detail(remove_member(client, NEIGHBOR), 404)
    detail(remove_member(client, uuid4()), 404)
    assert [m["name"] for m in household_settings(client, NEIGHBOR)["members"]] == ["Neighbor"]


class Verifier:
    """Neon Auth's signature checked (test_auth.py checks the real one)."""

    def __init__(self, genuine: bool = True) -> None:
        self.genuine = genuine

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        if not self.genuine:
            raise WebhookRejected("Bad signature.")


def before_create(client: TestClient, email: str, genuine: bool = True) -> Response:
    app.dependency_overrides[api_module.get_webhook_verifier] = lambda: Verifier(genuine)
    body = {
        "event_id": str(uuid4()),
        "event_type": "user.before_create",
        "timestamp": "2026-10-03T12:00:00.000Z",
        "user": {"id": str(uuid4()), "email": email, "name": "Someone"},
        "event_data": {"auth_provider": "credential"},
    }
    return client.post("/v1/auth/neon-webhook", json=body)


def test_neon_auth_makes_an_account_only_for_an_invited_email(client: TestClient) -> None:
    ok(invite(client, "sam@example.com"), 201)

    assert ok(before_create(client, "SAM@example.com")) == {"allowed": True, "error_message": None}
    refused = ok(before_create(client, "eve@example.com"))
    assert refused["allowed"] is False
    assert "invite-only" in refused["error_message"]
    # A request Neon Auth didn't sign is refused outright (and so is the sign-up).
    detail(before_create(client, "sam@example.com", genuine=False), 401)


def test_someone_who_already_has_a_name_keeps_it_when_they_join(
    client: TestClient, session: Session
) -> None:
    ok(invite(client, "sam@example.com", name="Sam"), 201)
    samuel = account(session, "sam@example.com", "Samuel")  # named before, e.g. removed once

    assert me(client, samuel)["name"] == "Samuel"


def test_a_requests_database_work_is_done_before_its_answer_is_sent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Lambda the environment can freeze as soon as the answer is out: a
    # session still open then stays mid-transaction until it thaws.
    events: list[str] = []
    opened = get_request_session

    def watched_session() -> Session:
        session = opened()
        close = session.close

        def closing() -> None:
            events.append("session closed")
            close()

        monkeypatch.setattr(session, "close", closing)
        return session

    monkeypatch.setattr(api_module, "get_request_session", watched_session)

    async def watched(scope: Scope, receive: Receive, send: Send) -> None:
        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                events.append("answer sent")
            await send(message)

        await app(scope, receive, sending)

    with TestClient(watched) as watching:
        assert watching.get("/v1/me", headers=as_user(MOM)).status_code == 200

    assert events == ["session closed", "answer sent"]


def test_each_request_says_whose_it_was_and_why_it_was_refused(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="castle_chef.api"):
        missing = client.get(f"/v1/grocery-orders/{uuid4()}", headers=as_user(MOM))
        client.get("/v1/me")
        client.get("/v1/me", headers=as_user(MOM))

    lines = [r.__dict__ for r in caplog.records if r.getMessage() == "http.request"]
    assert [(line["status"], line["user_id"], line.get("detail")) for line in lines] == [
        (404, str(MOM), missing.json()["detail"]),
        (401, None, "Sign in first."),
        (200, str(MOM), None),
    ]


def test_a_request_that_fails_unexpectedly_is_logged_as_an_error_with_its_traceback(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> None:
        raise RuntimeError("a defect")

    monkeypatch.setattr(household, "join_if_invited", broken)
    with (
        TestClient(app, raise_server_exceptions=False) as failing,
        caplog.at_level(logging.INFO, logger="castle_chef.api"),
    ):
        assert failing.get("/v1/me", headers=as_user(MOM)).status_code == 500

    [failed] = [r for r in caplog.records if r.getMessage() == "http.failed"]
    assert failed.levelname == "ERROR"
    assert (failed.__dict__["route"], failed.__dict__["user_id"]) == ("/v1/me", str(MOM))
    assert failed.exc_info is not None and "a defect" in str(failed.exc_info[1])


def test_a_caller_that_hangs_up_is_not_logged_as_a_defect(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(*_: object) -> None:
        raise ClientDisconnect()

    monkeypatch.setattr(household, "join_if_invited", gone)
    with caplog.at_level(logging.INFO, logger="castle_chef.api"):
        client.get("/v1/me", headers=as_user(MOM))

    events = [(r.getMessage(), r.levelname) for r in caplog.records if r.name == "castle_chef.api"]
    assert ("http.client_gone", "WARNING") in events
    assert not [event for event in events if event[1] == "ERROR"]
