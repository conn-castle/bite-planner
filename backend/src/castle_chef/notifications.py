"""What the app's notifications say, and when a member's daily reminder is due
(spec/requirements.md "Notifications")."""

import logging
from datetime import date, timedelta
from uuid import UUID

from castle_chef_agent import windows
from sqlmodel import Session, col, func, select

from castle_chef import orders, push, quotes
from castle_chef.booking import DRY_RUN
from castle_chef.db import get_session
from castle_chef.models import (
    AppUser,
    BookState,
    GroceryOrder,
    GroceryOrderStatus,
    Job,
    JobStatus,
    Meal,
    ShoppingListItem,
)
from castle_chef.push import Notification, NotificationKind
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

# How long APNs keeps one for a phone that's off or offline.
REMINDER_EXPIRY = timedelta(hours=12)
IDEAS_READY_EXPIRY = timedelta(hours=6)
LIST_READY_EXPIRY = timedelta(hours=6)
QUOTE_READY_EXPIRY = timedelta(hours=6)
BOOKING_EXPIRY = timedelta(hours=24)


def _weekday(day: date) -> str:
    return f"{day:%A}"


def _long_date(day: date) -> str:
    """ "Sunday, Sep 27"."""
    return f"{day:%A}, {day:%b} {day.day}"


def reminder(session: Session, user: AppUser, day: date) -> Notification | None:
    """The member's reminder for `day` (their local date), or None if none is due:
    the household's next order has a target date (it has placed one before), and
    `day` is on or after its reminders start. The next order is never placed (the
    one placed becomes the last), so reminders run daily until someone places it."""
    household_id = user.household_id
    target = orders.next_target_date(session, household_id)
    decide_by = orders.decide_by_date(session, household_id, target)
    start = orders.reminders_start_date(session, household_id, decide_by)
    if target is None or decide_by is None or start is None or day < start:
        return None
    number = orders.next_number(session, household_id)
    delivery = _long_date(target)
    if day < decide_by:
        title = f"Pick meals for Order {number}"
        body = f"Decide by {_weekday(decide_by)} for delivery {delivery}."
    elif day == decide_by:
        title = "Last day to pick meals"
        body = f"Decide by today for delivery {delivery}."
    else:
        title = f"Order {number} is overdue"
        body = f"It was due {_weekday(decide_by)}. Pick meals for delivery as soon as you can."
    return Notification(
        kind=NotificationKind.REMINDER,
        title=title,
        body=body,
        expires_in=REMINDER_EXPIRY,
        # Each day's reminder replaces the one before on the phone.
        collapse_id="reminder",
        order_number=number,
    )


def ideas_ready(session: Session, job: Job) -> Notification:
    """For the member who started a batch of suggestions, once it succeeded."""
    order = session.get_one(GroceryOrder, job.grocery_order_id)
    number = orders.number(session, order)
    count = session.exec(select(func.count()).select_from(Meal).where(Meal.job_id == job.id)).one()
    decide_by = orders.decide_by_date(
        session, order.household_id, orders.target_date(session, order)
    )
    ideas = f"{count} idea{'' if count == 1 else 's'} for Order {number}."
    body = ideas if decide_by is None else f"{ideas} Pick yours by {_weekday(decide_by)}."
    return Notification(
        kind=NotificationKind.IDEAS_READY,
        title="Your meal ideas are ready",
        body=body,
        expires_in=IDEAS_READY_EXPIRY,
        order_id=order.id,
    )


def list_ready(session: Session, build: Job) -> Notification:
    """For the member whose leaving Servings started a shopping list, once it's
    built and shopped: its products to buy, and the items the store doesn't have."""
    order = session.get_one(GroceryOrder, build.grocery_order_id)
    number = orders.number(session, order)
    items = session.exec(
        select(ShoppingListItem).where(
            ShoppingListItem.job_id == build.id, col(ShoppingListItem.on_hand).is_(False)
        )
    ).all()
    count = sum(1 for item in items if item.product is not None)
    missing = sum(1 for item in items if item.not_found)
    body = f"{count} product{'' if count == 1 else 's'} for Order {number}."
    if missing:
        body += f" {missing} item{'' if missing == 1 else 's'} to decide on."
    return Notification(
        kind=NotificationKind.LIST_READY,
        title="Your shopping list is ready",
        body=body,
        expires_in=LIST_READY_EXPIRY,
        order_id=order.id,
    )


