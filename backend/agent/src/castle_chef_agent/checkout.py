"""Checkout on Instacart's website, driven by the agent (a `quote` job, and a
`book` job): a headless browser signed in with the household's session, which
the agent works through tools that see the page as its accessibility tree
(elements by role and name, each with a ref) and act on those refs. No page's
layout is built into the code; what is, is the rules around it:

- the agent never presses Place order itself (the click tool refuses it); a
  booking presses it once, through `place_order`, after checkout is reported and
  checked, and only once the worker has recorded the click (it answers through
  the job directory, and may say not to: a dry run, or booking turned off);
- it only goes to Instacart's own pages;
- what it reports is read from the elements it points at, not taken from it,
  and must add up: the items, each other line, and the tip make the total, the
  tip follows the household's rule, and the delivery isn't Priority; a booking's
  is the window the rule chose (castle_chef_agent.windows) from the ones offered,
  to the address and card the final review showed.

The session's cookies are secrets: the worker puts them in the job directory for
the tools, takes the renewed ones back, and removes both; they never reach the
agent's context or the logs.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from castle_chef_agent import windows
from castle_chef_agent.address import normalized_label
from castle_chef_agent.jobfiles import locked, read_context, replace_file
from castle_chef_agent.windows import Window

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Locator, Page

SITE = "https://www.instacart.com"
# The household's session, as the worker hands it over, and as the tools hand it back,
# in their own directory (outside the job directory, which is kept after the job).
SESSION_FILE = "session.json"
RENEWED_SESSION_FILE = "session-renewed.json"
STATE_FILE = "checkout.json"
# A booking's Place order: the tools ask (the request), the worker answers.
CLICK_REQUEST_FILE = "click-request.json"
CLICK_ANSWER_FILE = "click-answer.json"
# How long `place_order` waits for the worker's answer, checking this often.
CLICK_ANSWER_TIMEOUT_S = 60
CLICK_ANSWER_CHECK_S = 0.25
# How long a control may take to act, and a page to load.
TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT_MS = 60_000
# After an action, the page is shown once its tree stops changing (it loads and
# updates in place): checked this often (by default; the worker's setting), for
# at most this long.
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


class BookTarget(BaseModel):
    """What a booking books: the delivery day and the latest time it should
    arrive by, to the address and card the final review showed."""

    day: date
    by: time
    address: str
    card: str


class CheckoutJob(BaseModel):
    """The worker's context for the tools: the store, the tip rule, how many
    products the cart holds, where the session is, and, for a booking, what it
    books (None: a quote, which never presses Place order)."""

    store: str
    tip_percent: int
    tip_max_cents: int
    cart_items: int
    session_dir: Path
    book: BookTarget | None = None
    delivery_address: str | None = None
    time_zone: str = "America/New_York"
    # Instacart's website: the worker passes its setting (tests point it at stand-ins).
    site: str = SITE
    # How often a page is checked for having stopped changing: the worker passes
    # its setting (tests' stand-in pages settle at once). At least two checks fit
    # in the longest wait.
    settle_check_ms: int = Field(default=SETTLE_CHECK_MS, gt=0, le=SETTLE_MAX_MS // 2)


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


class ClickAnswer(StrEnum):
    """The worker's answer when a booking asks to press Place order."""

    GO = "go"  # the click is recorded: press it
    DRY_RUN = "dry_run"  # development: don't
    OFF = "off"  # booking isn't turned on yet: don't
    REFUSED = "refused"  # this job mustn't (the order was clicked before, or changed)


class ClickRequest(BaseModel):
    """A booking asking to press Place order, for this checkout."""

    total_cents: int
    day: date
    by: time
    window: Window


class ClickReply(BaseModel):
    answer: ClickAnswer


class BookingRefusal(StrEnum):
    DAY_NOT_OFFERED = "day_not_offered"
    WINDOW_EXPIRED = "window_expired"
    PERMISSION_NOT_RECEIVED = "permission_not_received"


class Confirmation(BaseModel):
    """What Instacart showed after Place order: its order number, the window
    booked, and the total."""

    order_number: str
    window: Window
    total_cents: int


