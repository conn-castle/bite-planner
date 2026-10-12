"""Grocery orders: a household's draft, its suggestions and picks, its dates, and
placing it. Every write to a draft locks its row first, so concurrent requests
from the household's members apply one at a time.

The order lock: only one ordering session (one opening of the create-order flow,
on one phone) changes the draft at a time. Opening the flow takes the lock when
no one else has a live one; the app renews it every 30 seconds, and it expires 2
minutes after the last renewal. Changes are accepted only from the session that
has it."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, literal, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, func, select

from castle_chef import delivery, products, quotes, ratings, shopping
from castle_chef.db import for_update
from castle_chef.jobs import shop_of
from castle_chef.models import (
    BookState,
    GroceryOrder,
    GroceryOrderMeal,
    GroceryOrderStatus,
    Household,
    Job,
    JobStatus,
    Meal,
    MealRating,
    OrderStep,
    ShoppingListItem,
    utc_now,
)
from castle_chef.settings import get_settings

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


class ItemNotFound(Exception):
    pass


class NoAmount(Exception):
    """The item has no amount to change (its recipes give none)."""


class NoSuchProduct(Exception):
    """The item has no such product (or no product) to change."""


class ListNotCurrent(Exception):
    """The shopping list isn't ready for the order's picks; the message says why."""


class OrderLocked(Exception):
    """Another ordering session has the draft's lock (or this one no longer does)."""


# A lock expires this long after its last renewal (the app renews every 30 s).
LOCK_TTL = timedelta(minutes=2)


@dataclass(frozen=True)
class Orderer:
    """A member making changes in one ordering session: one opening of the flow,
    on one phone, identified by an id the app chooses."""

    user_id: UUID
    session: UUID


def lock_expires_at(order: GroceryOrder) -> datetime | None:
    """When the order's lock expires, or None if no one has taken it."""
    return None if order.lock_renewed_at is None else order.lock_renewed_at + LOCK_TTL


def has_live_lock(order: GroceryOrder, now: datetime) -> bool:
    expires = lock_expires_at(order)
    return expires is not None and expires > now


def _refuse(order: GroceryOrder, user_id: UUID, now: datetime) -> OrderLocked:
    if has_live_lock(order, now) and order.locked_by != user_id:
        return OrderLocked("Someone else in your household is placing this order.")
    if has_live_lock(order, now):
        return OrderLocked("You're placing this order on another phone.")
    return OrderLocked("This order isn't open here any more. Open it again.")


def _take(order: GroceryOrder, orderer: Orderer, step: OrderStep, now: datetime) -> bool:
    """Give the lock to the orderer's session if no one else has a live one; its
    own member's other session (another phone, or this one before a relaunch)
    gives way. Returns whether it did."""
    if has_live_lock(order, now) and order.locked_by != orderer.user_id:
        return False
    if order.lock_session != orderer.session:
        order.locked_at = now
    order.locked_by, order.lock_session = orderer.user_id, orderer.session
    order.lock_renewed_at, order.lock_step = now, step
    _reach(order, step)
    return True


def _reach(order: GroceryOrder, step: OrderStep) -> None:
    """Note a step the flow is at, if it's further than the order has gone."""
    flow = list(OrderStep)
    if flow.index(step) > flow.index(order.reached_step):
        order.reached_step = step


def _check_session(session: Session, order: GroceryOrder, orderer: Orderer) -> None:
    """Raise OrderLocked (rolling back) unless the orderer's session has the lock:
    live, or expired with no one having taken it since (a phone that slept a
    while carries on)."""
    if order.lock_session == orderer.session and order.locked_by == orderer.user_id:
        return
    session.rollback()
    raise _refuse(order, orderer.user_id, utc_now())


def _clear_lock(order: GroceryOrder) -> None:
    order.locked_by = order.lock_session = order.locked_at = None
    order.lock_renewed_at = order.lock_step = None


def release_locks_of(session: Session, household_id: UUID, user_id: UUID) -> None:
    """Stage releasing the order locks a member of the household has (the caller
    commits)."""
    for order in session.exec(
        for_update(
            select(GroceryOrder).where(
                GroceryOrder.household_id == household_id, GroceryOrder.locked_by == user_id
            )
        )
    ).all():
        _clear_lock(order)
        session.add(order)


