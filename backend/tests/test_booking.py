"""When approved orders book (castle_chef.booking): the daily run at 9:00 AM store
time, four days before delivery; Place order claimed at most once; and bookings
whose worker stopped, through the failure handler's events."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from castle_chef import booking, push, reminders
from castle_chef.api import create_app
from castle_chef.checkout import BookIssue, CartLine, QuoteRequest
from castle_chef.jobs import STALE_ERROR
from castle_chef.models import (
    AppUser,
    BookState,
    GroceryOrder,
    GroceryOrderStatus,
    Household,
    Job,
    JobKind,
    JobStatus,
    PushDevice,
    utc_now,
)
from castle_chef.push import Notification, Outcome, Sent
from castle_chef.settings import get_settings
from tests.test_failures import task_stopped

STORE = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 5)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=STORE)


def approved(session: Session, household_id: UUID, delivery: date) -> GroceryOrder:
    """An order approved for delivery on that day, waiting to book."""
    member = uuid4()
    order = GroceryOrder(household_id=household_id, created_by=member)
    session.add(order)
    session.flush()
    quote = Job(
        id=uuid4(),
        grocery_order_id=order.id,
        kind=JobKind.QUOTE,
        requested_by=member,
        status=JobStatus.SUCCEEDED,
        quote_request=QuoteRequest(
            lines=[CartLine(product_id="1", quantity=1)], tip_percent=10, tip_max_cents=1500
        ),
    )
    session.add(quote)
    session.flush()
    order.status, order.placed_at, order.placed_by = GroceryOrderStatus.PLACED, utc_now(), member
    order.delivery_date, order.next_delivery_date = delivery, delivery + timedelta(days=4)
    order.quote_id, order.book_state = quote.id, BookState.WAITING
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def another_household(session: Session) -> UUID:
    household = Household(name=f"Household {uuid4()}")
    session.add(household)
    session.commit()
    return household.id


def reloaded(session: Session, order: GroceryOrder) -> GroceryOrder:
    session.expire_all()
    return session.get_one(GroceryOrder, order.id)


class Queue:
    def __init__(self) -> None:
        self.dispatched: list[UUID] = []

    def dispatch(self, job_id: UUID) -> None:
        self.dispatched.append(job_id)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Queue:
    queued = Queue()
    monkeypatch.setattr(reminders, "get_dispatcher", lambda: queued)
    return queued


def run_daily(monkeypatch: pytest.MonkeyPatch, now: datetime) -> dict[str, list[str]]:
    """The booking schedule's firing, at `now` (the store's time)."""
    monkeypatch.setattr(booking, "store_now", lambda: now)
    return reminders.handle_event({"book_due": {}})


def test_an_order_books_at_nine_in_the_morning_four_days_before_delivery() -> None:
    delivery = TODAY + timedelta(days=4)
    assert not booking.is_due(delivery, at(TODAY - timedelta(days=1), 23, 59))
    assert not booking.is_due(delivery, at(TODAY, 8, 59))
    assert booking.is_due(delivery, at(TODAY, 9))
    # Approved later than that: right away.
    assert booking.is_due(delivery, at(TODAY + timedelta(days=2), 7))


def test_the_daily_run_starts_booking_the_orders_due_and_leaves_the_rest_waiting(
    session: Session, household_id: UUID, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    due = approved(session, household_id, TODAY + timedelta(days=4))
    later = approved(session, another_household(session), TODAY + timedelta(days=5))

    result = run_daily(monkeypatch, at(TODAY, 9))

    started = reloaded(session, due)
    assert started.book_state == BookState.BOOKING and started.book_job_id is not None
    assert result == {"queued": [str(started.book_job_id)]}
    assert queue.dispatched == [started.book_job_id]
    book = session.get_one(Job, started.book_job_id)
    assert (book.kind, book.requested_by, book.status) == (
        JobKind.BOOK,
        due.placed_by,
        JobStatus.PENDING,
    )
    assert reloaded(session, later).book_state == BookState.WAITING
    # The next day's run starts it, and nothing twice.
    run_daily(monkeypatch, at(TODAY + timedelta(days=1), 9))
    assert reloaded(session, later).book_state == BookState.BOOKING
    assert len(queue.dispatched) == 2


def stale(session: Session, order: GroceryOrder, clicked: bool) -> None:
    """Its book job started long ago and its worker was lost, maybe after the click."""
    job = session.get_one(Job, order.book_job_id)
    job.status, job.started_at = JobStatus.RUNNING, utc_now() - timedelta(hours=3)
    session.add(job)
    if clicked:
        order = session.get_one(GroceryOrder, order.id)
        order.clicked_at = utc_now() - timedelta(hours=3)
        session.add(order)
    session.commit()


@pytest.mark.parametrize(
    ("clicked", "state"),
    [(False, BookState.NOT_BOOKED), (True, BookState.CHECK_INSTACART)],
)
def test_the_daily_run_settles_a_booking_whose_worker_was_lost(
    session: Session,
    household_id: UUID,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
    clicked: bool,
    state: BookState,
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_daily(monkeypatch, at(TODAY, 9))
    stale(session, reloaded(session, order), clicked)

    run_daily(monkeypatch, at(TODAY + timedelta(days=1), 9))

    settled = reloaded(session, order)
    assert (settled.book_state, settled.book_job_id) == (state, None)
    if not clicked:
        assert settled.book_issue == BookIssue(message=STALE_ERROR)


def test_place_order_is_claimed_once(
    session: Session, household_id: UUID, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_daily(monkeypatch, at(TODAY, 9))
    job_id = reloaded(session, order).book_job_id
    assert job_id is not None
    # Not before its worker has it.
    assert booking.claim_click(session, job_id) is False
    job = session.get_one(Job, job_id)
    job.status, job.started_at = JobStatus.RUNNING, utc_now()
    session.add(job)
    session.commit()

    assert booking.claim_click(session, job_id) is True
    assert booking.claim_click(session, job_id) is False
    # Another job (an order not being booked by it) never claims it.
    assert booking.claim_click(session, uuid4()) is False
    # And nothing starts booking it again.
    clicked = reloaded(session, order)
    with pytest.raises(ValueError, match="already clicked"):
        booking.start(session, clicked)


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Notification]] = []

    def send(self, token: str, notification: Notification) -> Sent:
        self.sent.append((token, notification))
        return Sent(Outcome.SENT)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CASTLE_CHEF_FAILURE_HANDLER", "true")
    get_settings.cache_clear()
    yield TestClient(create_app())


@pytest.mark.parametrize(
    ("clicked", "state", "title"),
    [
        (False, BookState.NOT_BOOKED, "wasn't booked"),
        (True, BookState.CHECK_INSTACART, "Check Instacart for Order"),
    ],
)
def test_a_booking_whose_worker_stopped_is_settled_and_whoever_approved_it_told(
    session: Session,
    household_id: UUID,
    queue: Queue,
    events: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    clicked: bool,
    state: BookState,
    title: str,
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_daily(monkeypatch, at(TODAY, 9))
    order = reloaded(session, order)
    stale(session, order, clicked)
    assert order.placed_by is not None
    session.add(AppUser(user_id=order.placed_by, household_id=household_id))
    session.add(PushDevice(token="cd" * 32, user_id=order.placed_by))
    session.commit()
    sender = RecordingSender()
    monkeypatch.setattr(push, "get_sender", lambda: sender)

    response = events.post("/events", json=task_stopped(str(order.book_job_id), 137, "OOM"))

    assert response.status_code == 200
    settled = reloaded(session, order)
    assert settled.book_state == state
    [(token, notification)] = sender.sent
    assert token == "cd" * 32 and title in notification.title