class CheckoutState(BaseModel):
    """What the tools recorded, in the job directory (STATE_FILE)."""

    model_config = ConfigDict(extra="forbid")

    replacements: list[str] | None = None
    checkout: Checkout | None = None
    signed_out: bool = False
    availability: windows.Availability | None = None
    # All dates in the schedule's tablist: a quote may not omit a displayed day.
    offered_days: list[date] = []
    proposal: windows.DeliveryProposal | None = None
    alternatives_checked: bool = False
    # A booking's: the window the rule chose, or that none that day works.
    window: Window | None = None
    no_window: bool = False
    refusal: BookingRefusal | None = None
    # The address or card isn't the one the order was approved with.
    changed: str | None = None
    # Its Place order: the worker's answer, whether it was pressed, and what
    # Instacart confirmed (or that the agent found no confirmation).
    click: ClickAnswer | None = None
    clicked: bool = False
    confirmation: Confirmation | None = None
    not_confirmed: bool = False


def load_state(job_dir: Path) -> CheckoutState:
    path = job_dir / STATE_FILE
    return CheckoutState.model_validate_json(path.read_text()) if path.exists() else CheckoutState()


@contextmanager
def _changing(job_dir: Path) -> Iterator[CheckoutState]:
    """What the tools recorded, to change under the job directory's lock: saved
    when the block ends, unless it raises."""
    with locked(job_dir):
        state = load_state(job_dir)
        yield state
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