def earliest_delivery_date(today: date) -> date:
    """The soonest delivery date a member can choose. Later this asks the grocery
    service for its soonest slot."""
    return today + timedelta(days=1)


def get_draft(session: Session, household_id: UUID) -> GroceryOrder | None:
    return session.exec(
        select(GroceryOrder).where(
            GroceryOrder.household_id == household_id,
            GroceryOrder.status == GroceryOrderStatus.DRAFT,
        )
    ).first()


def get_or_create_draft(
    session: Session, household_id: UUID, orderer: Orderer
) -> tuple[GroceryOrder, bool]:
    """Open the household's draft: created if it has none, its lock taken for the
    orderer's session unless someone else has it (the draft then shows who), and
    committed. Returns (draft, created). Two members opening the flow at once get
    the same draft, and one of them the lock."""
    created_id = session.execute(
        insert(GroceryOrder)
        .values(
            id=uuid4(),
            household_id=household_id,
            created_by=orderer.user_id,
            status=GroceryOrderStatus.DRAFT,
        )
        .on_conflict_do_nothing(
            index_elements=["household_id"], index_where=text("status = 'draft'")
        )
        .returning(col(GroceryOrder.id))
    ).scalar_one_or_none()
    session.commit()
    draft = lock_current_draft(session, household_id)
    if draft is None:  # discarded between the insert and the read
        session.rollback()
        raise OrderNotFound("The draft was discarded; try again.")
    _take(draft, orderer, OrderStep.PICK, utc_now())
    session.add(draft)
    session.commit()
    session.refresh(draft)
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
    return session.exec(
        for_update(
            select(GroceryOrder).where(
                GroceryOrder.id == order_id, GroceryOrder.household_id == household_id
            )
        )
    ).first()


def lock_current_draft(session: Session, household_id: UUID) -> GroceryOrder | None:
    """The household's draft, locked for this transaction, or None if it has none."""
    return session.exec(
        for_update(
            select(GroceryOrder).where(
                GroceryOrder.household_id == household_id,
                GroceryOrder.status == GroceryOrderStatus.DRAFT,
            )
        )
    ).first()


def _lock_draft_row(session: Session, household_id: UUID, order_id: UUID) -> GroceryOrder:
    """The household's draft, locked for this transaction (its row, not the order lock)."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status != GroceryOrderStatus.DRAFT:
        session.rollback()
        raise OrderPlaced("This order has been placed and can't be changed.")
    return order


def lock_draft(
    session: Session, household_id: UUID, order_id: UUID, orderer: Orderer
) -> GroceryOrder:
    """The household's draft, locked for this transaction, for a change from the
    orderer's session: OrderLocked unless that session has the order lock."""
    order = _lock_draft_row(session, household_id, order_id)
    _check_session(session, order, orderer)
    return order


def take_lock(
    session: Session, household_id: UUID, order_id: UUID, orderer: Orderer, step: OrderStep
) -> GroceryOrder:
    """Take the draft's lock for the orderer's session, as its flow opens:
    OrderLocked if another member has a live one."""
    order = _lock_draft_row(session, household_id, order_id)
    now = utc_now()
    if not _take(order, orderer, step, now):
        session.rollback()
        raise _refuse(order, orderer.user_id, now)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def renew_lock(
    session: Session, household_id: UUID, order_id: UUID, orderer: Orderer, step: OrderStep
) -> GroceryOrder:
    """Renew the lock the orderer's session has, and say which step it's at. Never
    takes it: a renewal arriving after the flow closed, or after another opening
    took over, changes nothing (OrderLocked)."""
    order = lock_draft(session, household_id, order_id, orderer)
    order.lock_renewed_at, order.lock_step = utc_now(), step
    _reach(order, step)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def release_lock(session: Session, household_id: UUID, order_id: UUID, session_id: UUID) -> None:
    """Release the lock if that session has it, as its flow closes; otherwise
    (someone else's, none, gone, or placed) do nothing."""
    order = lock_order(session, household_id, order_id)
    if order is None or order.lock_session != session_id:
        session.rollback()
        return
    _clear_lock(order)
    session.add(order)
    session.commit()


