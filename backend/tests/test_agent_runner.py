"""The Responses runner through its real meal tools, without a network request."""

import asyncio
import json
import shutil
from datetime import date, time
from pathlib import Path
from typing import Self

import httpx2 as httpx
import pytest
from castle_chef_agent import runner
from castle_chef_agent.checkout import BookTarget, CheckoutJob
from castle_chef_agent.instacart import Instacart
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.meals import MealsJob, load_meals
from castle_chef_agent.runner import ToolServerConfig
from castle_chef_agent.server import (
    checkout_server,
    meals_server,
    products_server,
    shopping_list_server,
)
from mcp.client.stdio import get_default_environment
from openai import APIConnectionError, AuthenticationError
from openai.types.responses import FunctionToolParam, Response, ResponseInputParam
from openai.types.responses.response_input_item_param import FunctionCallOutput
from openai.types.shared_params.reasoning import Reasoning
from pydantic import TypeAdapter

MEAL: dict[str, object] = {
    "name": "Sausage and Peppers",
    "servings": 4,
    "dish": "plate",
    "ingredients": [{"name": "sausage", "icon": "ground_meat", "color": "brown"}],
    "steps": [{"text": "Roast the sausage.", "ingredients": ["sausage"]}],
}


class FakeOpenAI:
    def __init__(self, error: BaseException | None = None, incomplete: bool = False) -> None:
        self.responses = self
        self.inputs: list[tuple[str | ResponseInputParam, str | None]] = []
        self.error = error
        self.incomplete = incomplete

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_errors: object) -> None:
        pass

    async def create(
        self,
        *,
        model: str,
        reasoning: Reasoning,
        input: str | ResponseInputParam,
        tools: list[FunctionToolParam],
        previous_response_id: str | None,
    ) -> Response:
        if self.error is not None:
            raise self.error
        assert model == "gpt-6.1-sol"
        assert reasoning["effort"] == "high"
        assert {tool["name"] for tool in tools} == {"add_meal", "done"}
        if self.incomplete:
            return Response.model_validate(
                {
                    "id": "resp_incomplete",
                    "created_at": 0,
                    "model": model,
                    "object": "response",
                    "output": [],
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                }
            )
        self.inputs.append((input, previous_response_id))
        turn = len(self.inputs)
        calls: list[dict[str, object]] = []
        if turn == 1:
            calls.append(
                {
                    "type": "function_call",
                    "name": "add_meal",
                    "arguments": json.dumps({"meal": MEAL}),
                    "call_id": "call_meal",
                }
            )
        elif turn == 2:
            calls.append(
                {"type": "function_call", "name": "done", "arguments": "{}", "call_id": "call_done"}
            )
        return Response.model_validate(
            {
                "id": f"resp_{turn}",
                "created_at": 0,
                "model": model,
                "object": "response",
                "output": calls,
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "status": "completed",
            }
        )


def test_runner_gives_only_meal_tools_and_returns_their_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = shutil.which("castle-chef")
    assert launcher is not None
    write_context(tmp_path, MealsJob(count=1))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Create one meal and call done.")
    (tmp_path / "tools.json").write_text(
        ToolServerConfig(
            command=launcher, args=["mcp", "meals", "--job-dir", str(tmp_path)]
        ).model_dump_json()
    )
    fake = FakeOpenAI()
    monkeypatch.setattr(runner, "AsyncOpenAI", lambda: fake)

    result = asyncio.run(runner.run(tmp_path, prompt))

    assert result.stop_reason == "end_turn"
    assert [meal.name for meal in load_meals(tmp_path)] == ["Sausage and Peppers"]
    assert [previous for _, previous in fake.inputs] == [None, "resp_1", "resp_2"]
    for pending, _ in fake.inputs[1:]:
        assert isinstance(pending, list)
        output = TypeAdapter(FunctionCallOutput).validate_python(pending[0])["output"]
        assert isinstance(output, str)
        assert '"isError":false' in output
    assert (tmp_path / "responses.jsonl").read_text().count("function_call_output") == 2


@pytest.mark.parametrize("kind", ["meals", "shopping-list", "products", "quote", "book"])
def test_every_job_offers_its_mcp_schemas_as_functions(tmp_path: Path, kind: str) -> None:
    if kind == "meals":
        server = meals_server(tmp_path)
    elif kind == "shopping-list":
        server = shopping_list_server(tmp_path)
    elif kind == "products":
        server = products_server(tmp_path, Instacart.__new__(Instacart))
    else:
        book = (
            BookTarget(day=date(2026, 10, 15), by=time(16), address="1 Main St", card="Visa")
            if kind == "book"
            else None
        )
        write_context(
            tmp_path,
            CheckoutJob(
                store="Wegmans",
                tip_percent=10,
                tip_max_cents=1500,
                cart_items=1,
                session_dir=tmp_path,
                book=book,
            ),
        )
        server = checkout_server(tmp_path)
    offered = asyncio.run(server.list_tools())

    functions = runner.function_tools(offered)

    assert {function["name"] for function in functions} == {tool.name for tool in offered}
    assert all(
        function["parameters"] is not None and function["parameters"].get("type") == "object"
        for function in functions
    )
    assert all(function["strict"] is False for function in functions)
    if kind == "book":
        assert "place_order" in {function["name"] for function in functions}
    if kind == "quote":
        assert "place_order" not in {function["name"] for function in functions}


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            AuthenticationError(
                "Incorrect API key provided: sk-proj-****WXYZ",
                response=httpx.Response(
                    401,
                    request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
                    headers={"x-request-id": "req_abc"},
                ),
                body=None,
            ),
            "OpenAI API request failed (status 401, request req_abc).",
        ),
        (
            APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com/v1/responses")
            ),
            "OpenAI API connection failed: connection error.",
        ),
    ],
)
def test_api_errors_are_reported_after_mcp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException,
    message: str,
) -> None:
    launcher = shutil.which("castle-chef")
    assert launcher is not None
    write_context(tmp_path, MealsJob(count=1))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Create one meal.")
    (tmp_path / "tools.json").write_text(
        ToolServerConfig(
            command=launcher, args=["mcp", "meals", "--job-dir", str(tmp_path)]
        ).model_dump_json()
    )
    monkeypatch.setattr(runner, "AsyncOpenAI", lambda: FakeOpenAI(error))

    assert runner.main(tmp_path, prompt) == 1

    stderr = capsys.readouterr().err
    assert stderr.strip().splitlines()[-1] == message
    assert "WXYZ" not in stderr


def test_incomplete_response_fails_with_actionable_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = shutil.which("castle-chef")
    assert launcher is not None
    write_context(tmp_path, MealsJob(count=1))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Create one meal.")
    (tmp_path / "tools.json").write_text(
        ToolServerConfig(
            command=launcher, args=["mcp", "meals", "--job-dir", str(tmp_path)]
        ).model_dump_json()
    )
    monkeypatch.setattr(runner, "AsyncOpenAI", lambda: FakeOpenAI(incomplete=True))

    assert runner.main(tmp_path, prompt) == 1

    assert (
        "OpenAI response resp_incomplete ended with status incomplete"
        in (capsys.readouterr().err.strip().splitlines()[-1])
    )


def test_tool_process_does_not_receive_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "project-key-for-test")
    monkeypatch.setenv("DATABASE_URL", "database-secret-for-test")

    tool_env = get_default_environment() | runner.tool_environment()

    assert "OPENAI_API_KEY" not in tool_env
    assert "DATABASE_URL" not in tool_env
