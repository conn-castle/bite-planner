"""Booking approved orders at the store (spec/requirements.md "Shopping list",
stage 3; spec/README.md "Delivery, booking, and tip").

An approved order (status `placed`) waits until four days before its delivery,
when Instacart has offered its day for a day, and books at 9:00 AM store time
(a daily scheduled run), or right away when approved after that. A `book` job
fills the cart, has the agent choose the window and read checkout in the browser,
checks it, and presses Place order once (castle_chef.worker). Where the order is
with the store is its `book_state`; what happens next is said to whoever approved
it (castle_chef.notifications).

Place order is pressed at most once per order: the worker records the click
(`clicked_at`) before pressing it, and nothing books an order that has one,
unless a member says it wasn't placed after all.
"""

import logging
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select, update

from castle_chef.checkout import Booking, BookIssue
from castle_chef.jobs import ACTIVE, STALE_ERROR, fail_job, is_stale
from castle_chef.models import (
    BookState,
    GroceryOrder,
    GroceryOrderStatus,
    Job,
    JobKind,
    JobStatus,
    utc_now,
)
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

# An order books this many days before its delivery (Instacart first offers a
# day five days ahead, at midnight), at this hour of the store's day.
BOOK_DAYS_BEFORE = 4
BOOK_HOUR = 9

# The states a member can still change or cancel (nothing is under way at the
# store, and nothing was clicked).
UNBOOKED = (BookState.WAITING, BookState.NEEDS_TIME, BookState.NEEDS_PRODUCT, BookState.NOT_BOOKED)

# Said when booking in production isn't turned on yet (settings.instacart_booking).
BOOKING_OFF = "Booking at Instacart isn't turned on yet."
# The Instacart order number development's dry run shows.
DRY_RUN = "Dry run"


class NotChangeable(Exception):
    """The order can't be changed, cancelled, or booked again now; the message says why."""


def book_on(delivery: date) -> date:
    """The day an order for `delivery` books."""
    return delivery - timedelta(days=BOOK_DAYS_BEFORE)


def store_now() -> datetime:
    """Now, in the store's time zone."""
    return datetime.now(ZoneInfo(get_settings().store_time_zone))


def is_due(delivery: date, now: datetime) -> bool:
    """Whether an order for `delivery` books now (`now` in the store's time zone):
    its day has come and the day's run has been (an order approved before 9:00
    AM that day waits for it)."""
    day = book_on(delivery)
    return now.date() > day or (now.date() == day and now.hour >= BOOK_HOUR)


def start(session: Session, order: GroceryOrder) -> Job:
    """Start booking an approved order (the caller holds its row lock, and
    dispatches the job once this commits): a book job, for whoever approved it."""
    if order.status != GroceryOrderStatus.PLACED or order.placed_by is None:
        raise ValueError(f"Order {order.id} isn't approved.")
    if order.clicked_at is not None:
        raise ValueError(f"Order {order.id} was already clicked.")
    job = Job(
        id=uuid4(), grocery_order_id=order.id, kind=JobKind.BOOK, requested_by=order.placed_by
    )
    session.add(job)
    session.flush()
    order.book_state, order.book_job_id, order.book_issue = BookState.BOOKING, job.id, None
    session.add(order)
    session.commit()
    session.refresh(job)
    log.info("booking.started", extra={"grocery_order_id": str(order.id), "job_id": str(job.id)})
    return job


def start_if_due(session: Session, order: GroceryOrder, now: datetime) -> Job | None:
    """Start booking a waiting order if its day has come (the caller holds its
    row lock); None if it waits."""
    if order.book_state != BookState.WAITING or order.delivery_date is None:
        return None
    if not is_due(order.delivery_date, now):
        return None
    return start(session, order)


def start_due(session: Session, now: datetime) -> list[UUID]:
    """The daily run: start booking every waiting order whose day has come, and
    settle bookings whose job stopped without saying how it went (its worker was
    lost). Returns the book jobs to dispatch."""
    started: list[UUID] = []
    waiting = session.exec(
        select(GroceryOrder.id).where(GroceryOrder.book_state == BookState.WAITING)
    ).all()
    for order_id in waiting:
        order = _lock(session, order_id)
        if order is not None and (job := start_if_due(session, order, now)) is not None:
            started.append(job.id)
        else:
            session.rollback()
    settings = get_settings()
    stale_after = (
        timedelta(seconds=settings.pending_timeout_seconds),
        # The agent's limit, and the cart before and after it.
        timedelta(seconds=settings.agent_timeout_seconds + 600),
    )
    booking = session.exec(
        select(GroceryOrder.book_job_id).where(GroceryOrder.book_state == BookState.BOOKING)
    ).all()
    for job_id in booking:
        job = session.get(Job, job_id) if job_id is not None else None
        if job is None:
            continue
        if job.status in ACTIVE and is_stale(job, utc_now(), *stale_after):
            fail_job(session, job.id, STALE_ERROR)
            log.error("booking.job_stale", extra={"job_id": str(job.id)})
            session.refresh(job)
        if job.status == JobStatus.FAILED:
            job_failed(session, job.id, job.error or STALE_ERROR)
    return started


def _lock(session: Session, order_id: UUID) -> GroceryOrder | None:
    return session.exec(
        select(GroceryOrder).where(GroceryOrder.id == order_id).with_for_update()
    ).first()


def _booking_order(session: Session, job_id: UUID, expected: bool = True) -> GroceryOrder | None:
    """The order this job is booking, locked; None if it's no longer booking it
    (cancelled, or settled meanwhile), or isn't a book job (`expected` false:
    nothing to warn about)."""
    order = session.exec(
        select(GroceryOrder)
        .where(GroceryOrder.book_job_id == job_id, GroceryOrder.book_state == BookState.BOOKING)
        .with_for_update()
    ).first()
    if order is None:
        session.rollback()
        if expected:
            log.warning("booking.not_booking", extra={"job_id": str(job_id)})
    return order