def list_failed(session: Session, build: Job) -> Notification:
    """For the member waiting for the list, when shopping it at the store failed."""
    order = session.get_one(GroceryOrder, build.grocery_order_id)
    number = orders.number(session, order)
    return Notification(
        kind=NotificationKind.LIST_FAILED,
        title="Couldn't shop your list",
        body=f"Shopping Order {number}'s list at {get_settings().instacart_store} stopped. "
        "Open it to try again.",
        expires_in=LIST_READY_EXPIRY,
        order_id=order.id,
    )


def notify_quote(job_id: UUID) -> None:
    """Tell the requester the current draft's checkout check has finished."""
    try:
        with get_session() as session:
            job = session.get(Job, job_id)
            order = session.get(GroceryOrder, job.grocery_order_id) if job else None
            current = quotes.current(session, order) if order else None
            if (
                job is None
                or order is None
                or order.status != GroceryOrderStatus.DRAFT
                or current is None
                or current.id != job.id
                or job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED)
            ):
                return
            number = orders.number(session, order)
            failed = job.status == JobStatus.FAILED
            blocked = job.quote.blocked_because if job.quote else None
            notification = Notification(
                kind=NotificationKind.QUOTE_FAILED if failed else NotificationKind.QUOTE_READY,
                title="Couldn't check delivery times"
                if failed
                else (
                    "Your checkout needs attention" if blocked else "Your delivery times are ready"
                ),
                body=f"Order {number}'s check stopped. Open it to try again."
                if failed
                else (
                    f"Order {number}: {blocked} Open it to review."
                    if blocked
                    else f"Choose a delivery for Order {number}. Its estimated total is ready too."
                ),
                expires_in=QUOTE_READY_EXPIRY,
                collapse_id=f"quote-{order.id}",
                order_id=order.id,
            )
            requester = job.requested_by
        push.notify(requester, notification)
    except Exception:
        log.exception("job.notify_failed", extra={"job_id": str(job_id)})


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def booking(session: Session, order: GroceryOrder) -> Notification | None:
    """For whoever approved the order, once booking it has gone one way or
    another; None while it's waiting or under way."""
    number = orders.number(session, order)
    issue = order.book_issue.message if order.book_issue else ""
    store = get_settings().instacart_store
    if order.book_state == BookState.BOOKED and order.booking is not None:
        booked = order.booking
        when = _long_date(order.delivery_date) if order.delivery_date else "the delivery day"
        if booked.window is not None:
            when += f", {windows.label(booked.window)}"
        title = f"Order {number} is booked"
        if booked.order_number == DRY_RUN:
            title += " (dry run)"
        body = f"Arrives {when}."
        if booked.total_cents is not None:
            body += f" {_money(booked.total_cents)} at {store}."
        for swap in booked.swaps:
            body += f" {swap.gone} was gone, so it's {swap.bought} instead."
    elif order.book_state == BookState.NEEDS_TIME:
        title, body = f"Order {number} needs a new time", f"{issue} Open it to choose another."
        proposal = order.book_issue.proposal if order.book_issue else None
        if proposal is not None:
            body = (
                f"Nothing was ordered. Available: {_long_date(proposal.day)}, "
                f"{windows.label(proposal.window)}. Open to approve or decide later."
            )
    elif order.book_state == BookState.NEEDS_PRODUCT:
        title, body = f"Order {number} needs a product chosen", f"{issue} Open it to choose."
    elif order.book_state == BookState.NOT_BOOKED:
        title, body = f"Order {number} wasn't booked", f"{issue} Nothing was ordered."
    elif order.book_state == BookState.CHECK_INSTACART:
        title = f"Check Instacart for Order {number}"
        body = f"{issue} Check your Instacart orders before attempting another purchase."
    else:
        return None
    return Notification(
        kind=NotificationKind.BOOKING,
        title=title,
        body=body,
        expires_in=BOOKING_EXPIRY,
        # The latest word on the order replaces the one before.
        collapse_id=f"booking-{order.id}",
        order_id=order.id,
    )


def notify_booking(order_id: UUID) -> None:
    """Tell whoever approved the order how booking it went. A failure here is
    logged as an error; the booking stands."""
    try:
        with get_session() as session:
            order = session.get(GroceryOrder, order_id)
            notification = booking(session, order) if order else None
            approver = order.placed_by if order else None
        if notification is not None and approver is not None:
            push.notify(approver, notification)
    except Exception:
        log.exception("booking.notify_failed", extra={"grocery_order_id": str(order_id)})
