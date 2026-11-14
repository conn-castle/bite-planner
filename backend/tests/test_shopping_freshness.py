"""Accepted-input dependencies through the draft's public HTTP boundaries."""

from datetime import timedelta
from uuid import UUID

import pytest
from bite_planner_agent.shopping import Aisle
from fastapi.testclient import TestClient

from bite_planner import api
from bite_planner.shopping import ListEntry
from tests.test_api import (
    MOM,
    as_user,
    change_item,
    clear_rating,
    finish_list,
    no_content,
    ok,
    patch_household,
    pick,
    put_dates,
    rate,
    ready_quote,
    request_list,
    request_quote,
    set_servings,
    shopped_draft,
    tomorrow,
)
from tests.test_api import client as client
from tests.test_api import dispatcher as dispatcher
from tests.test_api import households as households
from tests.test_recipe_edits import read


def test_noop_and_undo_restore_existing_choices_and_estimate(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    original = read(client, order_id)
    meal_id = str(original.picks[0].meal_id)
    assert original.shopping_list is not None
    item = original.shopping_list.items[0]
    ok(change_item(client, order_id, str(item.id), {"count": 3, "quantity": 9}))
    quote_id = ready_quote(client, order_id)
    before = read(client, order_id)
    assert before.shopping_list is not None
    ok(pick(client, order_id, meal_id))  # idempotent selected tap
    ok(set_servings(client, order_id, meal_id, before.picks[0].servings))
    assert read(client, order_id).shopping_list == before.shopping_list
    for edit in ("servings", "pick", "not_for_us"):
        if edit == "servings":
            ok(set_servings(client, order_id, meal_id, 8))
        elif edit == "pick":
            ok(
                client.delete(
                    f"/v1/grocery-orders/{order_id}/meals/{meal_id}", headers=as_user(MOM)
                )
            )
        else:
            no_content(rate(client, meal_id, "not_for_us"))
        stale = read(client, order_id)
        assert stale.shopping_list is not None and not stale.shopping_list.current
        assert stale.shopping_list.items == before.shopping_list.items
        assert stale.quote is not None and not stale.quote.current
        if edit == "servings":
            ok(set_servings(client, order_id, meal_id, before.picks[0].servings))
        else:
            if edit == "not_for_us":
                no_content(clear_rating(client, meal_id))
            ok(pick(client, order_id, meal_id))
        restored = read(client, order_id)
        assert restored.shopping_list == before.shopping_list
        assert restored.quote is not None and restored.quote.current
        assert str(restored.quote.job.id) == quote_id
    assert str(api.JobOut.model_validate(ok(request_list(client, order_id))).id) == str(
        before.shopping_list.job.id
    )


@pytest.mark.parametrize(
    "setting",
    [
        "name",
        "delivery_time",
        "default_servings",
        "decide_by_offset_days",
        "reminder_lead_days",
        "product_gone",
    ],
)
def test_nonpricing_settings_and_dates_preserve_all_work(client: TestClient, setting: str) -> None:
    order_id, _ = shopped_draft(client)
    ready_quote(client, order_id)
    before = read(client, order_id)
    if setting == "name":
        ok(patch_household(client, {"name": "Local test castle"}))
    elif setting == "delivery_time":
        ok(patch_household(client, {"delivery_time": "16:00"}))
    elif setting == "product_gone":
        ok(patch_household(client, {"product_gone": "ask"}))
    else:
        ok(patch_household(client, {setting: 3}))
    ok(
        put_dates(
            client,
            order_id,
            tomorrow() + timedelta(days=7),
            tomorrow() + timedelta(days=10),
            by="15:00",
        )
    )
    meal_id: UUID = before.picks[0].meal_id
    no_content(rate(client, str(meal_id), "down"))
    after = read(client, order_id)
    assert after.shopping_list == before.shopping_list
    assert after.quote == before.quote


def test_quote_uses_actual_cart_inputs_and_can_cross_builds(client: TestClient) -> None:
    order_id, _ = shopped_draft(client)
    quote_id = ready_quote(client, order_id)
    before = read(client, order_id)
    assert before.shopping_list is not None
    item = before.shopping_list.items[0]
    meal_id = str(before.picks[0].meal_id)
    # Recipe amount alone is not a pricing input when product IDs/counts stay the same.
    ok(change_item(client, order_id, str(item.id), {"quantity": 9}))
    assert read(client, order_id).quote == before.quote
    edits: list[tuple[dict[str, int | bool], dict[str, int | bool]]] = [
        ({"count": 2}, {"count": 1}),
        ({"removed": True}, {"removed": False}),
    ]
    for change, undo in edits:
        ok(change_item(client, order_id, str(item.id), change))
        stale = read(client, order_id)
        assert stale.shopping_list is not None and stale.shopping_list.current
        assert stale.quote is not None and not stale.quote.current
        ok(change_item(client, order_id, str(item.id), undo))
        assert read(client, order_id).quote == before.quote
    assert item.alternatives
    ok(
        change_item(
            client, order_id, str(item.id), {"alternative": item.alternatives[0].product_id}
        )
    )
    changed = read(client, order_id)
    assert changed.quote is not None and not changed.quote.current
    # A different build which yields the same actual cart reuses the valid estimate.
    ok(set_servings(client, order_id, meal_id, 6))
    new_build = api.JobOut.model_validate(ok(request_list(client, order_id), 201))
    finish_list(
        str(new_build.id),
        [
            # Two items still choose the same one-count product each, as before.
            ListEntry(name="tortillas", quantity=12, aisle=Aisle.PANTRY, meals=[1]),
            ListEntry(name="cotija", quantity=6, aisle=Aisle.DAIRY_EGGS, meals=[1]),
        ],
    )
    restored = read(client, order_id)
    assert restored.shopping_list is not None and restored.shopping_list.current
    assert restored.quote is not None and restored.quote.current
    assert restored.quote.job.id == UUID(quote_id)
    assert str(api.JobOut.model_validate(ok(request_quote(client, order_id))).id) == quote_id
    for setting in ("tip_percent", "tip_max_cents"):
        original_value = 10 if setting == "tip_percent" else 1500
        ok(patch_household(client, {setting: original_value + 1}))
        stale = read(client, order_id)
        assert stale.shopping_list == restored.shopping_list
        assert stale.quote is not None and not stale.quote.current
        ok(patch_household(client, {setting: original_value}))
        assert read(client, order_id).quote == restored.quote
