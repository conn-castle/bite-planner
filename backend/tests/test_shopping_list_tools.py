"""The agent's shopping-list tools (bite_planner_agent.server's shopping-list server):
`add_item`, `add_staple`, `remove`, and `done`, called as the agent calls them in a
job directory, and what the worker saves of the list they make."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from bite_planner_agent import shopping as agent_shopping
from bite_planner_agent.jobfiles import write_context
from bite_planner_agent.server import shopping_list_server
from bite_planner_agent.shopping import Aisle, IngredientLine, ListJob
from mcp.server.mcpserver import MCPServer

from bite_planner import shopping
from bite_planner.shopping import ListEntry
from tests.test_products_tools import call

LINES = [
    IngredientLine(id="1a", meal=1, text="1 onion", has_amount=True),
    IngredientLine(id="1b", meal=1, text="0.5 lemon", has_amount=True),
    IngredientLine(id="1c", meal=1, text="1 tsp kosher salt", has_amount=True),
    IngredientLine(id="2a", meal=2, text="1 cup onion, diced", has_amount=True),
    IngredientLine(id="2b", meal=2, text="1 lemon", has_amount=True),
    IngredientLine(id="2c", meal=2, text="8 oz ground beef", has_amount=True),
]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    """A job directory as the worker makes it for these lines."""
    write_context(tmp_path, ListJob(lines=LINES))
    return tmp_path


@pytest.fixture
def tools(job_dir: Path) -> MCPServer:
    return shopping_list_server(job_dir)


def add_item(
    tools: MCPServer, name: str, aisle: str, *lines: str, **amount: Any
) -> tuple[bool, str]:
    amount.setdefault("more_alternatives_query", name)
    return call(tools, "add_item", name=name, aisle=aisle, lines=list(lines), **amount)


def add_staple(tools: MCPServer, staple: str, *lines: str) -> tuple[bool, str]:
    return call(tools, "add_staple", staple=staple, lines=list(lines))


def test_the_list_agent_must_supply_one_nonblank_broad_query(
    tools: MCPServer, job_dir: Path
) -> None:
    arguments: dict[str, object] = {
        "name": "yellow onions",
        "aisle": "produce",
        "quantity": 1,
        "lines": ["1a"],
    }
    assert call(tools, "add_item", **arguments)[0]
    assert call(tools, "add_item", **arguments, more_alternatives_query="   ")[0]
    assert not call(tools, "add_item", **arguments, more_alternatives_query="  onions  ")[0]
    draft = agent_shopping.load_draft(job_dir)
    assert draft.items[0].more_alternatives_query == "onions"


def test_an_item_is_added_with_the_lines_it_covers(tools: MCPServer, job_dir: Path) -> None:
    result = add_item(tools, "ground beef", "meat_seafood", "2c", quantity=0.5, unit="lb")

    assert result == (False, 'Added "ground beef": 0.5 lb, meat_seafood.')
    [item] = agent_shopping.load_draft(job_dir).items
    assert (item.name, item.quantity, item.unit, item.aisle, item.lines) == (
        "ground beef",
        0.5,
        "lb",
        Aisle.MEAT_SEAFOOD,
        ["2c"],
    )


def test_a_count_is_rounded_up_to_whole_ones(tools: MCPServer, job_dir: Path) -> None:
    result = add_item(tools, "lemons", "produce", "1b", "2b", quantity=1.5)

    assert result == (False, 'Added "lemons": 2, produce.')
    assert agent_shopping.load_draft(job_dir).items[0].quantity == 2


@pytest.mark.parametrize(("given", "saved"), [(1.005, 2), (2.0000000001, 2), (0.004, 1)])
def test_any_part_of_a_count_is_rounded_up(
    tools: MCPServer, job_dir: Path, given: float, saved: float
) -> None:
    refused, text = add_item(tools, "lemons", "produce", "1b", quantity=given)

    assert not refused, text
    assert agent_shopping.load_draft(job_dir).items[0].quantity == saved


@pytest.mark.parametrize(
    ("name", "quantity"), [("lemons", 0.0000001), ("   ", None)], ids=["rounds to 0", "blank"]
)
def test_an_item_that_would_be_saved_invalid_is_refused(
    tools: MCPServer, name: str, quantity: float | None
) -> None:
    refused, text = add_item(
        tools, name, "produce", "1b", quantity=quantity, more_alternatives_query="produce"
    )

    assert refused
    assert "Invalid item:\n" in text
    assert "Not covered yet: 1a (1 onion);" in call(tools, "done")[1]  # the list still loads


def test_the_worker_checks_the_lines_it_made_not_job_json(tools: MCPServer, job_dir: Path) -> None:
    # The agent can rewrite job.json, e.g. down to the one line it covered.
    write_context(job_dir, ListJob(lines=LINES[5:]))
    add_item(tools, "ground beef", "meat_seafood", "2c", quantity=8, unit="oz")
    assert not call(tools, "done")[0]

    with pytest.raises(shopping.InvalidList, match=r"^Not covered yet: 1a \(1 onion\);"):
        shopping.finished_list(job_dir, ListJob(lines=LINES))


def test_an_amount_in_a_unit_keeps_two_decimals(tools: MCPServer) -> None:
    result = add_item(tools, "ground beef", "meat_seafood", "2c", quantity=0.3333, unit="lb")

    assert result == (False, 'Added "ground beef": 0.33 lb, meat_seafood.')


@pytest.mark.parametrize(
    ("name", "lines", "amount", "problem"),
    [
        (
            "Kosher Salt",
            ["1c"],
            {},
            '"Kosher Salt" is the staple salt: add it as a staple with `add_staple` instead.',
        ),
        ("onions", ["3a"], {}, "There is no line 3a."),
        ("onions", ["1a", "1a"], {"quantity": 1}, "A line is given twice."),
        ("onions", ["1a", "2a"], {}, '"onions" needs a quantity: 1a, 2a give an amount.'),
        (
            "beef",
            ["2c"],
            {"quantity": float("inf"), "unit": "lb"},
            "quantity\n  Input should be a finite number",
        ),
        ("onions", ["1a"], {"unit": "cup"}, "A unit needs a quantity."),
        (
            "beef",
            ["2c"],
            {"quantity": 0.001, "unit": "lb"},
            "0.001 lb rounds to 0: use a smaller unit.",
        ),
        (
            "Sea-salt.",
            ["1c"],
            {},
            '"Sea-salt." is the staple salt: add it as a staple with `add_staple` instead.',
        ),
        (
            "Warm water",
            ["1c"],
            {},
            '"Warm water" is the staple water: add it as a staple with `add_staple` instead.',
        ),
        ("onions", [], {}, "lines\n  List should have at least 1 item"),
    ],
    ids=[
        "staple",
        "unknown line",
        "line twice",
        "no quantity",
        "infinite",
        "unit alone",
        "rounds to 0",
        "staple spelled",
        "warm water",
        "no lines",
    ],
)
def test_an_item_that_breaks_a_rule_is_refused_with_why(
    tools: MCPServer,
    job_dir: Path,
    name: str,
    lines: list[str],
    amount: dict[str, Any],
    problem: str,
) -> None:
    refused, text = add_item(tools, name, "pantry", *lines, **amount)

    assert refused
    assert problem in text
    assert agent_shopping.load_draft(job_dir).items == []


def test_a_line_already_covered_is_refused_naming_what_covers_it(tools: MCPServer) -> None:
    add_item(tools, "yellow onions", "produce", "1a", "2a", quantity=2)
    add_staple(tools, "salt", "1c")

    onion = add_item(tools, "red onion", "produce", "2a", quantity=1)
    pepper = add_staple(tools, "pepper", "1c")

    assert onion[0] and onion[1].endswith('Line 2a is already in "yellow onions".')
    assert pepper[0] and pepper[1].endswith("Line 1c is already in the staple salt.")


def test_an_item_or_staple_is_changed_by_removing_it_first(tools: MCPServer, job_dir: Path) -> None:
    add_item(tools, "Lemons", "produce", "1b", quantity=1)
    add_staple(tools, "salt", "1c")

    again = add_item(tools, "lemons", "produce", "1b", "2b", quantity=2)
    salt_again = add_staple(tools, "salt", "1c")
    removed = [call(tools, "remove", name="lemons"), call(tools, "remove", name="salt")]
    replaced = add_item(tools, "lemons", "produce", "1b", "2b", quantity=2)

    assert again[0] and again[1].endswith(
        '"lemons" is already on the list. To change it, `remove` it and add it again.'
    )
    assert salt_again[0] and "salt is already added." in salt_again[1]
    assert removed == [(False, 'Removed "lemons".'), (False, 'Removed "salt".')]
    assert replaced == (False, 'Added "lemons": 2, produce.')
    draft = agent_shopping.load_draft(job_dir)
    assert [(item.name, item.lines) for item in draft.items] == [("lemons", ["1b", "2b"])]
    assert draft.staples == []
    missing = call(tools, "remove", name="limes")
    assert missing[0] and missing[1].endswith('"limes" is not on the list.')


def test_a_staple_can_only_be_one_of_the_staples(tools: MCPServer, job_dir: Path) -> None:
    refused, text = add_staple(tools, "seasoned salt", "1c")

    assert refused
    assert "staple\n  Input should be 'salt', 'pepper', 'olive oil', 'vegetable oil' or" in text
    assert agent_shopping.load_draft(job_dir).staples == []


def test_done_says_what_is_not_covered_until_every_line_is(tools: MCPServer, job_dir: Path) -> None:
    add_staple(tools, "salt", "1c")
    unfinished = call(tools, "done")
    add_item(tools, "yellow onions", "produce", "1a", "2a", quantity=2)
    add_item(tools, "lemons", "produce", "1b", "2b", quantity=1.5)
    almost = call(tools, "done")
    add_item(tools, "ground beef", "meat_seafood", "2c", quantity=8, unit="oz")
    complete = call(tools, "done")

    assert unfinished[0] and unfinished[1].endswith(
        "Not covered yet: 1a (1 onion); 1b (0.5 lemon); 2a (1 cup onion, diced); "
        "2b (1 lemon); 2c (8 oz ground beef). The list has nothing to buy."
    )
    assert almost[0] and almost[1].endswith(": Not covered yet: 2c (8 oz ground beef).")
    assert complete == (False, "Complete: 3 items and 1 staples cover all 6 lines.")
    # What the worker saves: each item and staple with the meals its lines are from.
    assert shopping.finished_list(job_dir, ListJob(lines=LINES)).items == [
        ListEntry(
            name="yellow onions",
            more_alternatives_query="yellow onions",
            quantity=2,
            aisle=Aisle.PRODUCE,
            meals=[1, 2],
        ),
        ListEntry(
            name="lemons",
            more_alternatives_query="lemons",
            quantity=2,
            aisle=Aisle.PRODUCE,
            meals=[1, 2],
        ),
        ListEntry(
            name="ground beef",
            more_alternatives_query="ground beef",
            quantity=8,
            unit="oz",
            aisle=Aisle.MEAT_SEAFOOD,
            meals=[2],
        ),
        ListEntry(name="salt", aisle=Aisle.PANTRY, meals=[1], on_hand=True),
    ]


def test_the_worker_refuses_a_list_that_is_not_done(tools: MCPServer, job_dir: Path) -> None:
    add_item(tools, "ground beef", "meat_seafood", "2c", quantity=8, unit="oz")

    with pytest.raises(shopping.InvalidList, match=r"^Not covered yet: 1a \(1 onion\);"):
        shopping.finished_list(job_dir, ListJob(lines=LINES))


def test_no_tool_sends_the_agent_looking_for_the_job_directory(tools: MCPServer) -> None:
    # The server is given the job directory; what the agent reads of its tools mustn't ask for it.
    listed = asyncio.run(tools.list_tools())

    assert {tool.name for tool in listed} == {
        "adapt_recipe",
        "add_item",
        "add_staple",
        "remove",
        "done",
    }
    for tool in listed:
        assert "job" not in json.dumps(tool.model_dump(mode="json")).lower(), tool.name


def test_an_item_needs_no_quantity_when_its_lines_give_none(tmp_path: Path) -> None:
    write_context(
        tmp_path,
        ListJob(
            lines=[IngredientLine(id="1a", meal=1, text="hot sauce, to taste", has_amount=False)]
        ),
    )
    tools = shopping_list_server(tmp_path)

    result = add_item(tools, "hot sauce", "pantry", "1a")

    assert result == (False, 'Added "hot sauce": no amount, pantry.')
    assert call(tools, "done") == (False, "Complete: 1 items and 0 staples cover all 1 lines.")