def discard(session: Session, household_id: UUID, order_id: UUID, user_id: UUID) -> None:
    """Delete a draft, with its picks and jobs (their meals stay), unless another
    member is ordering (OrderLocked). Discarding one that's already gone does nothing."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        return
    if order.status != GroceryOrderStatus.DRAFT:
        session.rollback()
        raise OrderPlaced("This order has been placed and can't be discarded.")
    now = utc_now()
    if has_live_lock(order, now) and order.locked_by != user_id:
        session.rollback()
        raise _refuse(order, user_id, now)
    session.delete(order)
    session.commit()
    log.info("grocery_order.discarded", extra={"grocery_order_id": str(order_id)})


def pick(
    session: Session, household_id: UUID, order_id: UUID, meal_id: UUID, orderer: Orderer
) -> None:
    """Add one of the draft's suggestions to it."""
    lock_draft(session, household_id, order_id, orderer)
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
    session: Session,
    household_id: UUID,
    order_id: UUID,
    meal_id: UUID,
    servings: int,
    orderer: Orderer,
) -> None:
    """Change how many to make of one of the draft's picks. A meal that isn't
    picked (e.g. unpicked by a change still being saved) is an error, never a new pick."""
    lock_draft(session, household_id, order_id, orderer)
    pick = session.get(GroceryOrderMeal, (order_id, meal_id))
    if pick is None:
        session.rollback()
        raise NotPicked("That meal isn't picked.")
    pick.servings = servings
    session.commit()


def unpick(
    session: Session, household_id: UUID, order_id: UUID, meal_id: UUID, orderer: Orderer
) -> None:
    lock_draft(session, household_id, order_id, orderer)
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
    delivery_by: time | None,
    next_delivery: date | None,
    today: date,
    orderer: Orderer,
) -> None:
    """Save the delivery date, the latest time it should arrive by that day, and
    the next delivery date."""
    order = lock_draft(session, household_id, order_id, orderer)
    try:
        check_dates(delivery, next_delivery, today)
    except InvalidDates:
        session.rollback()
        raise
    order.delivery_date, order.next_delivery_date = delivery, next_delivery
    order.delivery_by = delivery_by
    session.add(order)
    session.commit()


def place(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    orderer: Orderer,
    delivery: date,
    delivery_by: time,
    next_delivery: date,
    quote_id: UUID,
    today: date,
) -> tuple[GroceryOrder, bool]:
    """Place (approve) a draft with the dates, time, and quote (its estimated
    total) the member saw: save them, check the draft and that the quote is its
    current one, and mark it placed by the user, now, waiting to book at the store
    (castle_chef.booking), releasing its lock; all under the draft's row lock, so
    another change can't land in between. Only the session with the order lock can
    place it. Placing an order that's already placed returns it unchanged (its
    dates, who placed it and when included), so a retry after a lost response is
    safe. Returns (order, placed now); one placed now is left uncommitted, its
    row still locked, for the caller to start booking it if it's due, and commit."""
    order = lock_order(session, household_id, order_id)
    if order is None:
        session.rollback()
        raise OrderNotFound("No such order.")
    if order.status == GroceryOrderStatus.PLACED:
        session.rollback()
        return order, False
    _check_session(session, order, orderer)
    missing = _not_ready(session, order, delivery, next_delivery, today) or _quote_problem(
        session, order, quote_id
    )
    if missing is not None:
        session.rollback()
        raise NotReady(missing)
    order.delivery_date, order.next_delivery_date = delivery, next_delivery
    order.delivery_by = delivery_by
    order.status = GroceryOrderStatus.PLACED
    order.placed_at = utc_now()
    order.placed_by = orderer.user_id
    order.book_state, order.quote_id = BookState.WAITING, quote_id
    order.clicked_at, order.booking, order.book_issue = None, None, None
    _clear_lock(order)
    session.add(order)
    session.flush()
    log.info("grocery_order.placed", extra={"grocery_order_id": str(order.id)})
    return order, True


