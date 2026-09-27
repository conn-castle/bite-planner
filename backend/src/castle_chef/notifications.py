"""What the app's notifications say, and when a member's daily reminder is due
(spec/requirements.md "Notifications")."""

from datetime import date, timedelta

from sqlmodel import Session, col, func, select

from castle_chef import orders
from castle_chef.models import AppUser, GroceryOrder, Job, Meal, ShoppingListItem
from castle_chef.push import Notification, NotificationKind

# How long APNs keeps one for a phone that's off or offline.
REMINDER_EXPIRY = timedelta(hours=12)
IDEAS_READY_EXPIRY = timedelta(hours=6)
LIST_READY_EXPIRY = timedelta(hours=6)


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


def list_ready(session: Session, job: Job) -> Notification:
    """For the member whose leaving Servings started a shopping list, once it's built."""
    order = session.get_one(GroceryOrder, job.grocery_order_id)
    number = orders.number(session, order)
    count = session.exec(
        select(func.count())
        .select_from(ShoppingListItem)
        .where(ShoppingListItem.job_id == job.id, col(ShoppingListItem.on_hand).is_(False))
    ).one()
    return Notification(
        kind=NotificationKind.LIST_READY,
        title="Your shopping list is ready",
        body=f"{count} item{'' if count == 1 else 's'} for Order {number}.",
        expires_in=LIST_READY_EXPIRY,
        order_id=order.id,
    )
