"""Instacart, through its own tools in the ChatGPT apps: `codex app-server` calls
them directly (no model runs) with the household's ChatGPT sign-in (CODEX_HOME).
Searching a store and reading or changing its cart.

The tools' replies carry what ChatGPT's widgets show (sizes, prices, pictures,
"Buy it again") in `_meta`; only that widget data is read. The rest of `_meta`
includes the widget's own Instacart token, so it is never kept or logged.
"""

import fcntl
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# The Instacart app's tools, on the app-server's ChatGPT apps MCP server.
APPS_SERVER = "codex_apps"
SEARCH_TOOL = "instacart.search_products"
CART_TOOL = "instacart.cart"
WIDGET_DATA = "instacart/widgetData"
# The badge Instacart shows on products the household has bought before.
BOUGHT_BEFORE_BADGE = "Buy it again"

type Json = dict[str, Any]


class InstacartError(Exception):
    """Instacart (or the app-server in front of it) refused or failed a call."""


def cents(price: str | None) -> int | None:
    """ "$1,234.56" as 123456 cents; None for no price."""
    if price is None:
        return None
    match = re.fullmatch(r"\$([\d,]+)(?:\.(\d{2}))?", price.strip())
    if match is None:
        raise InstacartError(f"Unexpected price {price!r}.")
    return int(match.group(1).replace(",", "")) * 100 + int(match.group(2) or 0)


class _Badge(BaseModel):
    label: str | None = None


class _QuantityAttributes(BaseModel):
    request_unit: str | None = None
    variable_weight: bool = False
    cost_unit: str | None = None
    estimated_weight: float | None = None
    estimated_total_price: str | None = None


class _WidgetProduct(BaseModel):
    """A product as the widget data gives it (search results and cart items)."""

    product_id: str
    name: str
    size: str | None = None
    items_price: str | None = None
    image_url: str | None = None
    quantity: float = 0
    quantity_type: str | None = None
    quantity_attributes: _QuantityAttributes = Field(default_factory=_QuantityAttributes)
    badges: list[_Badge] | None = None


class Product(BaseModel):
    """A product at the store, as a shopper sees it."""

    model_config = ConfigDict(frozen=True)

    product_id: str
    name: str
    # As the store labels it: "2 lb", "13.5 fl oz", or "per lb" for weighed items.
    size: str | None
    # The price of one: one package (estimated for weighed items), bag, or piece.
    price_cents: int | None
    # Weighed items (sold by the pound, bought by the package): the price per
    # pound and a package's estimated weight.
    price_per_lb_cents: int | None
    estimated_lb: float | None
    image_url: str | None
    bought_before: bool

    @classmethod
    def from_widget(cls, product: _WidgetProduct) -> Self:
        attributes = product.quantity_attributes
        weighed = attributes.variable_weight and attributes.cost_unit == "lb"
        return cls(
            product_id=product.product_id,
            name=product.name,
            size=product.size,
            price_cents=cents(attributes.estimated_total_price if weighed else product.items_price),
            price_per_lb_cents=cents(product.items_price) if weighed else None,
            estimated_lb=attributes.estimated_weight if weighed else None,
            image_url=product.image_url,
            bought_before=any(badge.label == BOUGHT_BEFORE_BADGE for badge in product.badges or []),
        )


class CartItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: Product
    # How many: packages for weighed items, otherwise pieces or packs.
    quantity: float


class Cart(BaseModel):
    """The store's cart (one per store) as Instacart has it now."""

    model_config = ConfigDict(frozen=True)

    items: list[CartItem]
    subtotal_cents: int | None
    # Whether checkout is allowed; if not, why (e.g. "$10 Min. to checkout").
    can_check_out: bool
    blocked_because: str | None


class CartUpdate(BaseModel):
    """Set a product's quantity in the cart: 0 takes it out."""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(pattern=r"^\d+$")
    quantity: float = Field(ge=0, allow_inf_nan=False)


class _Validation(BaseModel):
    status: str | None = None


class _CheckoutButton(BaseModel):
    disabled_message: str | None = None


class _WidgetCartItem(_WidgetProduct):
    quantity: float  # required in the cart (search results have none)


class _WidgetCart(BaseModel):
    items: list[_WidgetCartItem]
    subtotal: str | None = None
    validation: _Validation = Field(default_factory=_Validation)


class _CartData(BaseModel):
    cart: _WidgetCart
    checkout_button: _CheckoutButton = Field(default_factory=_CheckoutButton)


class _SearchData(BaseModel):
    items: list[_WidgetProduct]
    related_items: list[_WidgetProduct]


class _Thread(BaseModel):
    id: str


