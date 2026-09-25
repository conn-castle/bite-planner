"""Grocery orders: a household's draft, its suggestions and picks, its dates, and
placing it. Every write to a draft locks its row first, so concurrent requests
from the household's members apply one at a time."""

import logging
from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, func, select

from castle_chef.models import (
    GroceryOrder,
    GroceryOrderMeal,
    GroceryOrderStatus,
    Job,
    Meal,
)

log = logging.getLogger(__name__)


class OrderNotFound(Exception):
    pass


class OrderPlaced(Exception):
    """A placed order can't be changed."""


class NotASuggestion(Exception):
    pass


class InvalidDates(Exception):
    """The dates break a rule; the message says which, for the member."""


class NotReady(Exception):
    """The draft can't be placed yet; the message says why, for the member."""


def earliest_delivery_date(today: date) -> date:
    """The soonest delivery date a member can choose. Later this asks the grocery
    service for its soonest slot."""
    return today + timedelta(days=1)


def place_with_grocer(order: GroceryOrder) -> None:
    """Place the order with the grocery service. A stub for now: it does nothing."""
    log.info("grocer.placed_stub", extra={"grocery_order_id": str(order.id)})


def get_draft(session: Session, household_id: UUID) -> GroceryOrder | None:
    return session.exec(
        select(GroceryOrder).where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.DRAFT,
        )
    ).first()


def get_or_create_draft(
    session: Session, household_id: UUID, member_id: UUID
) -> tuple[GroceryOrder, bool]:
    """The household's draft, created if it has none, and committed. Returns
    (draft, created). Two members opening the flow at once get the same draft."""
    created_id = session.execute(
        insert(GroceryOrder)
        .values(
            id=uuid4(),
            household_id=household_id,
            created_by=member_id,
            status=GroceryOrderStatus.DRAFT,
        )
        .on_conflict_do_nothing(
            index_elements=["household_id"], index_where=text("status = 'draft'")
        )
        .returning(col(GroceryOrder.id))
    ).scalar_one_or_none()
    session.commit()
    draft = get_draft(session, household_id)
    if draft is None:  # discarded between the insert and the read
        raise OrderNotFound("The draft was discarded; try again.")
    if created_id is not None:
        log.info(
            "grocery_order.created",
            extra={"grocery_order_id": str(draft.id), "household_id": str(household_id)},
        )
    return draft, created_id is not None


