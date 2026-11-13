"""Run one Castle Chef job with the OpenAI Responses API and its MCP tools."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Tool
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.responses import FunctionToolParam, ResponseFunctionToolCall, ResponseInputParam
from openai.types.shared_params.reasoning import Reasoning
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

MODEL = "gpt-6.1-sol"
MAX_TURNS = 60
ARGUMENTS = TypeAdapter(dict[str, object])


class ToolServerConfig(BaseModel):
    command: str
    args: list[str]


class AgentUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class AgentSummary(BaseModel):
    stop_reason: Literal["end_turn", "max_turns"] = Field(alias="stopReason")
    model: str | None = None
    turns: int | None = None
    response_id: str | None = None
    usage: AgentUsage | None = None


def tool_environment() -> dict[str, str]:
    # The tool server needs its browser and Codex paths, never the OpenAI API key.
    return {
        name: os.environ[name]
        for name in (
            "HOME",
            "LANG",
            "PATH",
            "PLAYWRIGHT_BROWSERS_PATH",
            "CASTLE_CHEF_TITLE_MODEL_DIR",
        )
        if name in os.environ
    }


def function_tools(offered: list[Tool]) -> list[FunctionToolParam]:
    """Offer one job's MCP schemas as Responses API functions."""
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            "strict": False,
        }
        for tool in offered
    ]


async def run(job_dir: Path, prompt_path: Path) -> AgentSummary:
    """Give the model only this job's tools and return its final run summary."""
    config = ToolServerConfig.model_validate_json((job_dir / "tools.json").read_text())
    parameters = StdioServerParameters(
        command=config.command, args=config.args, env=tool_environment(), cwd=job_dir
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write) as session,
        AsyncOpenAI() as client,
    ):
        await session.initialize()
        offered = (await session.list_tools()).tools
        tools = function_tools(offered)
        names = {tool.name for tool in offered}
        previous_id: str | None = None
        pending: str | ResponseInputParam = prompt_path.read_text()
        input_tokens = 0
        output_tokens = 0
        with (job_dir / "responses.jsonl").open("w") as transcript:
            for turn in range(1, MAX_TURNS + 1):
                response = await client.responses.create(
                    model=MODEL,
                    reasoning=Reasoning(effort="high"),
                    input=pending,
                    tools=tools,
                    previous_response_id=previous_id,
                )
                transcript.write(response.model_dump_json(exclude_none=True) + "\n")
                transcript.flush()
                if response.usage is not None:
                    input_tokens += response.usage.input_tokens
                    output_tokens += response.usage.output_tokens
                if response.status != "completed":
                    raise RuntimeError(
                        f"OpenAI response {response.id} ended with status {response.status}: "
                        f"{response.error or response.incomplete_details}"
                    )
                calls = [
                    item for item in response.output if isinstance(item, ResponseFunctionToolCall)
                ]
                if not calls:
                    return AgentSummary(
                        stopReason="end_turn",
                        model=MODEL,
                        turns=turn,
                        response_id=response.id,
                        usage=AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens),
                    )
                pending = []
                for call in calls:
                    if call.name not in names:
                        raise RuntimeError(f"OpenAI called unknown tool {call.name!r}.")
                    try:
                        arguments = ARGUMENTS.validate_json(call.arguments)
                        result = await session.call_tool(call.name, arguments)
                        output = result.model_dump_json(by_alias=True, exclude_none=True)
                    except (ValueError, ValidationError) as error:
                        output = json.dumps({"isError": True, "message": str(error)})
                    pending.append(
                        {"type": "function_call_output", "call_id": call.call_id, "output": output}
                    )
                    transcript.write(json.dumps(pending[-1]) + "\n")
                    transcript.flush()
                previous_id = response.id
    return AgentSummary(
        stopReason="max_turns",
        model=MODEL,
        turns=MAX_TURNS,
        usage=AgentUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _causes(error: BaseException) -> list[BaseException]:
    """Unwrap errors collected by the MCP client's nested task groups."""
    if isinstance(error, BaseExceptionGroup):
        return [cause for nested in error.exceptions for cause in _causes(nested)]
    return [error]


def _error_message(error: BaseException) -> str:
    causes = _causes(error)
    status = next((cause for cause in causes if isinstance(cause, APIStatusError)), None)
    if isinstance(status, APIStatusError):
        return (
            f"OpenAI API request failed (status {status.status_code}, request {status.request_id})."
        )
    connection = next((cause for cause in causes if isinstance(cause, APIConnectionError)), None)
    if isinstance(connection, APIConnectionError):
        reason = str(connection.__cause__) if connection.__cause__ else "connection error"
        return f"OpenAI API connection failed: {' '.join(reason.split())[:300]}."
    cause = causes[0]
    if isinstance(cause, KeyboardInterrupt):
        return "Agent interrupted."
    detail = " ".join(str(cause).split())[:300]
    return f"{type(cause).__name__}: {detail or 'no detail'}"


def main(job_dir: Path, prompt_path: Path) -> int:
    try:
        result = asyncio.run(run(job_dir, prompt_path))
    except BaseException as error:
        import traceback

        api_error = any(
            isinstance(cause, (APIStatusError, APIConnectionError)) for cause in _causes(error)
        )
        if not api_error:
            traceback.print_exception(error)
        print(_error_message(error), file=sys.stderr)
        return 1
    print(result.model_dump_json(by_alias=True, exclude_none=True), flush=True)
    return 0
