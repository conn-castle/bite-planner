"""The household's Instacart store (castle_chef.instacart): searching and the cart
through Instacart's tools, and connecting it (`castle-chef agent-login codex`),
against a stand-in `codex app-server` that answers with recorded replies (a cart
holding items is assembled from recorded parts: fixtures/instacart/README.md)."""

import fcntl
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from castle_chef_agent.instacart import CartUpdate, InstacartError
from typer.testing import CliRunner

from castle_chef import instacart
from castle_chef.cli import app
from castle_chef.settings import get_settings
from tests.conftest import TEST_ADDRESS, TEST_ADDRESSES


def configure(home: Path, **config: Any) -> None:
    (home / "fake.json").write_text(json.dumps(config))


def requests(home: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (home / "requests.jsonl").read_text().splitlines()]


def test_search_gives_each_products_size_price_and_whether_it_was_bought_before(
    codex_home: Path,
) -> None:
    with instacart.store() as store:
        results = store.search("yellow onions")
    onions = results.matches[0]
    assert (onions.name, onions.size, onions.price_cents, onions.bought_before) == (
        "Wegmans Onions, Yellow",
        "2 lb",
        229,
        True,
    )
    # Weighed items: bought by the piece or package, priced by the pound.
    sweet = results.matches[1]
    assert (sweet.name, sweet.price_cents, sweet.price_per_lb_cents, sweet.estimated_lb) == (
        "Sweet Onions",
        172,
        229,
        0.75,
    )
    assert "Bulk Garlic" in [product.name for product in results.related]
    [search] = [r for r in requests(codex_home) if r.get("method") == "mcpServer/tool/call"]
    assert search["params"]["arguments"] == {"query": "yellow onions", "retailer_name": "Wegmans"}


def test_a_search_for_something_the_store_doesnt_carry_has_no_matches(codex_home: Path) -> None:
    with instacart.store() as store:
        results = store.search("curry leaves")
    assert results.matches == []
    assert results.related  # Instacart still suggests other products


def test_selected_address_is_reselected_after_instacart_switches_away(codex_home: Path) -> None:
    for _ in range(2):
        (codex_home / "selected-address.txt").write_text("202")
        with instacart.store(TEST_ADDRESS) as store:
            assert store.search("yellow onions").matches
            assert store.address is not None and store.address.city == "Rochester"
        assert (codex_home / "selected-address.txt").read_text() == "101"
    calls = [r["params"] for r in requests(codex_home) if r.get("method") == "mcpServer/tool/call"]
    selections = [
        i for i, call in enumerate(calls) if "set_default_address_id" in call["arguments"]
    ]
    searches = [i for i, call in enumerate(calls) if call["tool"] == "instacart.search_products"]
    assert len(selections) == len(searches) == 2
    assert all(selected < searched for selected, searched in zip(selections, searches, strict=True))
    assert all(
        calls[i]["arguments"]
        == {"retailer_name": "Wegmans", "service_type": "DELIVERY", "set_default_address_id": "101"}
        for i in selections
    )


def test_reading_saved_addresses_does_not_change_the_default_or_cart(codex_home: Path) -> None:
    with instacart.store() as store:
        addresses = store.addresses()
    assert [(a.id, a.city) for a in addresses] == [("101", "Rochester"), ("202", "Emerald Isle")]
    calls = [
        r["params"]["arguments"]
        for r in requests(codex_home)
        if r.get("method") == "mcpServer/tool/call"
    ]
    assert calls == [{"show_retailer_selector": True}]
    assert not (codex_home / "selected-address.txt").exists()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"addresses": []}, "isn't saved"),
        ({"ignore_address_selection": True}, "didn't select"),
        ({"address_unavailable": True}, "Wegmans isn't available"),
    ],
)
def test_unusable_selected_address_stops_before_search(
    codex_home: Path,
    config: dict[str, object],
    message: str,
) -> None:
    configure(codex_home, **config)
    with pytest.raises(InstacartError, match=message), instacart.store(TEST_ADDRESS) as store:
        store.search("yellow onions")
    calls = [r["params"] for r in requests(codex_home) if r.get("method") == "mcpServer/tool/call"]
    assert all(call["tool"] != "instacart.search_products" for call in calls)


def test_a_cart_under_the_store_minimum_cant_check_out(codex_home: Path) -> None:
    with instacart.store() as store:
        cart = store.cart()
    assert (cart.items, cart.subtotal_cents) == ([], 0)
    assert not cart.can_check_out
    assert cart.blocked_because == "$10 Min. to checkout"
    assert cart.below_minimum


