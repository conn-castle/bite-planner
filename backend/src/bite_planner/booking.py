"""Booking approved orders at the store (spec/requirements.md "Shopping list",
stage 3; spec/README.md "Delivery, booking, and tip").

An approved order (status `placed`) waits until four days before its delivery,
when Instacart has offered its day for a day, and books at 9:00 AM store time
(a scheduled run, repeated each hour to 9 PM so a run that failed is made up),
or right away when approved after that. One still waiting two hours after its
time is shown as not booked, so a member can try again. A `book` job
fills the cart, has the agent choose the window and read checkout in the browser,
checks it, and presses Place order once (bite_planner.worker). Where the order is
with the store is its `book_state`; what happens next is said to whoever approved
it (bite_planner.notifications).

Place order is pressed at most once per order: the worker records the click
(`clicked_at`) before pressing it, and nothing books an order that has one,
unless a member says it wasn't placed after all.
"""

import logging
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

from bite_planner_agent.checkout import ClickRequest
from sqlalchemy import literal, or_, tuple_
from sqlmodel import Session, col, func, select, update

from bite_planner import delivery_decisions, orders
from bite_planner.checkout import Booking, BookIssue
from bite_planner.db import for_update
from bite_planner.jobs import STALE_ERROR, fail_if_stale
from bite_planner.models import (
    BookState,
    DeliveryDecisionKind,
    GroceryOrder,
    GroceryOrderStatus,
    Job,
    JobKind,
    JobStatus,
    utc_now,
)

log = logging.getLogger(__name__)

# An order books this many days before its delivery (Instacart first offers a
# day five days ahead, at midnight), at this hour of the store's day.
BOOK_DAYS_BEFORE = 4
BOOK_HOUR = 9
# An order still waiting this long after its booking time is late: the hourly run
# after the one that failed has had its turn, so something is wrong that waiting
# won't fix, and a member is shown it as not booked, to try again (bite_planner.api).
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
    return orders.delivery_now()


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
        approved = session.get(Job, order.quote_id) if order.quote_id else None
        availability = approved.quote.availability if approved and approved.quote else None
        from bite_planner.quotes import availability_fresh

        if (
            not availability_fresh(approved.quote if approved else None, now)
            or availability is None
            or not any(
                day.day == order.delivery_date and day.windows
                for day in availability.remaining(now)
            )
        ):
            return None
    return start(session, order)


def start_due(session: Session, now: datetime) -> list[UUID]:
    """The hourly run: start booking every waiting order whose day has come, and
    surface stale jobs as failed or uncertain. Staleness never proves that the
    worker stopped or that a purchase failed. Returns the book jobs to dispatch."""
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
            settled = job_failed(session, job.id, job.error or STALE_ERROR)
            if settled is not None:
                from bite_planner.notifications import notify_booking

                notify_booking(settled.id)
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


def claim_click(session: Session, job_id: UUID, request: ClickRequest) -> bool:
    """Record that this job is about to press Place order, and commit: false if
    it mustn't (the order isn't being booked by it, or was clicked before, or the
    job is no longer running)."""
    now = store_now()
    if (
        request.window.end > request.by
        or datetime.combine(request.day, request.window.start, now.tzinfo) <= now
        or datetime.combine(request.day, request.by, now.tzinfo) <= now
    ):
        session.rollback()
        return False
    running = (
        select(Job.id)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.RUNNING,
            col(Job.worker_task_arn).is_not(None),
            col(Job.worker_stop).is_(None),
        )
        .exists()
    )
    claimed = session.scalars(
        update(GroceryOrder)
        .where(
            col(GroceryOrder.book_job_id) == job_id,
            col(GroceryOrder.book_state) == BookState.BOOKING,
            col(GroceryOrder.clicked_at).is_(None),
            col(GroceryOrder.delivery_date) == request.day,
            col(GroceryOrder.delivery_by) == request.by,
            # Not failed (as stale) meanwhile: its outcome would go unheard.
            running,
        )
        .values(clicked_at=func.now(), click_job_id=job_id)
        .returning(GroceryOrder)
    ).first()
    if claimed is not None:
        delivery_decisions.record(
            session,
            claimed,
            DeliveryDecisionKind.AUTHORIZED,
            job_id=job_id,
            window=request.window,
            authorized_total_cents=request.total_cents,
        )
    session.commit()
    if claimed is not None:
        log.info(
            "booking.clicking",
            extra={
                "job_id": str(job_id),
                "delivery_date": request.day,
                "delivery_by": request.by,
                "window": request.window.model_dump(mode="json"),
                "total_cents": request.total_cents,
            },
        )
    return claimed is not None