class _ThreadStarted(BaseModel):
    thread: _Thread


class SearchResults(BaseModel):
    """A search's products: those matching the query, and related ones Instacart
    suggests (all of them when nothing matches, e.g. an item the store doesn't carry)."""

    model_config = ConfigDict(frozen=True)

    matches: list[Product]
    related: list[Product]


class AppServer:
    """`codex app-server` over stdio (JSON-RPC, one message per line): started on
    entry, with one thread for tool calls, and stopped on exit."""

    def __init__(
        self, codex_home: Path, command: list[str], timeout_seconds: float, uid: int | None = None
    ) -> None:
        self.codex_home = codex_home
        self.command = command
        self.timeout_seconds = timeout_seconds
        # Run as this user (the worker, as root, runs it as the agent's user, who owns
        # the login); None: as this process's user.
        self.uid = uid
        self._next_id = 0
        self._thread_id: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        # What's been read but not yet split into messages (one per line).
        self._buffer = b""
        self._workdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Self:
        self._workdir = tempfile.TemporaryDirectory(prefix="codex-")
        if self.uid is not None:
            os.chown(self._workdir.name, self.uid, self.uid)
        env = {name: os.environ[name] for name in ("PATH", "LANG") if name in os.environ}
        # Its home is its own scratch directory; the login is in CODEX_HOME.
        env |= {"HOME": self._workdir.name, "CODEX_HOME": str(self.codex_home)}
        try:
            self._process = subprocess.Popen(
                [*self.command, "app-server", "--enable", "apps", "--enable", "plugins"],
                cwd=self._workdir.name,
                env=env,
                user=self.uid,
                group=self.uid,
                extra_groups=[] if self.uid is not None else None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # Its log can include tokens at verbose levels: never kept.
                stderr=subprocess.DEVNULL,
                # Its own process group, so stopping it stops the helpers it starts
                # (the code-mode host) too.
                start_new_session=True,
            )
        except OSError as error:
            self._stop()
            raise InstacartError(f"Couldn't start the Codex app-server: {error}") from error
        try:
            self.request("initialize", {"clientInfo": {"name": "castle-chef", "version": "1"}})
            self.notify("initialized")
        except BaseException:
            # Nothing may keep using the login once the caller lets go of its lock.
            self._stop()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self._stop()

    def _stop(self) -> None:
        """Stop the app-server and everything it started, and remove its directory."""
        if self._process is not None:
            # The whole group, not just the app-server: a helper still running could
            # use the login after the caller lets go of its lock.
            for stop in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self._process.pid, stop)
                except ProcessLookupError:
                    break
                if self._group_exited(within_seconds=10):
                    break
            self._process.wait()
            for pipe in (self._process.stdin, self._process.stdout):
                if pipe is not None:
                    pipe.close()
            self._process = None
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None

    def _group_exited(self, within_seconds: float) -> bool:
        """Wait for every process in the app-server's group to exit."""
        assert self._process is not None
        deadline = time.monotonic() + within_seconds
        while time.monotonic() < deadline:
            self._process.poll()  # reap the app-server itself
            try:
                os.killpg(self._process.pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def notify(self, method: str, params: Json | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: Json) -> Json:
        """Send a request and return its result; InstacartError on an error reply."""
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        message = self.wait_for(lambda m: m.get("id") == request_id, self.timeout_seconds)
        if "error" in message:
            error = message["error"]
            detail = str(error.get("message", error) if isinstance(error, dict) else error)
            # Kept short: a failed web request's page can be in it (e.g. a bot check).
            raise InstacartError(f"{method} failed: {' '.join(detail.split())[:300]}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise InstacartError(
                f"{method}'s reply wasn't as expected: its result isn't an object."
            )
        return result

    def wait_for(self, matches: Callable[[Json], bool], timeout_seconds: float) -> Json:
        """The next message `matches` accepts (others are skipped), within the timeout."""
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout.fileno()
        deadline = time.monotonic() + timeout_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(stdout, selectors.EVENT_READ)
            while True:
                # Messages already read come first: several can arrive in one read.
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise InstacartError(
                            "The Codex app-server sent something that isn't JSON-RPC."
                        ) from error
                    if not isinstance(message, dict):
                        raise InstacartError(
                            "The Codex app-server sent a message that isn't an object."
                        )
                    if matches(message):
                        return message
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise InstacartError("The Codex app-server didn't answer in time.")
                try:
                    chunk = os.read(stdout, 65536)
                except OSError as error:
                    raise InstacartError(
                        f"Couldn't read from the Codex app-server: {error}"
                    ) from error
                if not chunk:
                    raise InstacartError(
                        f"The Codex app-server stopped (exit status {self._process.wait()})."
                    )
                self._buffer += chunk

    def call_tool(self, tool: str, arguments: Json) -> Json:
        """Call one of the ChatGPT apps' tools; its result (content, structuredContent,
        _meta). InstacartError if the tool reports an error."""
        if self._thread_id is None:
            thread = self.request(
                "thread/start",
                {
                    "cwd": self._workdir.name if self._workdir else "/tmp",
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                },
            )
            try:
                self._thread_id = _ThreadStarted.model_validate(thread).thread.id
            except ValidationError as error:
                raise InstacartError(
                    f"thread/start's reply wasn't as expected: {_problems(error)}"
                ) from error
        result = self.request(
            "mcpServer/tool/call",
            {
                "threadId": self._thread_id,
                "server": APPS_SERVER,
                "tool": tool,
                "arguments": arguments,
            },
        )
        if result.get("isError"):
            content = result.get("content")
            parts = content if isinstance(content, list) else []
            texts = [str(part.get("text", "")) for part in parts if isinstance(part, dict)]
            raise InstacartError(f"Instacart refused {tool}: {' '.join(texts).strip()}")
        return result

    def _send(self, message: Json) -> None:
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(message).encode() + b"\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as error:  # a closed pipe, or a stopped server
            raise InstacartError(
                f"The Codex app-server stopped (exit status {self._process.poll()})."
            ) from error


def _problems(error: ValidationError) -> str:
    """Where a reply didn't validate and why, without its values (which include the
    household's addresses)."""
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'reply'}: {problem['msg']}"
        for problem in error.errors(include_url=False, include_input=False, include_context=False)
    )