def test_updating_the_cart_sends_the_quantities_and_returns_the_cart(codex_home: Path) -> None:
    updates = [
        CartUpdate(product_id="17499438", quantity=1),
        CartUpdate(product_id="3371870", quantity=2),
        CartUpdate(product_id="3403203", quantity=0),  # 0 takes a product out
    ]
    with instacart.store() as store:
        cart = store.update_cart(updates)
    [call] = [r for r in requests(codex_home) if r.get("method") == "mcpServer/tool/call"]
    # Instacart refuses product ids that aren't strings.
    assert call["params"]["arguments"]["item_updates"] == [
        {"product_id": "17499438", "quantity": 1.0},
        {"product_id": "3371870", "quantity": 2.0},
        {"product_id": "3403203", "quantity": 0.0},
    ]
    assert [(i.product.product_id, i.quantity) for i in cart.items] == [
        ("17499438", 1.0),
        ("3371870", 2.0),
    ]
    assert cart.subtotal_cents == 573


def test_cart_updates_take_only_store_product_ids_and_quantities() -> None:
    with pytest.raises(ValueError):
        CartUpdate(product_id="onions", quantity=1)
    for quantity in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            CartUpdate(product_id="17499438", quantity=quantity)


def test_instacart_refusing_a_change_is_an_error_saying_why(codex_home: Path) -> None:
    configure(codex_home, refuse_cart=True)
    with (
        instacart.store() as store,
        pytest.raises(InstacartError, match="failed connector schema validation"),
    ):
        store.update_cart([CartUpdate(product_id="17499438", quantity=1)])


def test_an_app_server_that_stops_is_an_error(codex_home: Path) -> None:
    configure(codex_home, stop_on="thread/start")
    with instacart.store() as store, pytest.raises(InstacartError, match="stopped"):
        store.cart()


def test_without_a_codex_login_nothing_reaches_instacart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASTLE_CHEF_CODEX_HOME", raising=False)
    get_settings.cache_clear()
    with pytest.raises(InstacartError, match="No Codex login"), instacart.store():
        pass


def test_the_codex_login_is_locked_while_the_store_is_open(codex_home: Path) -> None:
    with (
        instacart.store(),
        (codex_home / "codex.lock").open("a") as other,
        pytest.raises(BlockingIOError),
    ):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_connecting_signs_in_then_connects_instacart_and_waits_for_it(codex_home: Path) -> None:
    configure(codex_home, signed_in=False, accessible_after=2)
    result = CliRunner().invoke(app, ["agent-login", "codex"])
    assert result.exit_code == 0, result.output
    # What the owner does, in order (the structured log lines are in between).
    lines = [line for line in result.output.splitlines() if not line.startswith("{")]
    assert lines == [
        "Sign in to the household's ChatGPT account: https://auth.openai.com/codex/device",
        "Code: ABCD-1234",
        "Connect Instacart in the same ChatGPT account: "
        "https://chatgpt.com/apps/instacart/connector_691eab1e001081919e57189f8b2f03bc",
        "Instacart is connected: the Wegmans cart has 0 items.",
    ]
    methods = [r.get("method") for r in requests(codex_home)]
    # Waits for the owner: reads the cart until Instacart answers.
    assert methods.count("mcpServer/tool/call") == 3
    assert methods.index("plugin/install") > methods.index("account/login/start")


def test_connecting_when_signed_in_skips_the_device_code(codex_home: Path) -> None:
    result = CliRunner().invoke(app, ["agent-login", "codex"])
    assert result.exit_code == 0, result.output
    assert "Code:" not in result.output
    assert "account/login/start" not in [r.get("method") for r in requests(codex_home)]


def test_a_refused_sign_in_fails_the_login(codex_home: Path) -> None:
    configure(codex_home, signed_in=False, login_success=False)
    result = CliRunner().invoke(app, ["agent-login", "codex"])
    assert result.exit_code == 1
    assert "ChatGPT sign-in failed: Denied." in result.output
    assert "plugin/install" not in [r.get("method") for r in requests(codex_home)]


def test_the_search_command_shows_matches_and_related_products(codex_home: Path) -> None:
    result = CliRunner().invoke(app, ["instacart", "search", "yellow onions"])
    assert result.exit_code == 0, result.output
    assert "  17499438: Wegmans Onions, Yellow · 2 lb · $2.29 · bought before" in result.output
    assert "Related (5):" in result.output