def _settle(
    session: Session,
    order: GroceryOrder,
    state: BookState,
    booking: Booking | None = None,
    issue: BookIssue | None = None,
) -> None:
    job_id = order.book_job_id
    order.book_state, order.book_job_id = state, None
    order.booking, order.book_issue = booking, issue
    session.add(order)
    kind = (
        DeliveryDecisionKind.PROPOSED
        if issue and issue.proposal
        else DeliveryDecisionKind.UNCONFIRMED
        if state == BookState.CHECK_INSTACART
        else DeliveryDecisionKind.BOOKED
        if state == BookState.BOOKED
        else DeliveryDecisionKind.FAILED
    )
    delivery_decisions.record(session, order, kind, job_id=job_id or order.click_job_id)
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
        extra={
            "grocery_order_id": str(order.id),
            "job_id": str(job_id) if job_id else None,
            "state": state,
            "issue": issue and issue.message,
            "delivery_date": order.delivery_date,
            "delivery_by": order.delivery_by,
            "proposal": issue.proposal.model_dump(mode="json")
            if issue and issue.proposal
            else None,
        }
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
    if order.clicked_at is not None and (
        state != BookState.BOOKED or booking is None or booking.order_number in (None, DRY_RUN)
    ):
        log.error(
            "booking.unconfirmed_authorization",
            extra={"job_id": str(job_id), "local_state": state, "clicked_at": order.clicked_at},
        )
        state, booking = BookState.CHECK_INSTACART, None
        issue = BookIssue(
            message="Place order may have been pressed, but the purchase is unconfirmed."
        )
    elif (
        state == BookState.BOOKED
        and booking is not None
        and booking.order_number not in (None, DRY_RUN)
        and order.clicked_at is None
    ):
        log.error("booking.unexpected_confirmation", extra={"job_id": str(job_id)})
        state, booking = BookState.CHECK_INSTACART, None
        issue = BookIssue(
            message="A purchase was reported without its authorization. Check Instacart."
        )
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
            message="Place order may have been pressed, "
            "but booking stopped before Instacart confirmed it."
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
        if order.clicked_at is not None and order.book_state != BookState.CHECK_INSTACART:
            session.rollback()
            raise NotChangeable("A purchase may have been attempted. Check Instacart first.")
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
    order.accepted_delivery_proposal_id = None
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
        _refuse_unless(session, order, BookState.NOT_BOOKED, BookState.NEEDS_TIME)
    return start(session, order)


def approve_delivery(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    proposal_id: UUID,
    next_delivery: date,
    member_id: UUID,
) -> tuple[GroceryOrder, Job | None]:
    """Explicitly approve exactly one persisted alternative, under the order lock."""
    order = _locked_placed(session, household_id, order_id)
    if order.accepted_delivery_proposal_id == proposal_id:
        session.rollback()
        return order, None
    _refuse_unless(session, order, BookState.NEEDS_TIME)
    issue = order.book_issue
    proposal = issue.proposal if issue else None
    if proposal is None or proposal.id != proposal_id:
        session.rollback()
        raise NotChangeable("That delivery proposal changed. Review the current proposal.")
    now = store_now()
    if datetime.combine(proposal.day, proposal.window.start, now.tzinfo) <= now:
        session.rollback()
        raise NotChangeable("That delivery window has passed. Check times again.")
    from bite_planner_agent.windows import AVAILABILITY_MAX_AGE

    if not timedelta(0) <= now - proposal.observed_at <= AVAILABILITY_MAX_AGE:
        session.rollback()
        raise NotChangeable("Those delivery times are out of date. Check times again.")
    if next_delivery <= proposal.day:
        session.rollback()
        raise NotChangeable("Choose a next delivery date after the proposed delivery.")
    previous = {
        "delivery_date": order.delivery_date,
        "delivery_by": order.delivery_by,
        "next_delivery_date": order.next_delivery_date,
    }
    order.delivery_date, order.delivery_by = proposal.day, proposal.window.end
    order.next_delivery_date = next_delivery
    order.accepted_delivery_proposal_id = proposal.id
    session.add(order)
    delivery_decisions.record(
        session,
        order,
        DeliveryDecisionKind.REAPPROVED,
        user_id=member_id,
        proposal_id=proposal.id,
        window=proposal.window,
    )
    # start commits these exact dates and the proposal id together with the job.
    job = start(session, order)
    log.info(
        "delivery.reapproved",
        extra={
            "grocery_order_id": str(order.id),
            "user_id": str(member_id),
            "job_id": str(job.id),
            "proposal_id": str(proposal.id),
            "previous": previous,
            "delivery_date": proposal.day,
            "delivery_by": proposal.window.end,
            "next_delivery_date": next_delivery,
        },
    )
    return order, job


