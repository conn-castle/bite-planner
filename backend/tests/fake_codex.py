"""Stands in for `codex app-server` in tests (as CASTLE_CHEF_CODEX_COMMAND): the
same JSON-RPC over stdio, answering Instacart tool calls with the replies recorded
in fixtures/instacart/.

It's configured by `fake.json` in CODEX_HOME (the only setting the client passes
it besides PATH) and logs each request it gets to `requests.jsonl` there:
- signed_in: whether ChatGPT is signed in already (default true)
- login_success: how the device sign-in ends (default true)
- accessible_after: tool calls that fail before Instacart is connected (default 0)
- refuse_cart: answer cart changes with Instacart's recorded refusal (default false)
- stop_on: a method on which it exits without answering
- drop_search_items: answer searches without their product lists (a changed reply)
- drop_cart_quantities: answer cart changes with items that have no quantity
- non_object_result_for: a method whose result is a list instead of an object
- helper_stop_seconds: start a helper in its process group (like the code-mode
  host) that takes this long to exit once signaled; its pid goes to helper.pid
- cart: a cart that keeps its changes (in cart-state.json), starting with these
  {product_id, quantity}; its items are the recorded cart's, or copies of its
  first item under another id ("Product <id>", $2.29 each)
- unavailable: product ids the stateful cart doesn't take
- sold_out: product ids the stateful cart takes out but won't put in again
- refuse_checkout: a reason the stateful cart can't be checked out other than the
  minimum (its validation label, and the checkout button's message)

Like the real app-server, it can send several messages in one write: a
notification ahead of each tool call's result, and the sign-in's result right
behind its device code.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "instacart"
INSTACART_APP = "app-691eab1e001081919e57189f8b2f03bc"

home = Path(os.environ["CODEX_HOME"])
config: dict[str, Any] = (
    json.loads((home / "fake.json").read_text()) if (home / "fake.json").exists() else {}
)
tool_calls = 0


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def send(*messages: dict[str, Any]) -> None:
    """Write the messages in one go (one read on the other side can get them all)."""
    sys.stdout.write("".join(json.dumps(message) + "\n" for message in messages))
    sys.stdout.flush()


def cents(price: str) -> int:
    dollars, _, change = price.removeprefix("$").partition(".")
    return int(dollars) * 100 + int(change or 0)


def stateful_cart(arguments: dict[str, Any]) -> dict[str, Any]:
    """The cart as it is after this call, from the recorded cart's parts."""
    path = home / "cart-state.json"
    state: dict[str, float] = (
        json.loads(path.read_text())
        if path.exists()
        else {item["product_id"]: item["quantity"] for item in config["cart"]}
    )
    for update in arguments.get("item_updates", []):
        product_id, quantity = update["product_id"], update["quantity"]
        if product_id in config.get("unavailable", []):
            continue
        if quantity > 0 and product_id in config.get("sold_out", []):
            continue
        if quantity == 0:
            state.pop(product_id, None)
        else:
            state[product_id] = quantity
    path.write_text(json.dumps(state))
    reply = fixture("cart-with-items")
    cart = reply["_meta"]["instacart/widgetData"]["data"]["cart"]
    recorded = {item["product_id"]: item for item in cart["items"]}
    items = []
    for product_id, quantity in state.items():
        item = dict(recorded.get(product_id) or cart["items"][0])
        if product_id not in recorded:
            item |= {"product_id": product_id, "name": f"Product {product_id}"}
        items.append(item | {"quantity": quantity})
    subtotal = sum(round(cents(item["items_price"]) * item["quantity"]) for item in items)
    cart["items"] = items
    cart["subtotal"] = f"${subtotal // 100}.{subtotal % 100:02d}"
    valid = subtotal >= 1000
    cart["validation"] |= {
        "status": "VALID" if valid else "INVALID",
        "label": "GO_TO_CHECKOUT" if valid else "MIN_TO_CHECKOUT",
    }
    if "refuse_checkout" in config:
        cart["validation"] |= {"status": "INVALID", "label": "STORE_CLOSED"}
        data = reply["_meta"]["instacart/widgetData"]["data"]
        data["checkout_button"]["disabled_message"] = config["refuse_checkout"]
    return reply


