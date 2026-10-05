"""Booking approved orders at the store (spec/requirements.md "Shopping list",
stage 3; spec/README.md "Delivery, booking, and tip").

An approved order (status `placed`) waits until four days before its delivery,
when Instacart has offered its day for a day, and books at 9:00 AM store time
(a scheduled run, repeated each hour to 9 PM so a run that failed is made up),
or right away when approved after that. One still waiting two hours after its
time is shown as not booked, so a member can try again. A `book` job
fills the cart, has the agent choose the window and read checkout in the browser,
checks it, and presses Place order once (castle_chef.worker). Where the order is
with the store is its `book_state`; what happens next is said to whoever approved
it (castle_chef.notifications).

Place order is pressed at most once per order: the worker records the click
(`clicked_at`) before pressing it, and nothing books an order that has one,
unless a member says it wasn't placed after all.
"""

import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import literal, or_, tuple_
from sqlmodel import Session, col, func, select, update

from castle_chef import orders
from castle_chef.checkout import Booking, BookIssue
from castle_chef.db import for_update
from castle_chef.jobs import STALE_ERROR, fail_if_stale
from castle_chef.models import (
    BookState,
    GroceryOrder,
    GroceryOrderStatus,
    Job,
    JobKind,
    JobStatus,
)
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

# An order books this many days before its delivery (Instacart first offers a
# day five days ahead, at midnight), at this hour of the store's day.
BOOK_DAYS_BEFORE = 4
BOOK_HOUR = 9
# An order still waiting this long after its booking time is late: the hourly run
# after the one that failed has had its turn, so something is wrong that waiting
# won't fix, and a member is shown it as not booked, to try again (castle_chef.api).
LATE_AFTER = timedelta(hours=2)

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
    its day has come and that day's first run has been (an order approved before
    9:00 AM that day waits for it)."""
    day = book_on(delivery)
    return now.date() > day or (now.date() == day and now.hour >= BOOK_HOUR)


def is_late(order: GroceryOrder, now: datetime) -> bool:
    """Whether an approved order is still waiting LATE_AFTER past its booking time
    (its book day at BOOK_HOUR, store time; `now` in the store's time zone)."""
    if order.book_state != BookState.WAITING or order.delivery_date is None:
        return False
    book_at = datetime.combine(book_on(order.delivery_date), time(BOOK_HOUR), now.tzinfo)
    return now >= book_at + LATE_AFTER


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
    """The hourly run: start booking every waiting order whose day has come, and
    settle bookings whose job stopped without saying how it went (its worker was
    lost, and can no longer be running). Returns the book jobs to dispatch."""
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
    booking = session.exec(
        select(GroceryOrder.book_job_id).where(GroceryOrder.book_state == BookState.BOOKING)
    ).all()
    for job_id in booking:
        if job_id is None:
            continue
        if fail_if_stale(session, job_id):
            log.error("booking.job_stale", extra={"job_id": str(job_id)})
        job = session.get(Job, job_id)
        if job is not None and job.status == JobStatus.FAILED:
            job_failed(session, job.id, job.error or STALE_ERROR)
    return started


def _lock(session: Session, order_id: UUID) -> GroceryOrder | None:
    return session.exec(for_update(select(GroceryOrder).where(GroceryOrder.id == order_id))).first()


def _booking_order(session: Session, job_id: UUID, expected: bool = True) -> GroceryOrder | None:
    """The order this job is booking, locked; None if it's no longer booking it
    (cancelled, or settled meanwhile), or isn't a book job (`expected` false:
    nothing to warn about)."""
    order = session.exec(
        for_update(
            select(GroceryOrder).where(
                GroceryOrder.book_job_id == job_id, GroceryOrder.book_state == BookState.BOOKING
            )
        )
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
    claimed = session.exec(
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
    # What Instacart confirmed, when it did: which order, when, and what it charged.
    confirmed: dict[str, object] = (
        {
            "instacart_order": booking.order_number,
            "window": booking.window and booking.window.model_dump(mode="json"),
            "total_cents": booking.total_cents,
            "swaps": len(booking.swaps),
        }
        if booking is not None
        else {}
    )
    log.info(
        "booking.settled",
        extra={"grocery_order_id": str(order.id), "state": state, "issue": issue and issue.message}
        | confirmed,
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
    order = orders.lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise orders.OrderNotFound("No such order.")
    if order.status != GroceryOrderStatus.PLACED:
        session.rollback()
        raise NotChangeable("This order hasn't been placed.")
    return order


def _refuse_unless(session: Session, order: GroceryOrder, *states: BookState) -> None:
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
    through the flow and approve it again): only while it's the household's newest
    order. Once the next order is started, that's the draft; once it's placed, this
    one as the draft would come before an order already placed (decided 2026-10-04)."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(session, order, *UNBOOKED)
    # One read: an order being placed meanwhile is seen as the draft or as placed.
    following = session.exec(
        select(col(GroceryOrder.status)).where(
            GroceryOrder.household_id == household_id,
            or_(
                col(GroceryOrder.status) == GroceryOrderStatus.DRAFT,
                tuple_(col(GroceryOrder.created_at), col(GroceryOrder.id))
                > tuple_(literal(order.created_at), literal(order.id)),
            ),
        )
    ).all()
    if GroceryOrderStatus.DRAFT in following:
        session.rollback()
        raise NotChangeable(
            "The next order has been started, so this one can only be cancelled now."
        )
    if following:
        session.rollback()
        raise NotChangeable(
            "The next order has been placed, so this one can only be cancelled now."
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
    _refuse_unless(session, order, *UNBOOKED)
    session.delete(order)
    session.commit()
    log.info("grocery_order.cancelled", extra={"grocery_order_id": str(order_id)})


def book_again(session: Session, household_id: UUID, order_id: UUID) -> Job:
    """Try booking again after it wasn't booked, or when it's late (shown as not
    booked): a waiting order is booked once it's due, as the run would (the caller
    dispatches the job). Whichever of this and the run locks the order first starts
    it; the other finds it being booked."""
    order = _locked_placed(session, household_id, order_id)
    due = (
        order.book_state == BookState.WAITING
        and order.delivery_date is not None
        and is_due(order.delivery_date, store_now())
    )
    if not due:
        _refuse_unless(session, order, BookState.NOT_BOOKED)
    return start(session, order)


def settle(session: Session, household_id: UUID, order_id: UUID, placed: bool) -> GroceryOrder:
    """A member says whether Instacart has an order that booking couldn't confirm:
    booked (with nothing confirmed), or not booked (and it may be booked again)."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(session, order, BookState.CHECK_INSTACART)
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
