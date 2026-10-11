"""Stands in for the OpenAI Responses runner in worker tests.

Usage (as CASTLE_CHEF_AGENT_COMMAND): fake_agent.py MEALS STOP_REASON EXIT_CODE SECONDS
followed by the runner arguments the worker appends. It starts the MCP
server the job directory's `tools.json` names and uses its tools: MEALS meals
with `add_meal` (or, given a shopping list prompt, MEALS items sharing its lines with
`add_item`, and salt with `add_staple`; or, given a list to shop, each item's first
search result, with the second as its alternative), then `done`; or, given checkout
to read, checkout on the stand-in pages the prompt's tools open, reporting it (MEALS 0:
finding it signed out); or, given an order to book, the window `choose_window` says
on the order's day, the report, `place_order`, and the confirmation (MEALS 0: signed
out; 3: no confirmation found after the press). It takes SECONDS and prints
runner JSON. Settings are arguments because the worker gives the agent a minimal
environment.
"""

import asyncio
import json
import os
import re
import sys
import time
from typing import Any

from castle_chef_agent.runner import AgentSummary, tool_environment
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent

MEAL = {
    "name": "Sheet Pan Sausage and Peppers",
    "servings": 4,
    "dish": "plate",
    "ingredients": [
        {
            "name": "Italian sausage",
            "quantity": 1.5,
            "unit": "lb",
            "icon": "ground_meat",
            "color": "brown",
        },
        {
            "name": "bell peppers",
            "quantity": 3,
            "preparation": "sliced",
            "icon": "pepper",
            "color": "red",
        },
    ],
    "steps": [
        {
            "text": "Roast everything at 425F.",
            "timer_minutes": 25,
            "ingredients": ["Italian sausage", "bell peppers"],
        }
    ],
}

meal_count, stop_reason, exit_code, seconds, *args = sys.argv[1:]
cwd = args[args.index("--cwd") + 1]
with open(args[args.index("--prompt-file") + 1]) as file:
    prompt = file.read()
with open(os.path.join(cwd, "fake-agent-prompt.md"), "w") as file:
    file.write(prompt)
with open(os.path.join(cwd, "fake-agent-env.json"), "w") as file:
    json.dump(sorted(os.environ), file)
with open(os.path.join(cwd, "tools.json")) as config:
    server = json.load(config)


def text_of(result: CallToolResult) -> str:
    """The text a tool answered with (its first part)."""
    part = result.content[0]
    if not isinstance(part, TextContent):
        raise SystemExit(f"Not text: {part}")
    return part.text


def found(pattern: str, text: str) -> str:
    """The first group of the pattern's match in the text."""
    match = re.search(pattern, text)
    if match is None:
        raise SystemExit(f"No {pattern!r} in {text!r}.")
    return match.group(1)