def _not_ready(
    session: Session, order: GroceryOrder, delivery: date, next_delivery: date, today: date
) -> str | None:
    if not picks(session, order.id):
        return "Pick at least one meal."
    if (problem := shopped_problem(session, order.id)) is not None:
        return problem
    try:
        check_dates(delivery, next_delivery, today)
    except InvalidDates as error:
        return str(error)
    return None


def _quote_problem(session: Session, order: GroceryOrder, quote_id: UUID) -> str | None:
    """Why the member's quote isn't one to place the order on: not the current
    one (the list or the tip changed since), not read, or below the store's minimum.
    The household's row is locked first, so its tip rule can't change until the
    order is placed."""
    session.exec(for_update(select(Household).where(Household.id == order.household_id))).one()
    quote = quotes.current(session, order)
    if quote is None or quote.id != quote_id:
        return "The total changed; check the new one."
    if quote.status != JobStatus.SUCCEEDED or quote.quote is None:
        return "The total isn't ready yet."
    if quote.quote.blocked_because is not None:
        return quote.quote.blocked_because
    return None


def list_problem(session: Session, order_id: UUID) -> str | None:
    """Why the draft's shopping list isn't the one for its picks, or None if it is."""
    if shopping.current_build(session, order_id) is None:
        if shopping.newest_built(session, order_id) is None:
            return "The shopping list isn't ready yet."
        return "The shopping list was made for other meals or servings; wait for the new one."
    return None


def shopped_problem(session: Session, order_id: UUID) -> str | None:
    """Why the draft's list isn't ready to order: not the list for its picks, not
    shopped yet, or with items the store doesn't have still to decide on."""
    if (problem := list_problem(session, order_id)) is not None:
        return problem
    build = shopping.newest_build(session, order_id)
    assert build is not None  # the list is current
    shop = shop_of(session, build.id)
    if shop is None or shop.status != JobStatus.SUCCEEDED:
        return "The shopping list isn't shopped yet."
    order = session.get_one(GroceryOrder, order_id)
    household = session.get_one(Household, order.household_id)
    if order.status == GroceryOrderStatus.DRAFT:
        if delivery.selected(household) is None:
            return delivery.ADDRESS_REQUIRED
        if shop.delivery_address != household.delivery_address:
            return delivery.ADDRESS_CHANGED
    undecided = [
        item.name
        for item in products.items_to_buy(session, build.id)
        if item.not_found and item.product is None
    ]
    if undecided:
        store = get_settings().instacart_store
        return f"Decide on what {store} doesn't have: {', '.join(undecided)}."
    return None


def change_item(
    session: Session,
    household_id: UUID,
    order_id: UUID,
    item_id: UUID,
    orderer: Orderer,
    *,
    removed: bool | None,
    quantity: float | None,
    count: int | None = None,
    alternative: str | None = None,
) -> None:
    """Take an item off the draft's list (or put it back; for a product, don't buy
    it), change how much of it to buy (the amount worked out for the recipes
    resets it), how many of its product to buy, or switch its product to one of
    its alternatives (by product id). Only the current list's items to buy can
    change: a list made for other picks is about to be replaced."""
    lock_draft(session, household_id, order_id, orderer)
    newest = shopping.newest_build(session, order_id)
    item = session.get(ShoppingListItem, item_id)
    if item is None or newest is None or item.job_id != newest.id or item.on_hand:
        session.rollback()
        raise ItemNotFound("No such item on the shopping list.")
    if (problem := list_problem(session, order_id)) is not None:
        session.rollback()
        raise ListNotCurrent(problem)
    shop = shop_of(session, newest.id)
    if shop is not None and shop.status in (JobStatus.PENDING, JobStatus.RUNNING):
        session.rollback()
        raise ListNotCurrent("The list is being shopped; wait for it to finish.")
    if removed is not None:
        item.removed = removed
    if alternative is not None:
        try:
            use_alternative(item, alternative)
        except NoSuchProduct:
            session.rollback()
            raise
    if count is not None:
        if item.product is None:
            session.rollback()
            raise NoSuchProduct("The item has no product to buy more or fewer of.")
        item.product = item.product.model_copy(update={"count": count})
    if quantity is not None and item.quantity is None:
        session.rollback()
        raise NoAmount("The recipes give no amount for this item, so there's none to change.")
    if quantity is not None:
        # Back at the amount worked out for the recipes: no change of its own.
        item.changed_quantity = None if quantity == item.quantity else quantity
    session.add(item)
    session.commit()