def get_order(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder:
    """The household's order; another household's is indistinguishable from none."""
    order = session.get(GroceryOrder, order_id)
    if order is None or order.household_id != household_id:
        raise OrderNotFound("No such order.")
    return order


def lock_order(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder | None:
    """The household's order, locked for this transaction, or None."""
    order = session.exec(
        select(GroceryOrder)
        .where(GroceryOrder.id == order_id, GroceryOrder.household_id == household_id)
        .with_for_update()
    ).first()
    return order


def lock_draft(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder:
    """The household's draft, locked for this transaction."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status != GroceryOrderStatus.DRAFT:
        session.rollback()
        raise OrderPlaced("This order has been placed and can't be changed.")
    return order


def discard(session: Session, household_id: UUID, order_id: UUID) -> None:
    """Delete a draft, with its picks and jobs (their meals stay). Discarding one
    that's already gone does nothing."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        return
    if order.status != GroceryOrderStatus.DRAFT:
        session.rollback()
        raise OrderPlaced("This order has been placed and can't be discarded.")
    session.delete(order)
    session.commit()
    log.info("grocery_order.discarded", extra={"grocery_order_id": str(order_id)})


def pick(session: Session, household_id: UUID, order_id: UUID, meal_id: UUID) -> None:
    """Add one of the draft's suggestions to it."""
    lock_draft(session, household_id, order_id)
    suggested = session.exec(
        select(Meal.id)
        .join(Job, col(Job.id) == Meal.job_id)
        .where(Meal.id == meal_id, Job.grocery_order_id == order_id)
    ).first()
    if suggested is None:
        session.rollback()
        raise NotASuggestion("That meal isn't one of this order's suggestions.")
    session.execute(
        insert(GroceryOrderMeal)
        .values(grocery_order_id=order_id, meal_id=meal_id)
        .on_conflict_do_nothing()
    )
    session.commit()


def unpick(session: Session, household_id: UUID, order_id: UUID, meal_id: UUID) -> None:
    lock_draft(session, household_id, order_id)
    session.execute(
        delete(GroceryOrderMeal).where(
            col(GroceryOrderMeal.grocery_order_id) == order_id,
            col(GroceryOrderMeal.meal_id) == meal_id,
        )
    )
    session.commit()


def check_dates(delivery: date | None, next_delivery: date | None, today: date) -> None:
    """Raise InvalidDates if the dates break a rule. Either may still be unset."""
    earliest = earliest_delivery_date(today)
    if delivery is not None and delivery < earliest:
        raise InvalidDates(f"The delivery date can't be before {earliest.isoformat()}.")
    if next_delivery is not None and delivery is None:
        raise InvalidDates("Choose the delivery date before the next delivery date.")
    if next_delivery is not None and delivery is not None and next_delivery <= delivery:
        raise InvalidDates("The next delivery date must be after the delivery date.")


def set_dates(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    delivery: date | None,
    next_delivery: date | None,
    today: date,
) -> None:
    order = lock_draft(session, household_id, order_id)
    try:
        check_dates(delivery, next_delivery, today)
    except InvalidDates:
        session.rollback()
        raise
    order.delivery_date, order.next_delivery_date = delivery, next_delivery
    session.add(order)
    session.commit()


def place(session: Session, household_id: UUID, order_id: UUID, today: date) -> GroceryOrder:
    """Place a draft: check it, send it to the grocery service, and mark it placed.
    Placing an order that's already placed returns it unchanged, so a retry after
    a lost response is safe."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status == GroceryOrderStatus.PLACED:
        session.rollback()
        return order
    missing = _not_ready(session, order, today)
    if missing is not None:
        session.rollback()
        raise NotReady(missing)
    place_with_grocer(order)
    order.status = GroceryOrderStatus.PLACED
    session.add(order)
    session.commit()
    session.refresh(order)
    log.info("grocery_order.placed", extra={"grocery_order_id": str(order.id)})
    return order


def _not_ready(session: Session, order: GroceryOrder, today: date) -> str | None:
    if not picks(session, order.id):
        return "Pick at least one meal."
    if order.delivery_date is None:
        return "Choose the delivery date."
    if order.next_delivery_date is None:
        return "Choose the next delivery date."
    try:
        check_dates(order.delivery_date, order.next_delivery_date, today)
    except InvalidDates as error:
        return str(error)
    return None


# Computed, not stored.


def picks(session: Session, order_id: UUID) -> list[UUID]:
    """The order's picked meals, in the order they were suggested."""
    return list(
        session.exec(
            select(Meal.id)
            .join(GroceryOrderMeal, col(GroceryOrderMeal.meal_id) == Meal.id)
            .where(GroceryOrderMeal.grocery_order_id == order_id)
            .order_by(col(Meal.created_at), col(Meal.id))
        ).all()
    )


def jobs(session: Session, order_id: UUID) -> list[Job]:
    """The order's jobs, oldest first."""
    return list(
        session.exec(
            select(Job)
            .where(Job.grocery_order_id == order_id)
            .order_by(col(Job.created_at), col(Job.id))
        ).all()
    )


def job_meals(session: Session, job_ids: list[UUID]) -> dict[UUID, list[Meal]]:
    """Each job's meals, in a fixed order."""
    meals: dict[UUID, list[Meal]] = {job_id: [] for job_id in job_ids}
    for meal in session.exec(
        select(Meal)
        .where(col(Meal.job_id).in_(job_ids))
        .order_by(col(Meal.created_at), col(Meal.id))
    ).all():
        if meal.job_id is not None:
            meals[meal.job_id].append(meal)
    return meals


def number(session: Session, order: GroceryOrder) -> int:
    """ "Order 3": its position among the household's orders, oldest first."""
    return session.exec(
        select(func.count())
        .select_from(GroceryOrder)
        .where(
            GroceryOrder.household_id == order.household_id,
            tuple_(col(GroceryOrder.created_at), col(GroceryOrder.id))
            <= tuple_(order.created_at, order.id),
        )
    ).one()


def target_date(session: Session, order: GroceryOrder) -> date | None:
    """The previous placed order's next delivery date; None for the first order."""
    return session.exec(
        select(GroceryOrder.next_delivery_date)
        .where(
            GroceryOrder.household_id == order.household_id,
            GroceryOrder.status == GroceryOrderStatus.PLACED,
            tuple_(col(GroceryOrder.created_at), col(GroceryOrder.id))
            < tuple_(order.created_at, order.id),
        )
        .order_by(col(GroceryOrder.created_at).desc(), col(GroceryOrder.id).desc())
        .limit(1)
    ).first()
