"""Checkout on Instacart's website, driven by the agent (a `quote` job): a headless
browser signed in with the household's session, which the agent works through
tools that see the page as its accessibility tree (elements by role and name,
each with a ref) and act on those refs. No page's layout is built into the code;
what is, is the rules around it:

- the agent never presses Place order (the click tool refuses it);
- it only goes to Instacart's own pages;
- what it reports is read from the elements it points at, not taken from it,
  and must add up: the items, each other line, and the tip make the total, the
  tip follows the household's rule, and the delivery isn't Priority.

The session's cookies are secrets: the worker puts them in the job directory for
the tools, takes the renewed ones back, and removes both; they never reach the
agent's context or the logs.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from castle_chef_agent.jobfiles import locked, read_context, replace_file

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Locator, Page

SITE = "https://www.instacart.com"
# The household's session, as the worker hands it over, and as the tools hand it back,
# in their own directory (outside the job directory, which is kept after the job).
SESSION_FILE = "session.json"
RENEWED_SESSION_FILE = "session-renewed.json"
STATE_FILE = "checkout.json"
# How long a control may take to act, and a page to load.
TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT_MS = 60_000
# After an action, the page is shown once its tree stops changing (it loads and
# updates in place): checked this often, for at most this long.
SETTLE_CHECK_MS = 750
SETTLE_MAX_MS = 15_000
# The most of a page's tree shown at once: under what the agent's harness
# shows of a tool's reply (a store's front page is far longer).
MAX_SNAPSHOT_CHARS = 15_000
# The most elements `find` lists.
MAX_FOUND = 40
PLACE_ORDER = re.compile(r"place\s*order", re.I)
INSTACART_COOKIE = re.compile(r"(^|\.)instacart\.com$")


class CheckoutRefused(Exception):
    """A tool call breaks a rule; the message says which and what to do."""


class CheckoutJob(BaseModel):
    """The worker's context for the tools: the store, the tip rule, how many
    products the cart holds, and where the session is."""

    store: str
    tip_percent: int
    tip_max_cents: int
    cart_items: int
    session_dir: Path
    # Instacart's website: the worker passes its setting (tests point it at stand-ins).
    site: str = SITE


class QuoteLine(BaseModel):
    """One line of checkout's summary, as Instacart labels it: "Service fee", $2.49."""

    label: str
    cents: int


class Checkout(BaseModel):
    """What checkout showed for the cart, with a scheduled delivery and the tip rule."""

    # The items' total ("30 items"), with the store's loyalty prices.
    items_cents: int
    # The summary's other lines, in order (delivery, service fee, tax, ...).
    fees: list[QuoteLine]
    tip_cents: int
    total_cents: int
    # Where it's delivered (the account's default address) and the card it's
    # charged to ("Visa ending 4242").
    address: str
    card: str
    # What Instacart does when an item is out of stock, per item in the cart:
    # the different settings ("Replace with best match").
    replacements: list[str]


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacements: list[str] | None = None
    checkout: Checkout | None = None
    signed_out: bool = False


def load_state(job_dir: Path) -> _State:
    path = job_dir / STATE_FILE
    return _State.model_validate_json(path.read_text()) if path.exists() else _State()


def _save(job_dir: Path, state: _State) -> None:
    replace_file(job_dir / STATE_FILE, state.model_dump_json())


