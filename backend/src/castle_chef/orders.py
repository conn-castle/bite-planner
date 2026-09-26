"""Grocery orders: a household's draft, its suggestions and picks, its dates, and
placing it. Every write to a draft locks its row first, so concurrent requests
from the household's members apply one at a time."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, func, select

from castle_chef import ratings
from castle_chef.models import (
    GroceryOrder,
    GroceryOrderMeal,
    GroceryOrderStatus,
    Household,
    Job,
    Meal,
    MealRating,
    utc_now,
)

log = logging.getLogger(__name__)


class OrderNotFound(Exception):
    pass


class OrderPlaced(Exception):
    """A placed order can't be changed."""


class NotASuggestion(Exception):
    pass


class MealNotFound(Exception):
    pass


class NotPicked(Exception):
    pass


class NotForUs(Exception):
    """The household marked the meal "don't show meals like this"."""


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


def lock_current_draft(session: Session, household_id: UUID) -> GroceryOrder | None:
    """The household's draft, locked for this transaction, or None if it has none."""
    return session.exec(
        select(GroceryOrder)
        .where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.DRAFT,
        )
        .with_for_update()
    ).first()


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
    # The draft's lock also orders this check with a concurrent "not for us" mark,
    # which unpicks the meal under the same lock (rate_meal).
    if ratings.is_not_for_us(session, household_id, meal_id):
        session.rollback()
        raise NotForUs("You marked this meal 'don't show meals like this'.")
    # A new pick starts at the household's default servings; picking it again
    # keeps the servings it has.
    household = session.get_one(Household, household_id)
    session.execute(
        insert(GroceryOrderMeal)
        .values(grocery_order_id=order_id, meal_id=meal_id, servings=household.default_servings)
        .on_conflict_do_nothing()
    )
    session.commit()


def set_servings(
    session: Session, household_id: UUID, order_id: UUID, meal_id: UUID, servings: int
) -> None:
    """Change how many to make of one of the draft's picks. A meal that isn't
    picked (e.g. just unpicked on another phone) is an error, never a new pick."""
    lock_draft(session, household_id, order_id)
    pick = session.get(GroceryOrderMeal, (order_id, meal_id))
    if pick is None:
        session.rollback()
        raise NotPicked("That meal isn't picked.")
    pick.servings = servings
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


def place(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    user_id: UUID,
    delivery: date,
    next_delivery: date,
    today: date,
) -> GroceryOrder:
    """Place a draft with the dates the member saw: save them, check the draft,
    send it to the grocery service, and mark it placed by the user, now; all under
    the draft's lock, so another phone's change can't land in between. Placing an
    order that's already placed returns it unchanged (its dates, who placed it and
    when included), so a retry after a lost response is safe."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status == GroceryOrderStatus.PLACED:
        session.rollback()
        return order
    missing = _not_ready(session, order, delivery, next_delivery, today)
    if missing is not None:
        session.rollback()
        raise NotReady(missing)
    order.delivery_date, order.next_delivery_date = delivery, next_delivery
    place_with_grocer(order)
    order.status = GroceryOrderStatus.PLACED
    order.placed_at = utc_now()
    order.placed_by = user_id
    session.add(order)
    session.commit()
    session.refresh(order)
    log.info("grocery_order.placed", extra={"grocery_order_id": str(order.id)})
    return order


def _not_ready(
    session: Session, order: GroceryOrder, delivery: date, next_delivery: date, today: date
) -> str | None:
    if not picks(session, order.id):
        return "Pick at least one meal."
    try:
        check_dates(delivery, next_delivery, today)
    except InvalidDates as error:
        return str(error)
    return None


# Computed, not stored.


def household_meal(
    session: Session, household_id: UUID, meal_id: UUID, *, with_ingredients: bool = False
) -> Meal:
    """One of the household's meals: a suggestion of one of its orders (every pick
    is one), with its ingredients loaded if asked. Another household's is
    indistinguishable from none, and so is one whose draft was discarded."""
    query = (
        select(Meal)
        .join(Job, col(Job.id) == Meal.job_id)
        .join(GroceryOrder, col(GroceryOrder.id) == Job.grocery_order_id)
        .where(Meal.id == meal_id, GroceryOrder.household_id == household_id)
    )
    if with_ingredients:
        query = query.options(selectinload(Meal.ingredients))  # type: ignore[arg-type]
    meal = session.exec(query).first()
    if meal is None:
        raise MealNotFound("No such meal.")
    return meal


def rate_meal(session: Session, household_id: UUID, meal_id: UUID, rating: MealRating) -> None:
    """Set the household's rating of one of its meals, replacing any earlier one.
    Not for us also unpicks it from the draft: under the draft's lock, taken
    first, so a concurrent pick (which checks the rating under the same lock)
    can't leave it picked."""
    draft = lock_current_draft(session, household_id) if rating == MealRating.NOT_FOR_US else None
    try:
        household_meal(session, household_id, meal_id)
    except MealNotFound:
        session.rollback()
        raise
    if draft is not None:
        session.execute(
            delete(GroceryOrderMeal).where(
                col(GroceryOrderMeal.grocery_order_id) == draft.id,
                col(GroceryOrderMeal.meal_id) == meal_id,
            )
        )
    ratings.write(session, household_id, meal_id, rating)
    session.commit()