def defer_delivery(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    proposal_id: UUID,
    member_id: UUID,
) -> GroceryOrder:
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(session, order, BookState.NEEDS_TIME)
    issue = order.book_issue
    if issue is None or issue.proposal is None or issue.proposal.id != proposal_id:
        session.rollback()
        raise NotChangeable("That delivery proposal changed. Review the current proposal.")
    if issue.deferred_at is None:
        order.book_issue = issue.model_copy(update={"deferred_at": utc_now()})
        session.add(order)
        delivery_decisions.record(session, order, DeliveryDecisionKind.DEFERRED, user_id=member_id)
        session.commit()
        log.info(
            "delivery.deferred",
            extra={
                "grocery_order_id": str(order.id),
                "user_id": str(member_id),
                "proposal_id": str(proposal_id),
            },
        )
    else:
        session.rollback()
    return order


def can_confirm_absent(session: Session, order: GroceryOrder) -> bool:
    """AWS stopped the exact task that received this order's purchase authority."""
    if order.book_state != BookState.CHECK_INSTACART or order.clicked_at is None:
        return False
    job = session.get(Job, order.click_job_id) if order.click_job_id else None
    return (
        job is not None
        and job.worker_task_arn is not None
        and job.worker_stop is not None
        and job.grocery_order_id == order.id
        and job.kind == JobKind.BOOK
        and job.worker_stop.event.detail.task_arn == job.worker_task_arn
    )


def settle(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    placed: bool,
    member_id: UUID,
) -> GroceryOrder:
    """A member says whether Instacart has an order that booking couldn't confirm:
    booked (with nothing confirmed), or not booked (and it may be booked again)."""
    order = _locked_placed(session, household_id, order_id)
    _refuse_unless(session, order, BookState.CHECK_INSTACART)
    if placed:
        delivery_decisions.record(
            session,
            order,
            DeliveryDecisionKind.CONFIRMED_PRESENT,
            user_id=member_id,
            job_id=order.click_job_id,
        )
        _settle(
            session,
            order,
            BookState.BOOKED,
            booking=Booking(order_number=None, window=None, total_cents=None),
        )
    else:
        if not can_confirm_absent(session, order):
            session.rollback()
            log.error(
                "booking.stop_proof_missing",
                extra={"grocery_order_id": str(order.id), "job_id": str(order.click_job_id)},
            )
            raise NotChangeable(
                "Waiting for AWS to confirm that the booking worker stopped. "
                "Check Instacart; rebooking remains blocked."
            )
        if order.click_job_id is None:
            raise RuntimeError("Verified purchase attempt is missing its job ID.")
        attempt = session.get_one(Job, order.click_job_id)
        delivery_decisions.record(
            session,
            order,
            DeliveryDecisionKind.CONFIRMED_ABSENT,
            user_id=member_id,
            job_id=attempt.id,
            worker_stop=attempt.worker_stop,
        )
        order.clicked_at = None
        order.click_job_id = None
        _settle(
            session,
            order,
            BookState.NOT_BOOKED,
            issue=BookIssue(message="It wasn't placed at Instacart."),
        )
    session.refresh(order)
    return order