def tip_cents(items_cents: int, percent: int, max_cents: int) -> int:
    """The tip rule: a percentage of the items' total (to the nearest cent, half
    up), never more than the maximum."""
    return min((items_cents * percent + 50) // 100, max_cents)


_MONEY = re.compile(r"(-)?\$([\d,]+)(?:\.(\d{2}))?")


def money(text: str) -> int:
    """The last amount in the text, in cents: "original price $7.99, your price
    $0.00" is 0 (Instacart shows the price paid last)."""
    found = _MONEY.findall(text)
    if not found:
        raise CheckoutRefused(f"There's no amount in {text[:80]!r}.")
    sign, dollars, change = found[-1]
    cents = int(dollars.replace(",", "")) * 100 + int(change or 0)
    return -cents if sign else cents


def cents_text(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def subtree(tree: str, pattern: str, last: bool = False) -> str:
    """The part of an accessibility tree (one element per line, children indented
    under it) from the line matching the pattern (the first, or the last) through
    its children; empty when no line matches."""
    lines = tree.splitlines()
    starts = [n for n, line in enumerate(lines) if re.search(pattern, line)]
    if not starts:
        return ""
    start = starts[-1] if last else starts[0]
    depth = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and len(lines[end]) - len(lines[end].lstrip()) > depth:
        end += 1
    return "\n".join(lines[start:end])


def label_of(text: str) -> str:
    """A summary line's label: its first line, without an amount after it."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r"\s*-?\$.*$", "", first).strip()


def address_from(text: str) -> str:
    """The delivery address, from its control's text: up to the ZIP code (not the
    delivery instructions after it)."""
    # To the last ZIP code: a street number can have five digits too.
    match = re.match(r"\s*(.*\b\d{5}(?:-\d{4})?)\b", text, re.S)
    if match is None:
        raise CheckoutRefused("That element shows no address with a ZIP code.")
    return " ".join(match.group(1).split())


def card_from(text: str) -> str:
    """A card, from its control's text ("*4242 Visa Default"): "Visa ending 4242"."""
    match = re.search(r"\*(\d{4})\s+(.+?)(?:\s+Default)?$", " ".join(text.split()))
    if match is None:
        raise CheckoutRefused("That element shows no card (like *1234 Visa).")
    return f"{match.group(2)} ending {match.group(1)}"


def check_checkout(checkout: Checkout, job: CheckoutJob) -> list[str]:
    """What's wrong with a report, for the agent to fix (empty: nothing)."""
    problems = []
    if checkout.items_cents <= 0:
        problems.append("The items' total must be more than $0: point at the items' line.")
    added = checkout.items_cents + sum(f.cents for f in checkout.fees) + checkout.tip_cents
    if added != checkout.total_cents:
        problems.append(
            f"The lines don't add up: items, the other lines, and the tip make "
            f"{cents_text(added)}, but the total is {cents_text(checkout.total_cents)}. "
            "Include every summary line other than the items and the total."
        )
    expected = tip_cents(checkout.items_cents, job.tip_percent, job.tip_max_cents)
    if checkout.tip_cents != expected:
        problems.append(
            f"The tip is {cents_text(checkout.tip_cents)}; it must be {cents_text(expected)} "
            "(call `tip` with the items' total, then set that amount)."
        )
    if any(re.search(r"priority", fee.label, re.I) for fee in checkout.fees):
        problems.append("The delivery is Priority: choose a scheduled delivery instead.")
    return problems


@dataclass
class Browser:
    """The browser the tools drive: one page, signed in with the session."""

    job_dir: Path
    job: CheckoutJob
    context: BrowserContext
    page: Page

    async def snapshot(self, ref: str | None = None) -> str:
        """What's on screen as the agent sees it: the page's path, and the
        accessibility tree (each element with its ref) of the dialog that's open,
        if one is (as a person sees only it), else of the page; or of one element.
        Always cut from the whole page's tree: a tree of just one element numbers
        the refs anew, and the ones the agent has would stop working."""
        tree = await self._settled_tree()
        if ref:
            shown, tree = "Element:", subtree(tree, rf"\[ref={re.escape(ref)}\]")
            if not tree:
                raise CheckoutRefused(f"There's no element {ref} on the page now: look again.")
        elif dialog := subtree(tree, r"^\s*- (?:alert)?dialog\b", last=True):
            shown, tree = "A dialog is open (the page behind it isn't shown):", dialog
        else:
            shown = "The page:"
        if len(tree) > MAX_SNAPSHOT_CHARS:
            tree = (
                tree[:MAX_SNAPSHOT_CHARS]
                + "\n… (cut off: use `find` to locate an element by its text, or `look` at "
                "one part by its ref)"
            )
        return f"Page: {self.path()}\n{shown}\n{tree}"

    async def _settled_tree(self) -> str:
        """The page's tree once it has stopped changing (or as it is after the
        longest wait): a page still loading shows little."""
        previous = None
        for _ in range(SETTLE_MAX_MS // SETTLE_CHECK_MS):
            await self.page.wait_for_timeout(SETTLE_CHECK_MS)
            tree = await self.page.aria_snapshot(mode="ai")
            if tree == previous:
                return tree
            previous = tree
        return previous or await self.page.aria_snapshot(mode="ai")

    def path(self) -> str:
        return re.sub(r"[?#].*", "", self.page.url.removeprefix(self.job.site)) or "/"

    async def find(self, text: str) -> str:
        """The elements anywhere on the page whose line mentions the text."""
        tree = await self._settled_tree()
        found = [line.strip() for line in tree.splitlines() if text.lower() in line.lower()]
        if not found:
            return f"Page: {self.path()}\nNothing mentions {text!r}."
        more = f"\n… and {len(found) - MAX_FOUND} more" if len(found) > MAX_FOUND else ""
        return f"Page: {self.path()}\n" + "\n".join(found[:MAX_FOUND]) + more

    async def element_text(self, ref: str) -> str:
        """An element's text (and its accessible name, when that says more)."""
        element = await self._element(ref)
        text = await element.inner_text()
        name = await element.get_attribute("aria-label") or ""
        return text if len(text) >= len(name) else name

    async def _element(self, ref: str) -> Locator:
        from playwright.async_api import Error as PlaywrightError

        element = self.page.locator(f"aria-ref={ref}")
        try:
            found = await element.count()
        except PlaywrightError:  # a ref from a page since left
            found = 0
        if not found:
            raise CheckoutRefused(f"There's no element {ref} on the page now: look again.")
        return element

    async def _names(self, ref: str) -> str:
        """Everything an element is called: its text and each place its accessible
        name can come from (its label, what labels it, its title, value, or alt)."""
        element = await self._element(ref)
        names: str = await element.evaluate(
            """element => {
                const labelledBy = (element.getAttribute('aria-labelledby') || '')
                    .split(/\\s+/).filter(Boolean)
                    .map(id => document.getElementById(id)?.textContent || '')
                const labels = [...(element.labels || [])].map(label => label.textContent)
                return [element.innerText, element.getAttribute('aria-label'), ...labelledBy,
                    ...labels, element.title, element.value, element.alt]
                    .filter(Boolean).join(' ')
            }"""
        )
        return names

    def on_site(self) -> bool:
        return urlparse(self.page.url).hostname == urlparse(self.job.site).hostname

    async def _stay_on_site(self) -> None:
        if not self.on_site():
            await self.page.go_back()
            if not self.on_site():
                raise CheckoutRefused("The browser is off Instacart's site: open one of its pages.")
            raise CheckoutRefused("That led off Instacart's site; stay on its pages.")

    async def open(self, path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise CheckoutRefused(
                "Open a path on Instacart's site, like /store/wegmans/storefront."
            )
        await self.page.goto(f"{self.job.site}{path}", wait_until="domcontentloaded")
        await self._stay_on_site()
        return await self.snapshot()

    async def click(self, ref: str) -> str:
        await self._stay_on_site()
        if PLACE_ORDER.search(await self._names(ref)):
            raise CheckoutRefused("Placing the order isn't yours to do: never press Place order.")
        await (await self._element(ref)).click()
        await self._settled_tree()
        await self._stay_on_site()
        return await self.snapshot()

    async def type(self, ref: str, text: str) -> str:
        await self._stay_on_site()
        await (await self._element(ref)).fill(text)
        return await self.snapshot()

    async def save_session(self) -> None:
        """Hand the session back to the worker as it is now (Instacart renews it)."""
        state = await self.context.storage_state()
        cookies = [c for c in state["cookies"] if INSTACART_COOKIE.search(c["domain"].lstrip("."))]
        replace_file(
            self.job.session_dir / RENEWED_SESSION_FILE,
            json.dumps({"cookies": cookies, "origins": []}),
        )


@asynccontextmanager
async def browser(job_dir: Path) -> AsyncIterator[Browser]:
    """A headless browser signed in with the session the worker left in the job
    directory, for the length of the block. The renewed session is handed back
    when checkout is reported, and only then: never as the tools stop, which may
    be after the worker has cleared the job directory."""
    from playwright.async_api import async_playwright

    job = read_context(job_dir, CheckoutJob)
    state: dict[str, Any] = json.loads((job.session_dir / SESSION_FILE).read_text())
    async with async_playwright() as playwright:
        chromium: Browser = await playwright.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage"]
        )
        try:
            context = await chromium.new_context(
                storage_state=state,  # type: ignore[arg-type]
                viewport={"width": 1280, "height": 1000},
            )
            context.set_default_timeout(TIMEOUT_MS)
            context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

            page = await context.new_page()
            # Nothing opens another tab or window. (Leaving the site is caught after
            # each action: intercepting the page's requests to stop it first keeps
            # Instacart's checkout from loading.)
            context.on("page", lambda other: asyncio.ensure_future(other.close()))
            yield Browser(job_dir, job, context, page)
        finally:
            await chromium.close()


# What the tools record.


async def record_replacements(job_dir: Path, opened: Browser, refs: list[str]) -> list[str]:
    """The out-of-stock setting each cart item shows, read from those elements:
    one for every product in the cart."""
    count = opened.job.cart_items
    if len(set(refs)) != count:
        raise CheckoutRefused(
            f"The cart has {count} products: give the ref of each one's out-of-stock "
            f"(replacement) setting, {count} in all (you gave {len(set(refs))})."
        )
    shown = [" ".join((await opened.element_text(ref)).split()) for ref in set(refs)]
    if not all(re.search(r"replace|refund", setting, re.I) for setting in shown):
        raise CheckoutRefused("Those aren't all out-of-stock settings (like Replace with ...).")
    settings = sorted(set(shown))
    with locked(job_dir):
        state = load_state(job_dir)
        state.replacements = settings
        _save(job_dir, state)
    return settings


@dataclass(frozen=True)
class ReportRefs:
    """Where the agent says each part of checkout is on the page."""

    items: str
    lines: list[str]
    tip: str
    total: str
    delivery: str
    address: str
    card: str

    def all(self) -> list[str]:
        return [
            self.items,
            *self.lines,
            self.tip,
            self.total,
            self.delivery,
            self.address,
            self.card,
        ]


async def report(job_dir: Path, opened: Browser, refs: ReportRefs) -> Checkout:
    """Read checkout from the elements the agent points at, check it, and keep it."""
    state = load_state(job_dir)
    if state.replacements is None:
        raise CheckoutRefused(
            "Record the cart's out-of-stock settings first (`record_replacements`)."
        )
    await opened._stay_on_site()
    if len(set(refs.all())) != len(refs.all()):
        raise CheckoutRefused("Each part is a different element: point at each one once.")
    items, total = await opened.element_text(refs.items), await opened.element_text(refs.total)
    problems = []
    if not re.search(r"\bitems?\b", label_of(items), re.I):
        problems.append('The items\' total is the line that says how many items ("30 items").')
    if not re.match(r"total\b", label_of(total), re.I):
        problems.append("The total is the line labeled Total.")
    delivery = " ".join((await opened.element_text(refs.delivery)).split())
    if re.search(r"priority|fast", delivery, re.I) or not re.search(
        r"\d(:\d\d)?\s*(am|pm)|noon", delivery, re.I
    ):
        problems.append(
            f"The delivery chosen shows {delivery[:60]!r}: choose a scheduled day and time "
            "window (not Priority or Fast), and point at the delivery option that shows it."
        )
    if problems:
        raise CheckoutRefused(" ".join(problems))
    fees = []
    for ref in refs.lines:
        text = await opened.element_text(ref)
        fees.append(QuoteLine(label=label_of(text), cents=money(text)))
    checkout = Checkout(
        items_cents=money(items),
        fees=fees,
        tip_cents=money(await opened.element_text(refs.tip)),
        total_cents=money(total),
        address=address_from(await opened.element_text(refs.address)),
        card=card_from(await opened.element_text(refs.card)),
        replacements=state.replacements,
    )
    if problems := check_checkout(checkout, opened.job):
        raise CheckoutRefused(" ".join(problems))
    await opened.save_session()
    with locked(job_dir):
        state = load_state(job_dir)
        state.checkout = checkout
        _save(job_dir, state)
    return checkout


async def mark_signed_out(job_dir: Path, opened: Browser, ref: str) -> None:
    """Record that Instacart asks to sign in, pointed at by its sign-in control
    (not after checkout was reported: it was signed in then)."""
    if load_state(job_dir).checkout is not None:
        raise CheckoutRefused("Checkout is reported, so it was signed in: you're done.")
    if not re.search(r"\b(log|sign)\s*in\b", await opened.element_text(ref), re.I):
        raise CheckoutRefused("That isn't a sign-in control: point at the one Instacart shows.")
    with locked(job_dir):
        state = load_state(job_dir)
        state.signed_out = True
        _save(job_dir, state)