def clear_meal_rating(session: Session, household_id: UUID, meal_id: UUID) -> None:
    """Set one of the household's meals back to not rated (nothing to do if it isn't)."""
    household_meal(session, household_id, meal_id)
    ratings.clear(session, household_id, meal_id)
    session.commit()


@dataclass(frozen=True)
class Pick:
    """A meal picked for an order, and how many to make of it."""

    meal: Meal
    servings: int


def picks_of(session: Session, order_ids: list[UUID]) -> dict[UUID, list[Pick]]:
    """Each order's picks, in the order their meals were suggested, in one query."""
    found: dict[UUID, list[Pick]] = {order_id: [] for order_id in order_ids}
    if not order_ids:
        return found
    for order_id, servings, meal in session.exec(
        select(GroceryOrderMeal.grocery_order_id, GroceryOrderMeal.servings, Meal)
        .join(Meal, col(Meal.id) == GroceryOrderMeal.meal_id)
        .where(col(GroceryOrderMeal.grocery_order_id).in_(order_ids))
        .order_by(col(Meal.created_at), col(Meal.id))
    ).all():
        found[order_id].append(Pick(meal=meal, servings=servings))
    return found


def picks(session: Session, order_id: UUID) -> list[Pick]:
    """The order's picks, in the order their meals were suggested."""
    return picks_of(session, [order_id])[order_id]


def placed_orders(
    session: Session, household_id: UUID, covering_from: date | None = None
) -> list[GroceryOrder]:
    """The household's placed orders, oldest first: all of them, or only those that
    cover `covering_from` or a later day."""
    query = select(GroceryOrder).where(
        GroceryOrder.household_id == household_id,
        GroceryOrder.status == GroceryOrderStatus.PLACED,
    )
    if covering_from is not None:
        query = query.where(col(GroceryOrder.next_delivery_date) > covering_from)
    return list(
        session.exec(query.order_by(col(GroceryOrder.created_at), col(GroceryOrder.id))).all()
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


def numbers(session: Session, household_id: UUID) -> dict[UUID, int]:
    """Every one of the household's orders' number (see `number`), in one query."""
    ids = session.exec(
        select(GroceryOrder.id)
        .where(GroceryOrder.household_id == household_id)
        .order_by(col(GroceryOrder.created_at), col(GroceryOrder.id))
    ).all()
    return {order_id: position for position, order_id in enumerate(ids, start=1)}


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


def next_number(session: Session, household_id: UUID) -> int:
    """The number of the household's next order: its draft's, or the one it gets when started."""
    placed = session.exec(
        select(func.count())
        .select_from(GroceryOrder)
        .where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.PLACED,
        )
    ).one()
    return placed + 1


def next_target_date(session: Session, household_id: UUID) -> date | None:
    """The target date of the household's next order: its last placed order's next
    delivery date; None before its first order."""
    return session.exec(
        select(GroceryOrder.next_delivery_date)
        .where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.PLACED,
        )
        .order_by(col(GroceryOrder.created_at).desc(), col(GroceryOrder.id).desc())
        .limit(1)
    ).first()


def decide_by_date(session: Session, household_id: UUID, target: date | None) -> date | None:
    """The day to decide an order by: its target date less the household's offset."""
    if target is None:
        return None
    household = session.get_one(Household, household_id)
    return target - timedelta(days=household.decide_by_offset_days)