def use_alternative(item: ShoppingListItem, product_id: str) -> None:
    """Switch the item to one of its alternatives (staged; the caller commits); the
    product it had becomes one. Its product already: nothing to do (a retry)."""
    if item.product is not None and item.product.product.product_id == product_id:
        return
    curated = any(a.product.product_id == product_id for a in item.alternatives)
    source = item.alternatives if curated else item.more_alternatives
    chosen = next((a for a in source if a.product.product_id == product_id), None)
    if chosen is None:
        raise NoSuchProduct("That product isn't one of the item's alternatives.")
    previous_id = item.product.product.product_id if item.product else None
    others = [a for a in source if a.product.product_id not in (product_id, previous_id)]
    alternatives = [*others, item.product] if item.product else others
    if curated:
        item.alternatives = alternatives
    else:
        item.more_alternatives = alternatives
    item.product = chosen
    item.removed = False
    # Found, then: the store has this instead.
    item.not_found = False
    item.skippable_reason = None


# Computed, not stored.


def household_meal(session: Session, household_id: UUID, meal_id: UUID) -> Meal:
    """One of the household's meals: a suggestion of one of its orders (every pick
    is one). Another household's is indistinguishable from none, and so is one whose
    draft was discarded."""
    query = (
        select(Meal)
        .join(Job, col(Job.id) == Meal.job_id)
        .join(GroceryOrder, col(GroceryOrder.id) == Job.grocery_order_id)
        .where(Meal.id == meal_id, GroceryOrder.household_id == household_id)
    )
    meal = session.exec(query).first()
    if meal is None:
        raise MealNotFound("No such meal.")
    return meal


def rate_meal(
    session: Session,
    household_id: UUID,
    meal_id: UUID,
    rating: MealRating,
    orderer: Orderer | None,
) -> None:
    """Set the household's rating of one of its meals, replacing any earlier one.
    Not for us also unpicks it from the draft: under the draft's row lock, taken
    first, so a concurrent pick (which checks the rating under the same lock)
    can't leave it picked; and, when it is picked, only from the ordering session
    with the order lock (`orderer`, None outside the flow)."""
    draft = lock_current_draft(session, household_id) if rating == MealRating.NOT_FOR_US else None
    try:
        household_meal(session, household_id, meal_id)
    except MealNotFound:
        session.rollback()
        raise
    if draft is not None and session.get(GroceryOrderMeal, (draft.id, meal_id)) is not None:
        if orderer is None:
            session.rollback()
            raise OrderLocked("It's picked for the next order: open the order to change it.")
        _check_session(session, draft, orderer)
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
            <= tuple_(literal(order.created_at), literal(order.id)),
        )
    ).one()


def target_date(session: Session, order: GroceryOrder) -> date | None:
    """The previous placed order's next delivery date; None for the first order."""
    return session.exec(
        select(col(GroceryOrder.next_delivery_date))
        .where(
            GroceryOrder.household_id == order.household_id,
            GroceryOrder.status == GroceryOrderStatus.PLACED,
            tuple_(col(GroceryOrder.created_at), col(GroceryOrder.id))
            < tuple_(literal(order.created_at), literal(order.id)),
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
        select(col(GroceryOrder.next_delivery_date))
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


def reminders_start_date(
    session: Session, household_id: UUID, decide_by: date | None
) -> date | None:
    """The first day of the daily reminders to decide an order: its decide-by date
    less the household's reminder lead. They go on daily until it's placed."""
    if decide_by is None:
        return None
    household = session.get_one(Household, household_id)
    return decide_by - timedelta(days=household.reminder_lead_days)