def account_changes(checkout: Checkout, book: BookTarget) -> str | None:
    """What changed in the account since the order was approved (where it's
    delivered, or the card), as whoever approved it is told; None: nothing."""
    changes = []
    if checkout.address != book.address:
        changes.append(f"Instacart now delivers to {checkout.address}, not {book.address}.")
    if checkout.card != book.card:
        changes.append(f"Instacart now charges {checkout.card}, not {book.card}.")
    return " ".join(changes) or None


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
        check = self.job.settle_check_ms
        for _ in range(SETTLE_MAX_MS // check):
            await self.page.wait_for_timeout(check)
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
        """Everything an element is called, and the control it's part of (a click
        on an icon in a button presses the button): their text and each place an
        accessible name can come from (a label, what labels it, its title, value,
        or alt)."""
        element = await self._element(ref)
        names: str = await element.evaluate(
            """element => {
                const named = each => {
                    const labelledBy = (each.getAttribute('aria-labelledby') || '')
                        .split(/\\s+/).filter(Boolean)
                        .map(id => document.getElementById(id)?.textContent || '')
                    const labels = [...(each.labels || [])].map(label => label.textContent)
                    return [each.innerText, each.getAttribute('aria-label'), ...labelledBy,
                        ...labels, each.title, each.value, each.alt]
                }
                const control = element.closest(
                    'button, a, [role=button], [role=link], input[type=submit], input[type=button]')
                const its = control && control !== element ? named(control) : []
                return [...named(element), ...its].filter(Boolean).join(' ')
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
        chromium = await playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            context = await chromium.new_context(
                storage_state=state,  # type: ignore[arg-type]
                viewport={"width": 1280, "height": 1000},
                timezone_id=job.time_zone,
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
    with _changing(job_dir) as state:
        state.replacements = settings
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
    book = opened.job.book
    if book is None and state.replacements is None:
        raise CheckoutRefused(
            "Record the cart's out-of-stock settings first (`record_replacements`)."
        )
    if book is not None and state.window is None:
        raise CheckoutRefused("Choose the delivery window first (`choose_window`).")
    if state.clicked or state.click is not None:
        raise CheckoutRefused("Place order was asked for already: don't report again.")
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
    if (
        book is not None
        and state.window is not None
        and (
            windows.parse(delivery) != state.window
            or not _delivery_matches(delivery, book.day, opened.job.time_zone)
        )
    ):
        problems.append(
            f"The delivery chosen shows {delivery[:60]!r}, not the window to book "
            f"({book.day:%a, %b} {book.day.day}, {windows.label(state.window)}): choose that "
            "one, and point at the option showing it."
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
        # A booking doesn't read them (the review showed them).
        replacements=state.replacements or [],
    )
    if problems := check_checkout(checkout, opened.job):
        raise CheckoutRefused(" ".join(problems))
    if book is None and (
        state.availability is None
        or set(state.offered_days) != {day.day for day in state.availability.days}
    ):
        raise CheckoutRefused("Record every delivery-day tab with record_day before reporting.")
    expected_address = opened.job.delivery_address
    if expected_address is not None and (
        normalized_label(checkout.address) != normalized_label(expected_address)
    ):
        raise CheckoutRefused(
            f"Delivery address: checkout shows {checkout.address}, but this order is for "
            f"{expected_address}. Select that saved address before continuing."
        )
    if book is not None and (changed := account_changes(checkout, book)):
        with _changing(job_dir) as state:
            state.changed = changed
        raise CheckoutRefused(f"{changed} That's recorded, and nothing is ordered: stop here.")
    await opened.save_session()
    with _changing(job_dir) as state:
        state.checkout = checkout
    return checkout


async def mark_signed_out(job_dir: Path, opened: Browser, ref: str) -> None:
    """Record that Instacart asks to sign in, pointed at by its sign-in control
    (not after checkout was reported: it was signed in then)."""
    if load_state(job_dir).checkout is not None:
        raise CheckoutRefused("Checkout is reported, so it was signed in: you're done.")
    if not re.search(r"\b(log|sign)\s*in\b", await opened.element_text(ref), re.I):
        raise CheckoutRefused("That isn't a sign-in control: point at the one Instacart shows.")
    with _changing(job_dir) as state:
        state.signed_out = True


# A booking's tools.


def _book(opened: Browser) -> BookTarget:
    if opened.job.book is None:
        raise CheckoutRefused("This isn't a booking.")
    return opened.job.book


def _names_a_day(text: str) -> bool:
    """Whether the text names a calendar day ("Oct 6")."""
    months = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    return re.search(rf"\b(?:{months})\w*\.?\s+\d{{1,2}}\b", text, re.I) is not None


def _day_named(text: str, day: date) -> bool:
    """Whether the text names the day ("Tue, Oct 6", "October 6")."""
    return re.search(rf"\b{day:%b}\w*\.?\s+{day.day}\b", text, re.I) is not None


def _delivery_matches(text: str, day: date, zone: str) -> bool:
    if _names_a_day(text):
        return _day_named(text, day)
    if re.search(r"\b(today|tomorrow)\b", text, re.I):
        return delivery_day(text, datetime.now(ZoneInfo(zone)).date()) == day
    return False


def delivery_day(text: str, today: date) -> date:
    """Resolve a schedule tab, including relative labels, at the store."""
    if re.search(r"\btoday\b", text, re.I):
        day = today
    elif re.search(r"\btomorrow\b", text, re.I):
        day = today + timedelta(days=1)
    else:
        for offset in range(367):
            candidate = today + timedelta(days=offset)
            if _day_named(text, candidate):
                return candidate
        raise CheckoutRefused(f"Cannot read the delivery date in {text[:80]!r}.")
    if _names_a_day(text) and not _day_named(text, day):
        raise CheckoutRefused(f"The date and relative day disagree in {text[:80]!r}.")
    return day


async def record_day(
    job_dir: Path, opened: Browser, day: str, offered: list[str]
) -> dict[str, Window]:
    """Read a selected day's available windows from DOM refs, shared by quotes and bookings."""
    state = load_state(job_dir)
    if state.checkout is not None or state.click is not None or state.clicked:
        raise CheckoutRefused(
            "Checkout was reported or purchase was requested: don't change times."
        )
    await opened._stay_on_site()
    tab = await opened._element(day)
    if await tab.get_attribute("role") != "tab":
        raise CheckoutRefused("Point at a delivery-day tab.")
    now = datetime.now(ZoneInfo(opened.job.time_zone))
    date_shown = delivery_day(await opened.element_text(day), now.date())
    if await tab.is_disabled():
        if offered:
            raise CheckoutRefused("That day is disabled and has no available windows.")
    elif await tab.get_attribute("aria-selected") != "true":
        raise CheckoutRefused("Click that day's tab first, then give its windows.")
    names: list[str] = await tab.evaluate(
        """element => {
            const list = element.closest('[role=tablist]')
            if (!list) throw new Error('The day has no delivery tablist')
            return [...list.querySelectorAll('[role=tab]')].map(tab => tab.innerText)
        }"""
    )
    dates = sorted({delivery_day(name, now.date()) for name in names})
    if len(set(offered)) != len(offered):
        raise CheckoutRefused("Give each window once.")
    found: dict[str, Window] = {}
    for ref in offered:
        element = await opened._element(ref)
        if await element.is_disabled() or not await element.is_visible():
            raise CheckoutRefused(
                "Give only available, visible windows; omit full or disabled ones."
            )
        text = " ".join((await opened.element_text(ref)).split())
        window = windows.parse(text)
        if window is None:
            raise CheckoutRefused(f"{text[:40]!r} isn't a delivery window.")
        if datetime.combine(date_shown, window.start, now.tzinfo) > now:
            found[ref] = window
    with _changing(job_dir) as state:
        previous = state.availability
        captured = (
            {entry.day: entry for entry in previous.days if entry.day in dates} if previous else {}
        )
        captured[date_shown] = windows.AvailableDay(day=date_shown, windows=list(found.values()))
        state.proposal, state.alternatives_checked = None, False
        state.offered_days = dates
        state.availability = windows.Availability(
            observed_at=previous.observed_at if previous else now.astimezone(UTC),
            time_zone=opened.job.time_zone,
            days=[captured[key] for key in sorted(captured)],
        )
    return found


async def choose_window(job_dir: Path, opened: Browser, day: str, offered: list[str]) -> str:
    """Choose the delivery window to book among those Instacart offers on the
    order's day (the tab `day`, selected), by the rule (windows.choose); or
    record that none of them works, so the member chooses another day or time."""
    book = _book(opened)
    if load_state(job_dir).checkout is not None:
        raise CheckoutRefused("Checkout is reported: the window is chosen.")
    shown = " ".join((await opened.element_text(day)).split())
    today = datetime.now(ZoneInfo(opened.job.time_zone)).date()
    if delivery_day(shown, today) != book.day:
        raise CheckoutRefused(
            f"That's {shown[:40]!r}: point at the tab for {book.day:%a, %b} {book.day.day}."
        )
    found = await record_day(job_dir, opened, day, offered)
    chosen = windows.choose(list(found.values()), book.by)
    with _changing(job_dir) as state:
        state.window, state.no_window = chosen, chosen is None
    if chosen is None:
        return (
            "None of that day's windows works for the time the order needs: that's recorded, "
            "and someone must approve an alternative. Record every day with record_day, "
            "then call propose_delivery and stop without placing anything."
        )
    ref = next(ref for ref, window in found.items() if window == chosen)
    return f"Book {windows.label(chosen)}: click {ref}, then go on."


async def propose_delivery(job_dir: Path, opened: Browser) -> str:
    """Propose the next store-observed delivery, strictly before purchase authorization."""
    book = _book(opened)
    with _changing(job_dir) as state:
        if state.clicked or state.click is not None or (job_dir / CLICK_REQUEST_FILE).exists():
            raise CheckoutRefused("Purchase may have been attempted. Never propose or place again.")
        snapshot = state.availability
        if snapshot is None or set(state.offered_days) != {day.day for day in snapshot.days}:
            raise CheckoutRefused(
                "Record every displayed day first, including days with no windows."
            )
        state.alternatives_checked = True
        if not snapshot.days or book.day > max(day.day for day in snapshot.days):
            state.refusal = BookingRefusal.DAY_NOT_OFFERED
            return (
                "The approved day isn't offered yet. That's recorded. "
                "Stop; don't propose another day."
            )
        now = datetime.now(ZoneInfo(opened.job.time_zone))
        remaining = snapshot.remaining(now)
        approved = [w for day in remaining if day.day == book.day for w in day.windows]
        if windows.choose(approved, book.by) is not None:
            raise CheckoutRefused("An approved window is still available: use choose_window.")
        state.window, state.no_window = None, True
        state.proposal = windows.propose(snapshot, book.day, book.by, now)
    return "Alternative recorded for explicit approval. Nothing was ordered. Stop here."


async def _await_answer(job_dir: Path) -> ClickAnswer | None:
    """The worker's answer to the click request, or None if it didn't answer in time."""
    path = job_dir / CLICK_ANSWER_FILE
    for _ in range(int(CLICK_ANSWER_TIMEOUT_S / CLICK_ANSWER_CHECK_S)):
        if path.exists():
            return ClickReply.model_validate_json(path.read_text()).answer
        await asyncio.sleep(CLICK_ANSWER_CHECK_S)
    return None


async def place_order(job_dir: Path, opened: Browser, ref: str) -> str:
    """Press Place order, once: after checkout is reported, and only when the
    worker says to (it records the click first). Returns what to do next."""
    _book(opened)
    if load_state(job_dir).checkout is None:
        raise CheckoutRefused("Report checkout first (`report`); it's checked before ordering.")
    await opened._stay_on_site()
    if not PLACE_ORDER.search(await opened._names(ref)):
        raise CheckoutRefused("That isn't the Place order button.")
    # Asking is claimed under the lock: of calls made at once, only the one that
    # asks waits for the answer and may press it.
    with locked(job_dir):
        state = load_state(job_dir)
        request = job_dir / CLICK_REQUEST_FILE
        if state.click is not None or request.exists() or state.checkout is None:
            raise CheckoutRefused("Place order was asked for already: it's never asked for twice.")
        if state.window is None:
            raise CheckoutRefused("The approved delivery window is missing.")
        book = _book(opened)
        asked = ClickRequest(
            total_cents=state.checkout.total_cents, day=book.day, by=book.by, window=state.window
        )
        replace_file(request, asked.model_dump_json())
    answer = await _await_answer(job_dir)
    if answer is None:
        with _changing(job_dir) as state:
            state.refusal = BookingRefusal.PERMISSION_NOT_RECEIVED
        raise CheckoutRefused(
            "Booking did not answer. Do not retry; the worker must verify purchase authorization."
        )
    with _changing(job_dir) as state:
        state.click = answer
    if answer == ClickAnswer.DRY_RUN:
        return "This is a dry run: Place order wasn't pressed, and nothing was ordered. Stop here."
    if answer == ClickAnswer.OFF:
        return "Booking isn't turned on yet: Place order wasn't pressed. Stop here."
    if answer == ClickAnswer.REFUSED:
        return "This order mustn't be placed now: Place order wasn't pressed. Stop here."
    state = load_state(job_dir)
    now = datetime.now(ZoneInfo(opened.job.time_zone))
    if state.window is None or datetime.combine(book.day, state.window.start, now.tzinfo) <= now:
        with _changing(job_dir) as state:
            state.refusal = BookingRefusal.WINDOW_EXPIRED
        raise CheckoutRefused("The delivery window passed while waiting. Do not press Place order.")
    try:
        await (await opened._element(ref)).click()
    finally:
        # Pressed or not, it's never pressed again: what happened is found out next.
        with _changing(job_dir) as state:
            state.clicked = True
    await opened._settled_tree()
    return (
        "Place order was pressed. Now find Instacart's confirmation, and call `confirmed` "
        "with the refs of its order number, delivery window, and total; if there's none, "
        "look in your orders for this one, and if it isn't there either, call `not_confirmed`.\n"
        + await opened.snapshot()
    )


async def record_confirmation(
    job_dir: Path, opened: Browser, number: str, window: str, total: str
) -> Confirmation:
    """Read what Instacart confirmed from the elements the agent points at: this
    order's, showing its day (including Today/Tomorrow), window and reported total.
    A time-only entry cannot identify the delivery date of this purchase."""
    state = load_state(job_dir)
    book = _book(opened)
    if not state.clicked or state.checkout is None:
        raise CheckoutRefused("Nothing was ordered: there's no confirmation to record.")
    await opened._stay_on_site()
    if len({number, window, total}) != 3:
        raise CheckoutRefused("Each part is a different element: point at each one once.")
    shown = " ".join((await opened.element_text(number)).split())
    found = re.findall(r"\b[A-Z0-9-]*\d{5,}[A-Z0-9-]*\b", shown, re.I)
    if not found:
        raise CheckoutRefused(f"{shown[:60]!r} shows no order number: point at the one shown.")
    when = " ".join((await opened.element_text(window)).split())
    booked = windows.parse(when)
    if booked is None:
        raise CheckoutRefused("That element shows no delivery window: point at the one booked.")
    charged = money(await opened.element_text(total))
    if (
        booked != state.window
        or not _delivery_matches(when, book.day, opened.job.time_zone)
        or charged != state.checkout.total_cents
    ):
        raise CheckoutRefused(
            "That isn't this order: its window, day, or total isn't the one just placed "
            f"({book.day:%a, %b} {book.day.day}, {windows.label(state.window or booked)}, "
            f"{cents_text(state.checkout.total_cents)}). If this order's confirmation isn't "
            "shown anywhere, call `not_confirmed`."
        )
    confirmation = Confirmation(order_number=found[-1], window=booked, total_cents=charged)
    with _changing(job_dir) as state:
        state.confirmation = confirmation
    return confirmation


async def mark_not_confirmed(job_dir: Path) -> None:
    """Record that no confirmation could be found after Place order was pressed."""
    with _changing(job_dir) as state:
        if not state.clicked:
            raise CheckoutRefused("Nothing was ordered: there's nothing to confirm.")
        if state.confirmation is not None:
            raise CheckoutRefused("It's confirmed already: you're done.")
        state.not_confirmed = True
