"""The household's Instacart cart while a job fills it with an order's products
(a quote; booking, from piece 9 part 3b). The household's own items in it are set
aside first and put back when the job is done with the cart, whatever happens
(spec/requirements.md "Shopping list"). What was set aside, and what was put in,
is kept on the household (its cart ledger) until it's put back, so a run that
stops midway is undone by the next one.

Only one run uses the cart at a time: the whole run holds the Codex login's lock
(castle_chef.instacart.store)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from castle_chef_agent.instacart import Cart, CartUpdate, Instacart
from sqlalchemy import update
from sqlmodel import col

from castle_chef import instacart
from castle_chef.checkout import CartLedger, CartLine, OwnItem
from castle_chef.db import get_session
from castle_chef.models import Household
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)


class CartMismatch(Exception):
    """The cart didn't take the products and counts asked for; the message says which."""


class CartNotPutBack(Exception):
    """The household's cart couldn't be put back as it was; the message says what's
    missing. The ledger stays, so the next run tries again."""


def _save_ledger(household_id: UUID, ledger: CartLedger | None) -> None:
    with get_session() as session:
        session.execute(
            update(Household).where(col(Household.id) == household_id).values(cart_ledger=ledger)
        )
        session.commit()


def _ledger(household_id: UUID) -> CartLedger | None:
    with get_session() as session:
        return session.get_one(Household, household_id).cart_ledger


def put_back(store: Instacart, ledger: CartLedger) -> list[str]:
    """Undo a run: take out what it put in, and put the household's own items
    back as they were. Returns what the cart didn't take back: the household's
    items not restored, and the run's products still in it (empty: done)."""
    own = {line.product_id: line.quantity for line in ledger.set_aside}
    updates = [
        CartUpdate(product_id=product_id, quantity=0)
        for product_id in ledger.added
        if product_id not in own
    ]
    updates += [CartUpdate(product_id=pid, quantity=quantity) for pid, quantity in own.items()]
    cart = store.update_cart(updates) if updates else store.cart()
    held = {item.product.product_id: item for item in cart.items}
    missing = [
        line.name
        for line in ledger.set_aside
        if line.product_id not in held or held[line.product_id].quantity != line.quantity
    ]
    stuck = [held[pid].product.name for pid in ledger.added if pid not in own and pid in held]
    return missing + stuck


def check(cart: Cart, lines: list[CartLine], names: dict[str, str]) -> None:
    """CartMismatch unless the cart holds exactly these products and counts
    (`names`: each product's name, for the message)."""
    wanted = {line.product_id: line.quantity for line in lines}
    held = {item.product.product_id: item.quantity for item in cart.items}
    if held == wanted:
        return
    missing = [names.get(pid, pid) for pid, quantity in wanted.items() if held.get(pid) != quantity]
    extra = [item.product.name for item in cart.items if item.product.product_id not in wanted]
    problems = []
    if missing:
        problems.append(f"{get_settings().instacart_store} didn't take {', '.join(missing)}")
    if extra:
        problems.append(f"the cart also has {', '.join(extra)}")
    raise CartMismatch("; ".join(problems) + ".")


@contextmanager
def filled(household_id: UUID, lines: list[CartLine]) -> Iterator[Cart]:
    """The household's cart with just these products and counts, for the length
    of the block; its own items are set aside until then. Yields the cart as
    Instacart has it after filling (the caller checks it)."""
    with instacart.store() as store:
        if (leftover := _ledger(household_id)) is not None:
            # A run stopped (or couldn't put everything back) before this one.
            if problems := put_back(store, leftover):
                # Its second try: Instacart won't take them back, so the record is
                # dropped and the owner is told (an error alarms).
                log.error("cart.not_put_back", extra={"items": problems, "tries": 2})
            else:
                log.warning("cart.put_back_leftovers", extra={"set_aside": len(leftover.set_aside)})
            _save_ledger(household_id, None)
        own = store.cart().items
        ledger = CartLedger(
            set_aside=[
                OwnItem(
                    product_id=item.product.product_id,
                    quantity=item.quantity,
                    name=item.product.name,
                )
                for item in own
            ],
            added=[line.product_id for line in lines],
        )
        _save_ledger(household_id, ledger)
        log.info("cart.set_aside", extra={"own_items": len(own), "products": len(lines)})
        wanted = {line.product_id for line in lines}
        try:
            cart = store.update_cart(
                [
                    CartUpdate(product_id=item.product.product_id, quantity=0)
                    for item in own
                    if item.product.product_id not in wanted
                ]
                + [CartUpdate(product_id=line.product_id, quantity=line.quantity) for line in lines]
            )
            yield cart
        finally:
            if problems := put_back(store, ledger):
                # Kept on the ledger: the next run tries again.
                log.error("cart.not_put_back", extra={"items": problems, "tries": 1})
                raise CartNotPutBack(
                    f"Couldn't put your {get_settings().instacart_store} cart back as it was: "
                    f"{', '.join(problems)}. The next try puts them back."
                )
            _save_ledger(household_id, None)
            log.info("cart.put_back", extra={"own_items": len(own)})