def claim_click(session: Session, job_id: UUID) -> bool:
    """Record that this job is about to press Place order, and commit: false if
    it mustn't (the order isn't being booked by it, or was clicked before, or the
    job is no longer running)."""
    running = select(Job.id).where(Job.id == job_id, Job.status == JobStatus.RUNNING).exists()
    claimed = session.execute(
        update(GroceryOrder)
        .where(
            col(GroceryOrder.book_job_id) == job_id,
            col(GroceryOrder.book_state) == BookState.BOOKING,
            col(GroceryOrder.clicked_at).is_(None),
            # Not failed (as stale) meanwhile: its outcome would go unheard.
            running,
        )
        .values(clicked_at=func.now())
    ).rowcount
    session.commit()
    if claimed:
        log.info("booking.clicking", extra={"job_id": str(job_id)})
    return bool(claimed)


def _settle(
    session: Session,
    order: GroceryOrder,
    state: BookState,
    booking: Booking | None = None,
    issue: BookIssue | None = None,
) -> None:
    order.book_state, order.book_job_id = state, None
    order.booking, order.book_issue = booking, issue
    session.add(order)
    session.commit()
    log.info(
        "booking.settled",
        extra={"grocery_order_id": str(order.id), "state": state, "issue": issue and issue.message},
    )


def finish(
    session: Session,
    job_id: UUID,
    state: BookState,
    booking: Booking | None = None,
    issue: BookIssue | None = None,
) -> GroceryOrder | None:
    """Settle how the job's booking went; None (nothing changed) if the order is
    no longer being booked by it."""
    if state in (BookState.WAITING, BookState.BOOKING):
        raise ValueError(f"A booking can't end {state}.")
    order = _booking_order(session, job_id)
    if order is None:
        return None
    _settle(session, order, state, booking, issue)
    return order


def job_failed(session: Session, job_id: UUID, error: str) -> GroceryOrder | None:
    """A job failed: if it was booking an order, once Place order was pressed,
    someone checks whether Instacart has the order; before, nothing was ordered.
    None if it wasn't booking one."""
    order = _booking_order(session, job_id, expected=False)
    if order is None:
        return None
    if order.clicked_at is not None:
        issue = BookIssue(
            message="Place order was pressed, but booking stopped before Instacart confirmed it."
        )
        _settle(session, order, BookState.CHECK_INSTACART, issue=issue)
    else:
        _settle(session, order, BookState.NOT_BOOKED, issue=BookIssue(message=error))
    return order


# What members do with an approved order.


def _locked_placed(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder:
    from castle_chef.orders import OrderNotFound

    order = session.exec(
        select(GroceryOrder)
        .where(GroceryOrder.id == order_id, GroceryOrder.household_id == household_id)
        .with_for_update()
    ).first()
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status != GroceryOrderStatus.PLACED:
        session.rollback()
        raise NotChangeable("This order hasn't been placed.")
    return order


def _refuse_unless(order: GroceryOrder, session: Session, *states: BookState) -> None:
    if order.book_state in states:
        return
    session.rollback()
    if order.book_state == BookState.BOOKING:
        raise NotChangeable("It's being booked at the store right now.")
    if order.book_state == BookState.BOOKED:
        raise NotChangeable("It's booked at the store: change it in Instacart.")
    if order.book_state == BookState.CHECK_INSTACART:
        raise NotChangeable("Say first whether Instacart has it.")
    raise NotChangeable("It can't be changed now.")


def change(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder:
    """Make an approved order not booked yet the household's draft again (to go
    through the flow and approve it again): only while the following order has
    no draft."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(order, session, *UNBOOKED)
    draft = session.exec(
        select(GroceryOrder.id).where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.DRAFT,
        )
    ).first()
    if draft is not None:
        session.rollback()
        raise NotChangeable(
            "The next order has been started, so this one can only be cancelled now."
        )
    order.status, order.placed_at, order.placed_by = GroceryOrderStatus.DRAFT, None, None
    order.book_state, order.quote_id, order.book_issue = None, None, None
    session.add(order)
    session.commit()
    session.refresh(order)
    log.info("grocery_order.reopened", extra={"grocery_order_id": str(order.id)})
    return order


def cancel(session: Session, household_id: UUID, order_id: UUID) -> None:
    """Cancel an approved order not booked yet: it's deleted, like a discarded draft."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(order, session, *UNBOOKED)
    session.delete(order)
    session.commit()
    log.info("grocery_order.cancelled", extra={"grocery_order_id": str(order_id)})


def book_again(session: Session, household_id: UUID, order_id: UUID) -> Job:
    """Try booking again after it wasn't booked (the caller dispatches the job)."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(order, session, BookState.NOT_BOOKED)
    return start(session, order)


def settle(session: Session, household_id: UUID, order_id: UUID, placed: bool) -> GroceryOrder:
    """A member says whether Instacart has an order that booking couldn't confirm:
    booked (with nothing confirmed), or not booked (and it may be booked again)."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(order, session, BookState.CHECK_INSTACART)
    if placed:
        _settle(
            session,
            order,
            BookState.BOOKED,
            booking=Booking(order_number=None, window=None, total_cents=None),
        )
    else:
        order.clicked_at = None
        _settle(
            session,
            order,
            BookState.NOT_BOOKED,
            issue=BookIssue(message="It wasn't placed at Instacart."),
        )
    session.refresh(order)
    return order
