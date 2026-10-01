"""What an order would cost at the store (a `quote` job, for the final review):
the worker fills the household's cart with the list's products and reads
checkout (castle_chef.cart, castle_chef.checkout). A quote is current while the
list's products and counts, and the household's tip rule, are the ones it read."""

import logging
from datetime import timedelta
from uuid import UUID

from sqlmodel import Session, col, select

from castle_chef import products, shopping
from castle_chef.checkout import CartLine, QuoteRequest
from castle_chef.jobs import ACTIVE, JobIdTaken, fail_stale_jobs
from castle_chef.models import GroceryOrder, Household, Job, JobKind, JobStatus

log = logging.getLogger(__name__)


class NothingToQuote(Exception):
    """The order has no products to buy yet; the message says why."""


def request_for(session: Session, order: GroceryOrder) -> QuoteRequest:
    """What a quote of the order would price now: its current list's products to
    buy and their counts, and the household's tip rule."""
    build = shopping.newest_build(session, order.id)
    if build is None or not shopping.is_current(session, order.id, build):
        raise NothingToQuote("The shopping list isn't ready yet.")
    counts: dict[str, int] = {}
    for item in products.items_to_buy(session, build.id):
        if item.product is not None:
            product_id = item.product.product.product_id
            # Two items can buy the same product: the cart holds it once.
            counts[product_id] = counts.get(product_id, 0) + item.product.count
    if not counts:
        raise NothingToQuote("There's nothing to buy on the shopping list.")
    household = session.get_one(Household, order.household_id)
    return QuoteRequest(
        lines=[CartLine(product_id=pid, quantity=count) for pid, count in sorted(counts.items())],
        tip_percent=household.tip_percent,
        tip_max_cents=household.tip_max_cents,
    )


def current(session: Session, order: GroceryOrder) -> Job | None:
    """The newest quote of the order for what it would price now, or None."""
    try:
        request = request_for(session, order)
    except NothingToQuote:
        return None
    newest = newest_quote(session, order.id)
    return newest if newest is not None and newest.quote_request == request else None


def newest_quote(session: Session, order_id: UUID) -> Job | None:
    return session.exec(
        select(Job)
        .where(Job.grocery_order_id == order_id, Job.kind == JobKind.QUOTE)
        .order_by(col(Job.created_at).desc(), col(Job.id).desc())
        .limit(1)
    ).first()


def submit_quote(
    session: Session,
    order: GroceryOrder,
    member_id: UUID,
    job_id: UUID,
    stale_after: tuple[timedelta, timedelta],
) -> tuple[Job, bool]:
    """Start a quote of the draft as it is now, and commit. Returns (job,
    created): a quote waiting, running, or done for the same products, counts,
    and tip rule is returned rather than started again, and a retried request
    (the client picks the id) gets the job it made. The caller holds the
    order's row lock."""
    existing = session.get(Job, job_id)
    if existing is not None:
        session.rollback()
        if (existing.grocery_order_id, existing.requested_by, existing.kind) != (
            order.id,
            member_id,
            JobKind.QUOTE,
        ):
            raise JobIdTaken(f"Job id {job_id} is taken.")
        return existing, False
    try:
        request = request_for(session, order)
    except NothingToQuote:
        session.rollback()
        raise
    fail_stale_jobs(session, order.id, *stale_after)
    newest = newest_quote(session, order.id)
    if (
        newest is not None
        and newest.quote_request == request
        and newest.status in (*ACTIVE, JobStatus.SUCCEEDED)
    ):
        session.rollback()
        return newest, False
    job = Job(
        id=job_id,
        grocery_order_id=order.id,
        kind=JobKind.QUOTE,
        requested_by=member_id,
        quote_request=request,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log.info(
        "job.created",
        extra={"job_id": str(job.id), "kind": job.kind, "grocery_order_id": str(order.id)},
    )
    return job, True


def product_names(session: Session, order_id: UUID) -> dict[str, str]:
    """The names of the products on the order's current list, by id."""
    build = shopping.newest_build(session, order_id)
    if build is None:
        return {}
    return {
        item.product.product.product_id: item.product.product.name
        for item in products.items_to_buy(session, build.id)
        if item.product is not None
    }