def tool_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool == "instacart.search_products":
        slug = re.sub(r"[^a-z0-9]+", "-", arguments["query"].lower()).strip("-")
        # The worker tests' made-up items ("item 0") get the onions'; any other query
        # needs a recorded reply.
        reply = fixture(
            "search-yellow-onions" if re.fullmatch(r"item-\d+", slug) else f"search-{slug}"
        )
        if config.get("drop_search_items"):
            del reply["_meta"]["instacart/widgetData"]["data"]["items"]
        return reply
    if tool == "instacart.cart" and "cart" in config:
        return stateful_cart(arguments)
    if tool == "instacart.cart":
        if "item_updates" not in arguments:
            return fixture("cart-empty")
        if config.get("refuse_cart"):
            return fixture("cart-refused")
        reply = fixture("cart-with-items")
        if config.get("drop_cart_quantities"):
            for item in reply["_meta"]["instacart/widgetData"]["data"]["cart"]["items"]:
                del item["quantity"]
        return reply
    raise ValueError(tool)


def answer(method: str, params: dict[str, Any]) -> dict[str, Any]:
    global tool_calls
    match method:
        case "initialize":
            return {"userAgent": "fake-codex"}
        case "account/read":
            signed_in = config.get("signed_in", True)
            return {
                "account": {"type": "chatgpt"} if signed_in else None,
                "requiresOpenaiAuth": True,
            }
        case "account/login/start":
            return {
                "type": "chatgptDeviceCode",
                "loginId": "login-1",
                "userCode": "ABCD-1234",
                "verificationUrl": "https://auth.openai.com/codex/device",
            }
        case "plugin/install":
            return {
                "appsNeedingAuth": [
                    {
                        "id": INSTACART_APP,
                        "name": "Instacart",
                        "installUrl": "https://chatgpt.com/apps/instacart/connector_691eab1e001081919e57189f8b2f03bc",
                    }
                ],
                "authPolicy": "ON_USE",
            }
        case "app/list":
            # From AWS, ChatGPT's app listing is behind a bot check.
            raise ValueError("failed to list apps: Request failed with status 403 Forbidden")
        case "thread/start":
            return {"thread": {"id": "thread-1"}}
        case "mcpServer/tool/call":
            tool_calls += 1
            if tool_calls <= config.get("accessible_after", 0):
                raise ValueError(f"Unknown tool {params['tool']}")  # not connected yet
            return tool_call(params["tool"], params["arguments"])
    raise ValueError(method)


if "helper_stop_seconds" in config:
    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, sys, time\n"
            "def stop(*_):\n"
            f"    time.sleep({config['helper_stop_seconds']})\n"
            "    sys.exit()\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "time.sleep(60)",
        ]
    )
    (home / "helper.pid").write_text(str(helper.pid))

for line in sys.stdin:
    message = json.loads(line)
    with (home / "requests.jsonl").open("a") as log:
        log.write(json.dumps(message) + "\n")
    if "id" not in message:
        continue  # a notification
    method = message["method"]
    if config.get("stop_on") == method:
        sys.exit(3)
    try:
        result: dict[str, Any] | list[object] = answer(method, message.get("params") or {})
    except (ValueError, FileNotFoundError) as error:
        error_reply = {"code": -32001, "message": f"Unknown: {error}"}
        send({"jsonrpc": "2.0", "id": message["id"], "error": error_reply})
        continue
    if config.get("non_object_result_for") == method:
        result = []
    response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
    if method == "mcpServer/tool/call":
        progress = {"method": "item/mcpToolCall/progress", "params": {"message": "calling"}}
        send({"jsonrpc": "2.0", **progress}, response)
    elif method == "account/login/start":
        success = config.get("login_success", True)
        completed = {
            "loginId": "login-1",
            "success": success,
            "error": None if success else "Denied.",
        }
        send(response, {"jsonrpc": "2.0", "method": "account/login/completed", "params": completed})
    else:
        send(response)
