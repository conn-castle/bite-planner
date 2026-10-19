"""When approved orders book (castle_chef.booking): the run at 9:00 AM store time,
four days before delivery, repeated each hour; Place order claimed at most once;
and bookings whose worker stopped, through the failure handler's events."""

import re
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from castle_chef_agent.checkout import ClickRequest
from castle_chef_agent.windows import Window
from fastapi.testclient import TestClient
from sqlmodel import Session

from castle_chef import booking, jobs, push, reminders
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
from tests.test_checkout_tools import DOCKERFILE
from tests.test_failures import TASK, task_stopped
from tests.test_failures import events as events

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
    order.delivery_by = time(18)
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


def run_booking(monkeypatch: pytest.MonkeyPatch, now: datetime) -> dict[str, list[str]]:
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


def test_the_run_starts_booking_the_orders_due_and_leaves_the_rest_waiting(
    session: Session, household_id: UUID, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    due = approved(session, household_id, TODAY + timedelta(days=4))
    later = approved(session, another_household(session), TODAY + timedelta(days=5))

    result = run_booking(monkeypatch, at(TODAY, 9))

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
    run_booking(monkeypatch, at(TODAY + timedelta(days=1), 9))
    assert reloaded(session, later).book_state == BookState.BOOKING
    assert len(queue.dispatched) == 2


def test_the_run_each_hour_starts_an_order_an_earlier_run_missed_once(
    session: Session, household_id: UUID, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 9:00 run failed: the one at 10:00 makes it up, and the ones after find
    # nothing more to start.
    due = approved(session, household_id, TODAY + timedelta(days=4))

    made_up = run_booking(monkeypatch, at(TODAY, 10))
    again = run_booking(monkeypatch, at(TODAY, 11))

    started = reloaded(session, due)
    assert started.book_state == BookState.BOOKING
    assert made_up == {"queued": [str(started.book_job_id)]}
    assert again == {"queued": []}
    assert queue.dispatched == [started.book_job_id]


def running(
    session: Session, order: GroceryOrder, clicked: bool, since: timedelta = timedelta(hours=3)
) -> None:
    """Its book job's worker claimed it `since` ago, maybe pressing Place order since."""
    job = session.get_one(Job, order.book_job_id)
    job.worker_task_arn = TASK
    job.status, job.started_at = JobStatus.RUNNING, utc_now() - since
    session.add(job)
    if clicked:
        order = session.get_one(GroceryOrder, order.id)
        order.clicked_at = utc_now()
        order.click_job_id = job.id
        session.add(order)
    session.commit()


@pytest.mark.parametrize("clicked", [False, True])
@pytest.mark.parametrize(
    "since",
    # Waiting for the agent behind another job, or close to the worker's own limit
    # (stopped at 40 minutes, killed 30 seconds later).
    [timedelta(minutes=35), timedelta(minutes=40)],
)
def test_the_run_leaves_a_booking_alone_while_its_worker_may_still_be_running(
    session: Session,
    household_id: UUID,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
    clicked: bool,
    since: timedelta,
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_booking(monkeypatch, at(TODAY, 9))
    running(session, reloaded(session, order), clicked, since)
    sender = RecordingSender()
    monkeypatch.setattr(push, "get_sender", lambda: sender)

    run_booking(monkeypatch, at(TODAY, 10))

    still = reloaded(session, order)
    assert still.book_state == BookState.BOOKING and still.book_job_id is not None
    assert session.get_one(Job, still.book_job_id).status == JobStatus.RUNNING
    assert sender.sent == []


@pytest.mark.parametrize(
    ("clicked", "state"),
    [(False, BookState.NOT_BOOKED), (True, BookState.CHECK_INSTACART)],
)
def test_the_run_settles_a_booking_whose_worker_was_lost(
    session: Session,
    household_id: UUID,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
    clicked: bool,
    state: BookState,
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_booking(monkeypatch, at(TODAY, 9))
    # Started by the 9:00 run, and an hour later its worker is long gone.
    running(session, reloaded(session, order), clicked, timedelta(hours=1))

    run_booking(monkeypatch, at(TODAY, 10))

    settled = reloaded(session, order)
    assert (settled.book_state, settled.book_job_id) == (state, None)
    if not clicked:
        assert settled.book_issue == BookIssue(message=STALE_ERROR)


def test_a_bookings_worker_counts_as_lost_by_the_worker_images_own_limit() -> None:
    entrypoint = re.search(
        r'ENTRYPOINT \["timeout", "--kill-after=(\d+)s", "(\d+)s", "castle-chef"\]',
        DOCKERFILE.read_text(),
    )
    assert entrypoint, "docker/Dockerfile no longer says how long the worker may run."
    kill_after, limit = (int(seconds) for seconds in entrypoint.groups())

    assert timedelta(seconds=limit + kill_after) == jobs.WORKER_LIMIT, (
        "Update castle_chef.jobs.WORKER_LIMIT to docker/Dockerfile's timeout."
    )


def test_place_order_is_claimed_once(
    session: Session, household_id: UUID, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = approved(session, household_id, TODAY + timedelta(days=4))
    run_booking(monkeypatch, at(TODAY, 9))
    job_id = reloaded(session, order).book_job_id
    assert job_id is not None
    request = ClickRequest(
        day=TODAY + timedelta(days=4),
        by=time(18),
        window=Window(start=time(16), end=time(18)),
        total_cents=2404,
    )
    # Not before its worker has it.
    assert booking.claim_click(session, job_id, request) is False
    job = session.get_one(Job, job_id)
    job.worker_task_arn = TASK
    job.status, job.started_at = JobStatus.RUNNING, utc_now()
    session.add(job)
    session.commit()

    assert booking.claim_click(session, job_id, request) is True
    assert booking.claim_click(session, job_id, request) is False
    # Another job (an order not being booked by it) never claims it.
    assert booking.claim_click(session, uuid4(), request) is False
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
    run_booking(monkeypatch, at(TODAY, 9))
    order = reloaded(session, order)
    running(session, order, clicked)
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
