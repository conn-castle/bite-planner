"""The household's Instacart cart while a job fills it (bite_planner.cart), against
the stand-in app-server's cart that keeps its changes (fake_codex.py)."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlmodel import Session

from bite_planner import cart
from bite_planner.checkout import CartLedger, CartLine, OwnItem
from bite_planner.models import Household

from .conftest import TEST_ADDRESS

ONIONS, SWEET_ONIONS = "17499438", "3371870"  # recorded products (fixtures/instacart)


def start_cart(codex_home: Path, items: dict[str, float], **config: Any) -> None:
    lines = [{"product_id": pid, "quantity": quantity} for pid, quantity in items.items()]
    (codex_home / "fake.json").write_text(json.dumps({"cart": lines, **config}))


def cart_now(codex_home: Path) -> dict[str, float]:
    path = codex_home / "cart-state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def ledger(session: Session, household_id: UUID) -> CartLedger | None:
    session.expire_all()
    return session.get_one(Household, household_id).cart_ledger


def test_the_households_own_items_are_set_aside_while_the_cart_is_filled_then_put_back(
    session: Session, household_id: UUID, codex_home: Path
) -> None:
    start_cart(codex_home, {ONIONS: 1, "555": 4})
    lines = [CartLine(product_id=SWEET_ONIONS, quantity=2), CartLine(product_id="777", quantity=3)]

    with cart.filled(household_id, lines, TEST_ADDRESS) as filled:
        inside = cart_now(codex_home)
        kept = ledger(session, household_id)
        cart.check(filled.cart, lines, {})

    assert inside == {SWEET_ONIONS: 2, "777": 3}
    assert kept == CartLedger(
        set_aside=[
            OwnItem(product_id=ONIONS, quantity=1, name="Wegmans Onions, Yellow"),
            OwnItem(product_id="555", quantity=4, name="Product 555"),
        ],
        added=[SWEET_ONIONS, "777"],
    )
    assert cart_now(codex_home) == {ONIONS: 1, "555": 4}
    assert ledger(session, household_id) is None


def test_a_product_the_household_already_has_goes_back_to_its_own_count(
    session: Session, household_id: UUID, codex_home: Path
) -> None:
    start_cart(codex_home, {ONIONS: 1})

    with cart.filled(household_id, [CartLine(product_id=ONIONS, quantity=3)], TEST_ADDRESS):
        inside = cart_now(codex_home)

    assert (inside, cart_now(codex_home)) == ({ONIONS: 3}, {ONIONS: 1})


def test_the_cart_is_put_back_when_the_run_fails(
    session: Session, household_id: UUID, codex_home: Path
) -> None:
    start_cart(codex_home, {ONIONS: 1})

    with (
        pytest.raises(RuntimeError),
        cart.filled(household_id, [CartLine(product_id="777", quantity=3)], TEST_ADDRESS),
    ):
        raise RuntimeError("checkout changed")

    assert cart_now(codex_home) == {ONIONS: 1}
    assert ledger(session, household_id) is None


def test_a_run_that_stopped_midway_is_put_back_by_the_next(
    session: Session, household_id: UUID, codex_home: Path
) -> None:
    # The last run set aside the onions and put in its 777s, then its task died.
    start_cart(codex_home, {"777": 3, "888": 1})  # 888: added by the household since
    household = session.get_one(Household, household_id)
    household.cart_ledger = CartLedger(
        set_aside=[OwnItem(product_id=ONIONS, quantity=1, name="Wegmans Onions, Yellow")],
        added=["777"],
    )
    session.commit()

    with cart.filled(household_id, [CartLine(product_id="999", quantity=2)], TEST_ADDRESS):
        inside = cart_now(codex_home)
        kept = ledger(session, household_id)

    assert inside == {"999": 2}
    # Its own items, as they were before both runs.
    assert kept is not None and {line.product_id for line in kept.set_aside} == {"888", ONIONS}
    assert cart_now(codex_home) == {"888": 1, ONIONS: 1}


def test_a_product_the_cart_doesnt_take_is_named(
    session: Session, household_id: UUID, codex_home: Path
) -> None:
    start_cart(codex_home, {}, unavailable=["777"])
    lines = [CartLine(product_id=ONIONS, quantity=1), CartLine(product_id="777", quantity=2)]

    with (
        pytest.raises(cart.CartMismatch, match="Wegmans didn't take Curry Leaves"),
        cart.filled(household_id, lines, TEST_ADDRESS) as filled,
    ):
        cart.check(filled.cart, lines, {"777": "Curry Leaves"})

    assert cart_now(codex_home) == {}


def test_an_item_the_cart_wont_take_back_is_tried_again_by_the_next_run(
    session: Session, household_id: UUID, codex_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The household's 555s sell out while the run has them set aside.
    start_cart(codex_home, {"555": 2}, sold_out=["555"])

    with (
        pytest.raises(cart.CartNotPutBack, match="Product 555"),
        cart.filled(household_id, [CartLine(product_id="777", quantity=1)], TEST_ADDRESS),
    ):
        pass

    # Kept, so the next run tries again;
    assert cart_now(codex_home) == {}
    kept = ledger(session, household_id)
    assert kept is not None and [line.product_id for line in kept.set_aside] == ["555"]
    # which, when Instacart still won't take them, says so as an error and goes on.
    with cart.filled(household_id, [CartLine(product_id="777", quantity=1)], TEST_ADDRESS):
        pass
    assert ledger(session, household_id) is None
    [error] = [r for r in caplog.records if r.getMessage() == "cart.not_put_back"][1:]
    assert (error.levelname, error.__dict__["items"]) == ("ERROR", ["Product 555"])