def _widget_data[T: BaseModel](result: Json, model: type[T], tool: str) -> T:
    meta = result.get("_meta")
    widget = meta.get(WIDGET_DATA) if isinstance(meta, dict) else None
    data = widget.get("data") if isinstance(widget, dict) else None
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise InstacartError(
            f"Instacart's {tool} reply wasn't as expected: {_problems(error)}"
        ) from error


class Instacart:
    """One store at Instacart, through an app-server session."""

    def __init__(self, server: AppServer, retailer: str) -> None:
        self.server = server
        self.retailer = retailer

    def search(self, query: str) -> SearchResults:
        """The store's products for the query (up to 10 in all), best first."""
        result = self.server.call_tool(
            SEARCH_TOOL, {"query": query, "retailer_name": self.retailer}
        )
        data = _widget_data(result, _SearchData, SEARCH_TOOL)
        return SearchResults(
            matches=[Product.from_widget(p) for p in data.items],
            related=[Product.from_widget(p) for p in data.related_items],
        )

    def cart(self) -> Cart:
        return self._cart({"retailer_name": self.retailer})

    def update_cart(self, updates: list[CartUpdate]) -> Cart:
        """Set these products' quantities (0 takes one out); the cart after."""
        return self._cart(
            {
                "retailer_name": self.retailer,
                "item_updates": [update.model_dump() for update in updates],
            }
        )

    def _cart(self, arguments: Json) -> Cart:
        data = _widget_data(self.server.call_tool(CART_TOOL, arguments), _CartData, CART_TOOL)
        valid = data.cart.validation.status == "VALID"
        return Cart(
            items=[
                CartItem(product=Product.from_widget(i), quantity=i.quantity)
                for i in data.cart.items
            ],
            subtotal_cents=cents(data.cart.subtotal),
            can_check_out=valid,
            # The button's message says why (the button itself reports enabled regardless).
            blocked_because=None
            if valid
            else data.checkout_button.disabled_message or "Checkout isn't allowed.",
        )


@contextmanager
def codex_lock(lock_file: Path | None, uid: int | None = None) -> Iterator[None]:
    """Hold the Codex login's lock (a file next to it): one app-server at a time uses
    the login, since two refreshing it at once can invalidate it. The file belongs
    to `uid` (the login's owner), so every user of the login can open it."""
    if lock_file is None:
        yield
        return
    with lock_file.open("a") as lock:  # closing it releases the lock
        if uid is not None and os.geteuid() == 0:
            os.fchown(lock.fileno(), uid, uid)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def connect(
    codex_home: Path,
    retailer: str,
    *,
    lock_file: Path | None = None,
    command: list[str] | None = None,
    timeout_seconds: float = 120,
    uid: int | None = None,
) -> Iterator[Instacart]:
    """The store at Instacart, for the length of the block (holding the Codex lock)."""
    with (
        codex_lock(lock_file, uid),
        AppServer(codex_home, command or ["codex"], timeout_seconds, uid) as server,
    ):
        yield Instacart(server, retailer)
