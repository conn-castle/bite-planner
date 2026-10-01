"""Stands in for `al grok` in worker tests.

Usage (as CASTLE_CHEF_AGENT_COMMAND): fake_agent.py MEALS STOP_REASON EXIT_CODE SECONDS
followed by the Grok arguments the worker appends. Like Grok, it starts the MCP
server the job directory's `.grok/config.toml` names and uses its tools: MEALS meals
with `add_meal` (or, given a shopping list prompt, MEALS items sharing its lines with
`add_item`, and salt with `add_staple`; or, given a list to shop, each item's first
search result, with the second as its alternative), then `done`; or, given checkout
to read, checkout on the stand-in pages the prompt's tools open, reporting it (MEALS 0:
finding it signed out). It takes SECONDS and prints
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

        async def page_after(tool: str, arguments: dict[str, Any]) -> str:
            result = await session.call_tool(tool, arguments)
            if getattr(result, "is_error", False):
                raise SystemExit(f"{tool} failed: {result}")
            return result.content[0].text

        def ref(page: str, pattern: str) -> str:
            match = re.search(rf"{pattern}[^\n]*\[ref=(\w+)\]|\[ref=(\w+)\]: {pattern}", page)
            if match is None:
                raise SystemExit(f"No {pattern!r} on the page.")
            return match.group(1) or match.group(2)

        if "at checkout on Instacart" in prompt and meal_count == "0":
            # Signed out: Instacart shows its sign-in control instead of the store.
            page = await page_after("open_page", {"path": "/store/sign-in"})
            await call("signed_out", {"ref": ref(page, 'button "Log in')})
        elif "at checkout on Instacart" in prompt:
            # As the prompt says: the cart's settings, checkout, a scheduled
            # window, the rule's tip, then the report.
            store = re.search(r"Open `(/store/[^`]+)`", prompt).group(1)
            page = await page_after("open_page", {"path": store})
            page = await page_after("click", {"ref": ref(page, 'button "View cart')})
            settings = re.findall(r'button "Replace with[^"]*" \[ref=(\w+)\]', page)
            await call("record_replacements", {"refs": settings})
            page = await page_after("click", {"ref": ref(page, 'button "Go to checkout')})
            page = await page_after("click", {"ref": ref(page, 'button "Schedule')})
            page = await page_after("click", {"ref": ref(page, 'button "4pm')})
            tip = await page_after("tip", {"items": ref(page, r"\d+ items")})
            amount = re.search(r"type ([\d.]+)", tip).group(1)
            page = await page_after("click", {"ref": ref(page, 'button "Other"')})
            page = await page_after(
                "type_text", {"ref": ref(page, 'textbox "Other amount"'), "text": amount}
            )
            page = await page_after("click", {"ref": ref(page, 'button "Save Tip"')})
            await call(
                "report",
                {
                    "items": ref(page, r"\d+ items"),
                    "lines": [ref(page, "Delivery with"), ref(page, "Service fee")],
                    "tip": ref(page, re.escape("$" + amount)),
                    "total": ref(page, "Total"),
                    "delivery": ref(page, 'button "Tomorrow'),
                    "address": ref(page, 'button "1 Main Street'),
                    "card": ref(page, r'button "\*4242'),
                },
            )
        elif prompt.startswith("Choose what to buy at"):
            # Each item: search for it, then choose the first result (one of it) with
            # the second as its alternative.
            for number, name in re.findall(r"^(\d+)\. (.+?)(?::[^(]*)? \(", prompt, re.MULTILINE):
                result = await session.call_tool("search", {"item": int(number), "query": name})
                ids = re.findall(r"^(\d+): ", result.content[0].text, re.MULTILINE)
                await call(
                    "choose",
                    {
                        "item": int(number),
                        "product_id": ids[0],
                        "count": 1,
                        "alternatives": [{"product_id": ids[1], "count": 1}],
                    },
                )
        elif "`add_item`" in prompt:
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
        if "at checkout on Instacart" not in prompt:
            await call("done", {}, check=False)


asyncio.run(use_tools())
time.sleep(float(seconds))
print(json.dumps({"text": "done", "stopReason": stop_reason}))
sys.exit(int(exit_code))