def test_a_changed_search_reply_is_an_error_that_leaves_out_its_contents(codex_home: Path) -> None:
    configure(codex_home, drop_search_items=True)
    with instacart.store() as store, pytest.raises(InstacartError) as raised:
        store.search("yellow onions")
    message = str(raised.value)
    assert "reply wasn't as expected: items: Field required" in message
    # The reply holds the household's addresses and the store's details: none of it.
    assert "Wegmans" not in message and "address" not in message


def test_a_failed_start_lets_go_of_the_login(codex_home: Path) -> None:
    configure(codex_home, stop_on="initialize")
    with pytest.raises(InstacartError, match="stopped"), instacart.store():
        pass
    with (codex_home / "codex.lock").open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again


def test_the_cart_command_shows_the_cart_and_why_checkout_is_blocked(codex_home: Path) -> None:
    result = CliRunner().invoke(app, ["instacart", "cart"])
    assert result.exit_code == 0, result.output
    assert "0 items, subtotal $0.00." in result.output
    assert "Checkout blocked: $10 Min. to checkout" in result.output


def test_connecting_gives_up_when_instacart_isnt_connected_in_time(
    codex_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(codex_home, accessible_after=1000)
    monkeypatch.setattr(instacart, "APPROVAL_TIMEOUT_SECONDS", 0)
    result = CliRunner().invoke(app, ["agent-login", "codex"])
    assert result.exit_code == 1
    assert "Instacart wasn't connected in time: " in result.stderr


def test_an_unknown_login_provider_is_a_usage_error() -> None:
    result = CliRunner().invoke(app, ["agent-login", "gemini"])
    assert result.exit_code == 2
    assert "codex" in result.stderr


def test_the_login_is_released_only_once_everything_codex_started_has_stopped(
    codex_home: Path,
) -> None:
    configure(codex_home, helper_stop_seconds=0.5)
    with instacart.store() as store:
        store.cart()
        helper = int((codex_home / "helper.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(helper, 0)


def test_a_cart_item_without_a_quantity_is_an_error(codex_home: Path) -> None:
    configure(codex_home, drop_cart_quantities=True)
    with (
        instacart.store() as store,
        pytest.raises(InstacartError, match="quantity: Field required"),
    ):
        store.update_cart([CartUpdate(product_id="17499438", quantity=1)])


def test_a_reply_that_isnt_an_object_is_an_error(codex_home: Path) -> None:
    configure(codex_home, non_object_result_for="thread/start")
    with instacart.store() as store, pytest.raises(InstacartError, match="isn't an object"):
        store.cart()


def test_searches_made_at_once_each_get_their_own_answer(codex_home: Path) -> None:
    # The agent's tool calls can come all at once, on several threads.
    queries = ["yellow onions", "curry leaves"] * 3
    with instacart.store() as store, ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(store.search, queries))
    matches = [len(result.matches) for result in results]
    assert matches == [5, 0] * 3


def test_matching_uses_street_abbreviations_and_zip_but_not_the_default(codex_home: Path) -> None:
    entered = TEST_ADDRESS.model_copy(
        update={"street_address": "1 MAIN ST.", "postal_code": "14604-1234"}
    )
    with instacart.store(entered) as store:
        assert store.address is not None and store.address.id == "101"


@pytest.mark.parametrize("unit", ["", "Apt 2"])
def test_duplicate_or_wrong_unit_saved_addresses_stop_before_selecting(
    codex_home: Path,
    unit: str,
) -> None:
    saved = TEST_ADDRESSES[0].model_dump()
    saved["is_default"] = False
    if unit:
        saved["apartment_number"] = "1"
        addresses = [saved]
        expected = TEST_ADDRESS.model_copy(update={"apartment_number": unit})
        message = "isn't saved"
    else:
        addresses = [saved, saved | {"id": "303", "unattended_delivery": True}]
        expected = TEST_ADDRESS
        message = "more than one"
    configure(codex_home, addresses=addresses)
    with pytest.raises(InstacartError, match=message), instacart.store(expected):
        pytest.fail("An ambiguous or different unit must not yield a usable store.")
    calls = [r["params"] for r in requests(codex_home) if r.get("method") == "mcpServer/tool/call"]
    assert [call["arguments"] for call in calls] == [{"show_retailer_selector": True}]