async def use_tools() -> None:
    parameters = StdioServerParameters(
        command=server["command"], args=server["args"], env=tool_environment(), cwd=cwd
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
            return text_of(result)

        def ref(page: str, pattern: str) -> str:
            match = re.search(rf"{pattern}[^\n]*\[ref=(\w+)\]|\[ref=(\w+)\]: {pattern}", page)
            if match is None:
                raise SystemExit(f"No {pattern!r} on the page.")
            return match.group(1) or match.group(2)

        booking = prompt.startswith("Book the household's")
        if booking and meal_count == "0":
            page = await page_after("open_page", {"path": "/store/sign-in"})
            await call("signed_out", {"ref": ref(page, 'button "Log in')})
        elif booking:
            # As the prompt says: checkout, the order's day and the window the rule
            # chooses, the rule's tip, the report, then Place order and what it shows.
            store = found(r"Open `(/store/[^`]+)`", prompt)
            day = found(r"Click the tab for \w+, (\w+ \d+)", prompt)
            page = await page_after("open_page", {"path": store})
            page = await page_after("click", {"ref": ref(page, 'button "View cart')})
            page = await page_after("click", {"ref": ref(page, 'button "Go to checkout')})
            page = await page_after("click", {"ref": ref(page, 'button "Schedule')})
            page = await page_after("click", {"ref": ref(page, rf'tab "\w+, {day}\b')})
            windows = re.findall(r'button "[^"]*(?:am|pm|Noon)[^"]*" \[ref=(\w+)\]', page)
            said = await page_after(
                "choose_window", {"day": ref(page, rf'tab "\w+, {day}\b'), "windows": windows}
            )
            if "Stop here" in said:
                return
            page = await page_after("click", {"ref": found(r"click (\w+)", said)})
            tip = await page_after("tip", {"items": ref(page, r"\d+ items")})
            amount = found(r"type ([\d.]+)", tip)
            page = await page_after("click", {"ref": ref(page, 'button "Other"')})
            page = await page_after(
                "type_text", {"ref": ref(page, 'textbox "Other amount"'), "text": amount}
            )
            page = await page_after("click", {"ref": ref(page, 'button "Save Tip"')})
            reported = await session.call_tool(
                "report",
                {
                    "items": ref(page, r"\d+ items"),
                    "lines": [ref(page, "Delivery with"), ref(page, "Service fee")],
                    "tip": ref(page, re.escape("$" + amount)),
                    "total": ref(page, "Total"),
                    "delivery": ref(page, 'button "[^"]*More times'),
                    "address": ref(page, 'button "1 Main Street'),
                    "card": ref(page, r'button "\*4242'),
                },
            )
            if getattr(reported, "is_error", False):
                return  # told to stop (the account changed)
            placed = await page_after("place_order", {"ref": ref(page, 'button "Place order"')})
            if not placed.startswith("Place order was pressed"):
                return
            if meal_count == "3":
                await call("not_confirmed", {})
                return
            page = await page_after("look", {})
            await call(
                "confirmed",
                {
                    "number": ref(page, '"Order #'),
                    "window": ref(page, "Arrives"),
                    "total": ref(page, "Total"),
                },
            )
        elif "at checkout on Instacart" in prompt and meal_count == "0":
            # Signed out: Instacart shows its sign-in control instead of the store.
            page = await page_after("open_page", {"path": "/store/sign-in"})
            await call("signed_out", {"ref": ref(page, 'button "Log in')})
        elif "at checkout on Instacart" in prompt:
            # As the prompt says: the cart's settings, checkout, a scheduled
            # window, the rule's tip, then the report.
            store = found(r"Open `(/store/[^`]+)`", prompt)
            page = await page_after("open_page", {"path": store})
            page = await page_after("click", {"ref": ref(page, 'button "View cart')})
            settings = re.findall(r'button "Replace with[^"]*" \[ref=(\w+)\]', page)
            await call("record_replacements", {"refs": settings})
            page = await page_after("click", {"ref": ref(page, 'button "Go to checkout')})
            page = await page_after("click", {"ref": ref(page, 'button "Schedule')})
            page = await page_after("click", {"ref": ref(page, 'button "4pm')})
            tip = await page_after("tip", {"items": ref(page, r"\d+ items")})
            amount = found(r"type ([\d.]+)", tip)
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
                    "delivery": ref(page, r'button "\w{3}, \w{3} \d+, 4pm'),
                    "address": ref(page, 'button "1 Main Street'),
                    "card": ref(page, r'button "\*4242'),
                },
            )
        elif prompt.startswith("Choose what to buy at"):
            # Each item: search for it, then choose the first result (one of it) with
            # the second as its alternative.
            for number, name in re.findall(r"^(\d+)\. (.+?)(?::[^(]*)? \(", prompt, re.MULTILINE):
                result = await session.call_tool("search", {"item": int(number), "query": name})
                ids = re.findall(r"^(\d+): ", text_of(result), re.MULTILINE)
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
        if "at checkout on Instacart" not in prompt and not booking:
            await call("done", {}, check=False)


asyncio.run(use_tools())
time.sleep(float(seconds))
print(AgentSummary.model_validate({"stopReason": stop_reason}).model_dump_json(by_alias=True))
sys.exit(int(exit_code))
