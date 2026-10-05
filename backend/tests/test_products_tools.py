"""The agent's shopping tools (castle_chef_agent.server's products server): search
the store, choose each item's product with alternatives, or mark it not found,
checked as each is made. Called as the agent calls them, over a real Instacart
session with a stand-in `codex app-server` (recorded replies)."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from castle_chef_agent.instacart import connect
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.products import ShopItem, ShopJob
from castle_chef_agent.server import products_server
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from tests.conftest import FAKE_CODEX

ITEMS = [
    ShopItem(number=1, name="yellow onions", quantity=3, unit="lb", meals=["Coconut curry"]),
    ShopItem(number=2, name="curry leaves", quantity=10, unit=None, meals=["Coconut curry"]),
    ShopItem(number=3, name="sweet onions", quantity=None, unit=None, meals=["Fajitas"]),
]


@pytest.fixture
def tools(tmp_path: Path, codex_home: Path) -> Iterator[MCPServer]:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    write_context(job_dir, ShopJob(items=ITEMS))
    with connect(codex_home, "Wegmans", command=["python3", str(FAKE_CODEX)]) as store:
        server = products_server(job_dir, store)
        JOB_DIRS[id(server)] = job_dir
        yield server


JOB_DIRS: dict[int, Path] = {}


def tools_dir(tools: MCPServer) -> Path:
    return JOB_DIRS[id(tools)]


def call(tools: MCPServer, tool: str, **arguments: Any) -> tuple[bool, str]:
    """(refused, text) of one tool call: a refusal is the error the agent reads."""
    try:
        result = asyncio.run(tools.call_tool(tool, arguments))
    except ToolError as error:
        return True, str(error)
    assert isinstance(result, CallToolResult)  # these tools never ask for input
    text = " ".join(getattr(part, "text", "") for part in result.content)
    return bool(result.is_error), text


def search_onions(tools: MCPServer, item: int = 1) -> None:
    assert not call(tools, "search", item=item, query="yellow onions")[0]


def test_search_lists_each_products_id_size_price_and_whether_it_was_bought_before(
    tools: MCPServer,
) -> None:
    refused, text = call(tools, "search", item=1, query="yellow onions")
    assert not refused
    lines = text.splitlines()
    assert lines[0] == "Matches (5):"
    assert lines[1] == "17499438: Wegmans Onions, Yellow · 2 lb · $2.29 · bought before"
    assert lines[2] == "3371870: Sweet Onions · per lb · $1.72 · $2.29/lb, about 0.75 lb"
    assert "Related (5):" in lines


def test_a_search_with_no_matches_says_the_store_may_not_carry_it(tools: MCPServer) -> None:
    text = call(tools, "search", item=2, query="curry leaves")[1]
    assert "Matches (0):\n(none: the store may not carry it)\nRelated (10):" in text


def test_only_products_the_items_searches_returned_can_be_chosen(tools: MCPServer) -> None:
    refused, text = call(tools, "choose", item=3, product_id="17499438", count=1, why_none="x")
    assert refused
    assert "No search for item 3 returned product 17499438" in text
    # A product found for another item isn't this one's.
    search_onions(tools, item=1)
    refused, text = call(tools, "choose", item=3, product_id="3371870", count=1, why_none="x")
    assert refused and "No search for item 3 returned product 3371870" in text


def test_a_choice_must_cover_the_items_amount(tools: MCPServer) -> None:
    search_onions(tools)
    alternatives = [{"product_id": "24713455", "count": 1}]
    refused, text = call(
        tools, "choose", item=1, product_id="17499438", count=1, alternatives=alternatives
    )
    assert refused
    assert text.endswith(
        "1 of Wegmans Onions, Yellow (2 lb) doesn't cover 3 lb of yellow onions: "
        "it takes at least 2."
    )
    refused, text = call(
        tools, "choose", item=1, product_id="17499438", count=2, alternatives=alternatives
    )
    assert (refused, text) == (False, "Item 1: 2 x Wegmans Onions, Yellow, 1 alternatives.")


def test_a_choice_needs_alternatives_or_why_there_are_none(tools: MCPServer) -> None:
    search_onions(tools, item=3)
    refused, text = call(tools, "choose", item=3, product_id="3371870", count=2)
    assert refused
    assert "Give alternatives, or say why there are none." in text
    refused, _ = call(
        tools, "choose", item=3, product_id="3371870", count=2, why_none="The only sweet onion."
    )
    assert not refused


def test_alternatives_come_from_searches_once_each_and_at_most_eight(tools: MCPServer) -> None:
    search_onions(tools, item=3)
    for alternatives, problem in [
        ([{"product_id": "999", "count": 1}], "No search for item 3 returned product 999"),
        ([{"product_id": "3371870", "count": 1}], "not the product chosen"),
        ([{"product_id": "24713455", "count": 1}] * 2, "Give each alternative once"),
    ]:
        refused, text = call(
            tools, "choose", item=3, product_id="3371870", count=1, alternatives=alternatives
        )
        assert refused and problem in text
    # Nine products its search did return: still too many.
    searched = json.loads((tools_dir(tools) / "products.json").read_text())["searched"]["3"]
    nine = [{"product_id": p, "count": 1} for p in searched if p != "3371870"][:9]
    assert len(nine) == 9
    refused, text = call(tools, "choose", item=3, product_id="3371870", count=1, alternatives=nine)
    # The tool's input schema refuses it, before the check behind it.
    assert refused and "alternatives\n  List should have at most 8 items" in text


def test_alternatives_must_cover_the_items_amount_too(tools: MCPServer) -> None:
    search_onions(tools)
    small = [{"product_id": "3371870", "count": 1}]  # one sweet onion, about 0.75 lb
    refused, text = call(
        tools, "choose", item=1, product_id="17499438", count=2, alternatives=small
    )
    assert refused
    assert text.endswith(
        "Alternative: 1 of Sweet Onions (per lb) doesn't cover 3 lb of yellow onions: "
        "it takes at least 4."
    )


def test_an_item_not_found_needs_a_search_and_alternatives_or_why_the_meal_works_without_it(
    tools: MCPServer,
) -> None:
    refused, text = call(tools, "not_found", item=2, why_skippable="Aroma only.")
    assert refused and "Search for item 2 first" in text
    call(tools, "search", item=2, query="curry leaves")
    refused, text = call(tools, "not_found", item=2)
    assert refused
    assert "or say why the meals work without it" in text
    assert not call(tools, "not_found", item=2, why_skippable="Aroma only; the curry is fine.")[0]
    bay = [{"product_id": "16960310", "count": 1}]  # Laxmi Bay Leaves, a related product
    assert call(tools, "not_found", item=2, alternatives=bay) == (
        False,
        "Item 2: not found, 1 alternatives.",
    )


def test_done_lists_the_items_left_until_every_one_is_decided(tools: MCPServer) -> None:
    search_onions(tools)
    search_onions(tools, item=3)
    call(tools, "search", item=2, query="curry leaves")
    refused, text = call(tools, "done")
    assert refused
    assert "Still to decide: 1 (yellow onions), 2 (curry leaves), 3 (sweet onions)." in text
    call(tools, "choose", item=1, product_id="17499438", count=2, why_none="Only one bag size.")
    call(tools, "not_found", item=2, why_skippable="Aroma only.")
    call(tools, "choose", item=3, product_id="3371870", count=1, why_none="The only one.")
    assert call(tools, "done") == (False, "Complete: all 3 items are decided.")


def test_choosing_again_replaces_the_items_decision(tools: MCPServer, tmp_path: Path) -> None:
    search_onions(tools, item=3)
    call(tools, "not_found", item=3, why_skippable="Not needed.")
    call(tools, "choose", item=3, product_id="3371870", count=1, why_none="The only one.")
    state = json.loads((tmp_path / "job" / "products.json").read_text())
    assert state["choices"]["3"]["chosen"]["product"]["product_id"] == "3371870"


def test_an_item_not_on_the_list_is_refused(tools: MCPServer) -> None:
    refused, text = call(tools, "search", item=9, query="yellow onions")
    assert refused and "There's no item 9 on the list." in text
    refused, text = call(tools, "choose", item=9, product_id="3371870", count=1, why_none="x")
    assert refused and "There's no item 9 on the list." in text
