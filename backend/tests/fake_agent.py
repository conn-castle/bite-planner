"""Stands in for `al grok` in worker tests.

Usage (as CASTLE_CHEF_AGENT_COMMAND): fake_agent.py MEALS STOP_REASON EXIT_CODE SECONDS
followed by the Grok arguments the worker appends. Like Grok, it starts the MCP
server the job directory's `.grok/config.toml` names and uses its tools: MEALS meals
with `add_meal` (or, given a shopping list prompt, MEALS items sharing its lines with
`add_item`, and salt with `add_staple`), then `done`. It takes SECONDS and prints
Grok-style JSON. Settings are arguments because the worker gives the agent a minimal
environment.
"""

import asyncio
import json
import os
import re
import sys
import time
import tomllib
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

MEAL = {
    "name": "Sheet Pan Sausage and Peppers",
    "servings": 4,
    "ingredients": [
        {"name": "Italian sausage", "quantity": 1.5, "unit": "lb"},
        {"name": "bell peppers", "quantity": 3, "preparation": "sliced"},
    ],
    "steps": ["Roast everything at 425F for 25 minutes."],
}

meal_count, stop_reason, exit_code, seconds, *args = sys.argv[1:]
cwd = args[args.index("--cwd") + 1]
with open(args[args.index("--prompt-file") + 1]) as file:
    prompt = file.read()
with open(os.path.join(cwd, "fake-agent-prompt.md"), "w") as file:
    file.write(prompt)
with open(os.path.join(cwd, "fake-agent-env.json"), "w") as file:
    json.dump(sorted(os.environ), file)
with open(os.path.join(cwd, ".grok", "config.toml"), "rb") as file:
    server = tomllib.load(file)["mcp_servers"]["castle-chef"]


async def use_tools() -> None:
    parameters = StdioServerParameters(
        command=server["command"], args=server["args"], env=dict(os.environ), cwd=cwd
    )
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def call(tool: str, arguments: dict[str, Any], check: bool = True) -> None:
            result = await session.call_tool(tool, arguments)
            if check and getattr(result, "is_error", False):
                raise SystemExit(f"{tool} failed: {result}")

        if "`add_item`" in prompt:
            lines = re.findall(r"^- (\d+[a-z]+): (.*)$", prompt, re.MULTILINE)
            salt = [line for line, text in lines if text.split(",")[0].endswith("salt")]
            rest = [line for line, text in lines if line not in salt]
            for index in range(int(meal_count)):
                if covered := rest[index :: int(meal_count)]:
                    await call(
                        "add_item",
                        {
                            "name": f"item {index}",
                            "quantity": 2,
                            "aisle": "pantry",
                            "lines": covered,
                        },
                    )
            if salt:
                await call("add_staple", {"staple": "salt", "lines": salt})
        else:
            for index in range(int(meal_count)):
                await call("add_meal", {"meal": MEAL | {"name": f"{MEAL['name']} {index}"}})
        await call("done", {}, check=False)


asyncio.run(use_tools())
time.sleep(float(seconds))
print(json.dumps({"text": "done", "stopReason": stop_reason}))
sys.exit(int(exit_code))
