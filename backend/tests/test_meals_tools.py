"""The agent's meals tools (castle_chef_agent.server's meals server): `add_meal`
and `done`, called as the agent calls them, in a job directory as the worker
makes it."""

import asyncio
import json
from pathlib import Path
from typing import Any

import typer
from castle_chef_agent import cli
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.meals import MealsJob
from castle_chef_agent.server import meals_server
from mcp.server.mcpserver import MCPServer
from typer.testing import CliRunner

from tests.test_products_tools import call

MEAL: dict[str, Any] = {
    "name": "Creamy Tuscan Chicken",
    "description": "One-pan chicken in a garlicky cream sauce.",
    "servings": 6,
    "active_time_minutes": 25,
    "total_time_minutes": 40,
    "ingredients": [
        {"name": "chicken", "quantity": 3, "unit": "lb"},
        {"name": "garlic", "quantity": 4, "unit": "clove", "preparation": "minced"},
        {"name": "heavy cream", "quantity": 2, "unit": "cup"},
    ],
    "steps": [
        {"text": "Season the chicken.", "ingredients": ["chicken"]},
        {"text": "Sear the chicken with the garlic.", "ingredients": ["chicken", "garlic"]},
        {"text": "Simmer in the cream.", "timer_minutes": 15, "ingredients": ["heavy cream"]},
    ],
}


def batch(job_dir: Path, count: int = 20) -> MCPServer:
    """The meals tools for a batch of `count` meals, in a job directory as the worker makes it."""
    job_dir.mkdir(exist_ok=True)
    write_context(job_dir, MealsJob(count=count))
    return meals_server(job_dir)


def add(tools: MCPServer, meal: dict[str, Any]) -> tuple[bool, str]:
    return call(tools, "add_meal", meal=meal)


def saved_names(job_dir: Path) -> list[str]:
    """The names of the meals saved for the worker, in the order they were added."""
    return [
        json.loads(path.read_text())["name"] for path in sorted((job_dir / "meals").glob("*.json"))
    ]


def test_each_meal_added_is_saved_for_the_worker(tmp_path: Path) -> None:
    tools = batch(tmp_path)

    first = add(tools, MEAL)
    second = add(tools, MEAL | {"name": "Tacos"})

    assert (first, second) == ((False, 'Added "Creamy Tuscan Chicken".'), (False, 'Added "Tacos".'))
    assert saved_names(tmp_path) == ["Creamy Tuscan Chicken", "Tacos"]


def test_a_meal_without_servings_is_refused(tmp_path: Path) -> None:
    meal = {key: value for key, value in MEAL.items() if key != "servings"}

    refused, text = add(batch(tmp_path), meal)

    assert refused
    assert "meal.servings\n  Field required" in text
    assert not (tmp_path / "meals").exists()


def test_a_step_may_only_use_ingredients_the_meal_lists(tmp_path: Path) -> None:
    steps = [{"text": "Sear the chicken in butter.", "ingredients": ["chicken", "butter"]}]

    refused, text = add(batch(tmp_path), MEAL | {"steps": steps})

    assert refused
    assert 'Steps name ingredients the meal doesn\'t list: "butter".' in text
    assert not (tmp_path / "meals").exists()


def test_a_step_names_which_entry_of_an_ingredient_listed_twice(tmp_path: Path) -> None:
    ingredients = [
        {"name": "milk", "quantity": 1, "unit": "cup"},
        {"name": "Milk", "quantity": 2, "unit": "tbsp"},
    ]
    steps = [{"text": "Whisk in the milk.", "ingredients": ["milk"]}]

    refused, text = add(batch(tmp_path), MEAL | {"ingredients": ingredients, "steps": steps})

    assert refused
    assert 'lists more than once: "milk"' in text
    assert not (tmp_path / "meals").exists()


def test_a_step_timer_is_at_least_a_minute(tmp_path: Path) -> None:
    steps = [{"text": "Rest the chicken.", "timer_minutes": 0}]

    refused, text = add(batch(tmp_path), MEAL | {"steps": steps})

    assert refused
    assert "timer_minutes\n  Input should be greater than or equal to 1" in text
    assert not (tmp_path / "meals").exists()


def test_a_meal_with_fields_the_tool_doesnt_have_is_refused(tmp_path: Path) -> None:
    refused, text = add(batch(tmp_path), MEAL | {"calories": 500})

    assert refused
    assert "meal.calories\n  Extra inputs are not permitted" in text
    assert not (tmp_path / "meals").exists()


def test_meals_added_at_once_are_each_saved(tmp_path: Path) -> None:
    # The agent can call several `add_meal`s at once; the server runs each on its own thread.
    names = [f"Meal {index}" for index in range(8)]
    tools = batch(tmp_path)

    async def add_all() -> list[Any]:
        return await asyncio.gather(
            *(tools.call_tool("add_meal", {"meal": MEAL | {"name": name}}) for name in names),
            return_exceptions=True,
        )

    results = asyncio.run(add_all())

    assert not [result for result in results if isinstance(result, BaseException)]
    assert sorted(saved_names(tmp_path)) == names
    assert [path.name for path in sorted((tmp_path / "meals").glob("*.json"))] == [
        f"{number:03d}.json" for number in range(1, 9)
    ]


def test_a_meal_already_added_is_refused(tmp_path: Path) -> None:
    tools = batch(tmp_path)
    assert not add(tools, MEAL)[0]

    refused, text = add(tools, MEAL | {"name": "creamy  tuscan CHICKEN"})

    assert refused
    assert text.endswith('"creamy  tuscan CHICKEN" is already added.')
    assert saved_names(tmp_path) == ["Creamy Tuscan Chicken"]


def test_no_more_meals_than_the_batch_asks_for(tmp_path: Path) -> None:
    tools = batch(tmp_path, count=1)
    assert not add(tools, MEAL)[0]

    refused, text = add(tools, MEAL | {"name": "Tacos"})

    assert refused
    assert text.endswith("All 1 meals are already added.")
    assert saved_names(tmp_path) == ["Creamy Tuscan Chicken"]


def test_done_says_how_many_meals_are_missing_until_the_batch_is_complete(
    tmp_path: Path,
) -> None:
    tools = batch(tmp_path, count=2)
    add(tools, MEAL)

    missing = call(tools, "done")
    add(tools, MEAL | {"name": "Tacos"})
    complete = call(tools, "done")

    assert missing[0]
    assert missing[1].endswith("1 of 2 meals are added.")
    assert complete == (False, "Complete.")


def test_no_tool_sends_the_agent_looking_for_the_job_directory(tmp_path: Path) -> None:
    # The server is given the job directory; what the agent reads of its tools mustn't ask for it.
    tools = asyncio.run(batch(tmp_path).list_tools())

    assert {tool.name for tool in tools} == {"add_meal", "done"}
    for tool in tools:
        assert "job" not in json.dumps(tool.model_dump(mode="json")).lower(), tool.name


def test_the_tools_need_an_existing_job_directory(tmp_path: Path) -> None:
    # As the worker starts them, from the agent's own `castle-chef` (docker/castle-chef).
    # The refusal itself, not its printed form (Typer colors that under GitHub Actions).
    result = CliRunner().invoke(
        cli.app, ["mcp", "meals", "--job-dir", str(tmp_path / "missing")], standalone_mode=False
    )

    refusal = result.exception
    assert isinstance(refusal, typer.BadParameter)
    assert refusal.param is not None and refusal.param.name == "job_dir"
    assert not (tmp_path / "missing").exists()
