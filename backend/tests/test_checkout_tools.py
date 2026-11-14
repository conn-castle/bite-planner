"""The agent's checkout tools (bite_planner_agent.checkout), in a headless browser on
stand-ins for Instacart's pages (fixtures/checkout): what the agent sees, what it
can and can't do, and how its report is read and checked."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from bite_planner_agent import checkout
from bite_planner_agent.checkout import (
    RENEWED_SESSION_FILE,
    SESSION_FILE,
    Browser,
    CheckoutJob,
    CheckoutRefused,
    QuoteLine,
    ReportRefs,
    load_state,
    tip_cents,
)
from bite_planner_agent.jobfiles import read_context, write_context

from tests.conftest import SETTLE_CHECK_MS

SESSION = {
    "cookies": [
        {
            "name": "session",
            "value": "household",
            "domain": ".instacart.com",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ],
    "origins": [],
}


def ref(page: str, pattern: str) -> str:
    """The ref of the element whose line in the page matches `pattern`."""
    match = re.search(rf"{pattern}[^\n]*\[ref=(\w+)\]|\[ref=(\w+)\]: {pattern}", page)
    assert match, f"No {pattern!r} in:\n{page}"
    return match.group(1) or match.group(2)


@pytest.fixture
def job_dir(tmp_path: Path, instacart_site: str) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    sessions = tmp_path / "session"
    sessions.mkdir()
    write_context(
        directory,
        CheckoutJob(
            store="Wegmans",
            tip_percent=10,
            tip_max_cents=1500,
            cart_items=2,  # the stand-in cart's onions and sweet onions
            session_dir=sessions,
            site=instacart_site,
            settle_check_ms=SETTLE_CHECK_MS,
        ),
    )
    (sessions / SESSION_FILE).write_text(json.dumps(SESSION))
    return directory


def renewed_file(job_dir: Path) -> Path:
    return job_dir.parent / "session" / RENEWED_SESSION_FILE


def in_browser(job_dir: Path, steps: Callable[[Browser], Awaitable[Any]]) -> Any:
    async def run() -> Any:
        async with checkout.browser(job_dir) as opened:
            return await steps(opened)

    return asyncio.run(run())


async def to_checkout(opened: Browser) -> str:
    """As the agent goes: the store, the cart (recording its out-of-stock
    settings), and on to checkout."""
    page = await opened.open("/store/wegmans/storefront")
    page = await opened.click(ref(page, 'button "View cart"'))
    refs = re.findall(r'button "Replace with best match" \[ref=(e\d+)\]', page)
    await checkout.record_replacements(opened.job_dir, opened, refs)
    return await opened.click(ref(page, 'button "Go to checkout"'))


async def schedule_and_tip(opened: Browser, page: str) -> str:
    page = await opened.click(ref(page, 'button "Schedule'))
    names = re.findall(r'tab "([^"\n]+)"', page)
    for name in names:
        page = await opened.click(ref(page, 'tab "' + re.escape(name) + '"'))
        offered = re.findall(r'button "[^"\n]*(?:am|pm|Noon)[^"\n]*" \[ref=(\w+)\]', page)
        await checkout.record_day(
            opened.job_dir, opened, ref(page, 'tab "' + re.escape(name) + '"'), offered
        )
    page = await opened.click(ref(page, 'button "4pm'))
    page = await opened.click(ref(page, 'button "Other"'))
    page = await opened.type(ref(page, 'textbox "Other amount"'), "1.96")
    return await opened.click(ref(page, 'button "Save Tip"'))


def refs_of(page: str, lines: list[str]) -> ReportRefs:
    return ReportRefs(
        items=ref(page, "2 items"),
        lines=[ref(page, line) for line in lines],
        tip=ref(page, r"\$1\.96"),
        total=ref(page, "Total"),
        delivery=ref(page, r'button "\w{3}, \w{3} \d+, 4pm'),
        address=ref(page, 'button "1 Main Street'),
        card=ref(page, r'button "\*4242'),
    )


def test_the_tip_is_a_percentage_of_the_items_but_never_more_than_the_maximum() -> None:
    assert tip_cents(1959, 10, 1500) == 196
    assert tip_cents(1955, 10, 1500) == 196  # half a cent rounds up
    assert tip_cents(25_000, 10, 1500) == 1500
    assert tip_cents(1959, 0, 1500) == 0


def test_the_agent_sees_the_page_as_elements_with_refs(job_dir: Path) -> None:
    page = in_browser(job_dir, lambda opened: opened.open("/store/wegmans/storefront"))

    assert page.startswith("Page: /store/wegmans/storefront\n")
    assert re.search(r'button "View cart" \[ref=\w+\]', page)


def test_a_page_is_shown_once_it_has_stopped_changing(job_dir: Path) -> None:
    # Checked every 250 ms, not the suite's 50: the page changes every 20 ms, and
    # a busy machine mustn't stall it long enough to look settled.
    context = read_context(job_dir, CheckoutJob)
    write_context(job_dir, context.model_copy(update={"settle_check_ms": 250}))

    page = in_browser(job_dir, lambda opened: opened.open("/store/settling"))

    assert 'heading "Your cart"' in page
    assert 'button "Go to checkout"' in page
    assert "Loading" not in page


def test_a_reported_checkout_is_read_from_the_elements_pointed_at(job_dir: Path) -> None:
    async def steps(opened: Browser) -> checkout.Checkout:
        page = await schedule_and_tip(opened, await to_checkout(opened))
        return await checkout.report(
            job_dir, opened, refs_of(page, ["Delivery with Instacart", "Service fee"])
        )

    read = in_browser(job_dir, steps)

    assert (read.items_cents, read.tip_cents, read.total_cents) == (1959, 196, 2404)
    assert read.fees == [
        QuoteLine(label="Delivery with Instacart+ original price", cents=0),
        QuoteLine(label="Service fee", cents=249),
    ]
    assert (read.address, read.card) == ("1 Main Street Rochester, NY 14604", "Visa ending 4242")
    assert read.replacements == ["Replace with best match"]
    assert load_state(job_dir).checkout == read
    # The session, as Instacart renewed it, is handed back with the report.
    renewed = json.loads(renewed_file(job_dir).read_text())
    assert [cookie["name"] for cookie in renewed["cookies"]] == ["session"]


@pytest.mark.parametrize("matches", [True, False])
def test_checkout_must_use_the_selected_delivery_address(job_dir: Path, matches: bool) -> None:
    context = read_context(job_dir, CheckoutJob)
    address = (
        "1 Main Street, Rochester, NY 14604" if matches else "2 Beach Road, Emerald Isle, NC 28594"
    )
    write_context(job_dir, context.model_copy(update={"delivery_address": address}))

    async def steps(opened: Browser) -> checkout.Checkout:
        page = await schedule_and_tip(opened, await to_checkout(opened))
        return await checkout.report(
            job_dir, opened, refs_of(page, ["Delivery with Instacart", "Service fee"])
        )

    if matches:
        assert in_browser(job_dir, steps).address == "1 Main Street Rochester, NY 14604"
    else:
        with pytest.raises(CheckoutRefused, match="Delivery address: checkout shows"):
            in_browser(job_dir, steps)
        assert load_state(job_dir).checkout is None


def test_a_report_that_doesnt_add_up_or_breaks_a_rule_is_refused_with_why(job_dir: Path) -> None:
    async def steps(opened: Browser) -> list[str]:
        page = await to_checkout(opened)
        refused = []
        # Still Priority, with its fee, and the tip Instacart starts with.
        lines = ["Delivery with Instacart", "Service fee", "Priority fee"]
        try:
            await checkout.report(
                job_dir,
                opened,
                ReportRefs(
                    items=ref(page, "2 items"),
                    lines=[ref(page, line) for line in lines],
                    tip=ref(page, r"\$2\.00"),
                    total=ref(page, "Total"),
                    delivery=ref(page, 'button "Schedule'),
                    address=ref(page, 'button "1 Main Street'),
                    card=ref(page, r'button "\*4242'),
                ),
            )
        except CheckoutRefused as error:
            refused.append(str(error))
        # A line left out (the service fee).
        page = await schedule_and_tip(opened, page)
        try:
            await checkout.report(job_dir, opened, refs_of(page, ["Delivery with Instacart"]))
        except CheckoutRefused as error:
            refused.append(str(error))
        return refused

    tip_and_priority, missing_line = in_browser(job_dir, steps)

    assert "it must be $1.96" in tip_and_priority
    assert "Priority" in tip_and_priority
    assert "don't add up" in missing_line
    assert load_state(job_dir).checkout is None


def test_place_order_is_never_pressed_and_pages_stay_on_instacart(job_dir: Path) -> None:
    async def steps(opened: Browser) -> list[str]:
        page = await to_checkout(opened)
        # The button, one named Place order by its label ("Pay $25.08 now"), a
        # submit input whose value is Place order, and a Place order button with
        # an icon of its own in it: all named Place order, or part of it.
        orders = re.findall(r'button "Place order" \[ref=(\w+)\]', page)
        assert len(orders) == 4
        refused = []
        actions: list[Callable[[], Awaitable[str]]] = [
            lambda: opened.click(orders[0]),
            lambda: opened.click(orders[1]),
            lambda: opened.click(orders[2]),
            lambda: opened.click(ref(page, 'img "Lock"')),
            lambda: opened.open("https://example.com/"),
            lambda: opened.open("//example.com/"),
        ]
        for action in actions:
            try:
                await action()
            except CheckoutRefused as error:
                refused.append(str(error))
        return refused

    place, labeled, submit, icon, elsewhere, other_host = in_browser(job_dir, steps)

    assert all("never press Place order" in refusal for refusal in (place, labeled, submit, icon))
    assert "path on Instacart's site" in elsewhere
    assert "path on Instacart's site" in other_host


def test_links_to_another_site_or_a_new_tab_go_nowhere(job_dir: Path) -> None:
    async def steps(opened: Browser) -> tuple[str, str, str, int]:
        page = await to_checkout(opened)
        with pytest.raises(CheckoutRefused, match="led off Instacart's site") as refused:
            await opened.click(ref(page, 'button "Help center"'))
        after = await opened.snapshot()
        with pytest.raises(CheckoutRefused, match="look again"):
            await opened.click(ref(page, 'button "Keep shopping in a new tab"'))  # an old ref
        tab = await opened.click(ref(after, 'button "Keep shopping in a new tab"'))
        return str(refused.value), after, tab, len(opened.context.pages)

    refused, after, tab, pages = in_browser(job_dir, steps)

    # The other site never loads, and the browser is back on checkout.
    assert "stay on its pages" in refused
    assert after.startswith("Page: /store/checkout_v4\n")
    assert tab.startswith("Page: /store/checkout_v4\n")
    assert pages == 1


def test_a_report_points_at_different_elements_the_items_and_total_lines_and_every_item(
    job_dir: Path,
) -> None:
    async def steps(opened: Browser) -> list[str]:
        page = await schedule_and_tip(opened, await to_checkout(opened))
        refused = []
        free = ref(page, "Delivery with Instacart")  # "your price $0.00"
        tries = [
            # The $0 delivery line as items, tip, and total adds up (0 + 0 = 0).
            ReportRefs(
                free,
                [],
                free,
                free,
                ref(page, r'button "\w{3}, \w{3} \d+, 4pm'),
                ref(page, 'button "1 Main Street'),
                ref(page, r'button "\*4242'),
            ),
            refs_of(page, ["Service fee", "Service fee"]),
        ]
        for refs in tries:
            try:
                await checkout.report(job_dir, opened, refs)
            except CheckoutRefused as error:
                refused.append(str(error))
        try:
            scheduled = ref(page, r'button "\w{3}, \w{3} \d+, 4pm')
            await checkout.record_replacements(job_dir, opened, [scheduled])
        except CheckoutRefused as error:
            refused.append(str(error))
        return refused

    reused, repeated, one_item = in_browser(job_dir, steps)

    assert "point at each one once" in reused
    assert "point at each one once" in repeated
    assert "The cart has 2 products" in one_item
    assert load_state(job_dir).checkout is None


def test_signed_out_isnt_taken_after_checkout_is_reported(job_dir: Path) -> None:
    async def steps(opened: Browser) -> None:
        page = await schedule_and_tip(opened, await to_checkout(opened))
        await checkout.report(
            job_dir, opened, refs_of(page, ["Delivery with Instacart", "Service fee"])
        )
        page = await opened.open("/store/sign-in")
        with pytest.raises(CheckoutRefused, match="you're done"):
            await checkout.mark_signed_out(job_dir, opened, ref(page, 'button "Log in"'))

    in_browser(job_dir, steps)
    assert not load_state(job_dir).signed_out


def test_an_address_is_read_to_its_last_zip_code() -> None:
    shown = "10000 Main Street Rochester, NY 14604 Leave at the door"
    assert checkout.address_from(shown) == "10000 Main Street Rochester, NY 14604"


def test_the_session_is_handed_back_only_with_a_report(job_dir: Path) -> None:
    # Not as the tools stop: the worker may have cleared the job directory by then.
    in_browser(job_dir, lambda opened: opened.open("/store/wegmans/storefront"))
    assert not renewed_file(job_dir).exists()

    async def signed_out(opened: Browser) -> None:
        page = await opened.open("/store/wegmans/storefront")
        with pytest.raises(CheckoutRefused, match="isn't a sign-in control"):
            await checkout.mark_signed_out(job_dir, opened, ref(page, 'button "View cart"'))
        page = await opened.open("/store/sign-in")
        await checkout.mark_signed_out(job_dir, opened, ref(page, 'button "Log in"'))

    in_browser(job_dir, signed_out)
    assert load_state(job_dir).signed_out
    assert not renewed_file(job_dir).exists()


def test_an_open_dialog_is_shown_alone_and_find_looks_everywhere(job_dir: Path) -> None:
    async def steps(opened: Browser) -> tuple[str, str, str]:
        page = await opened.open("/store/wegmans/storefront")
        cart = await opened.click(ref(page, 'button "View cart"'))
        return page, cart, await opened.find("go to checkout")

    page, cart, found = in_browser(job_dir, steps)

    assert "The page:" in page and 'heading "Wegmans"' in page
    # The cart, as a person sees it: not the store behind it.
    assert "A dialog is open" in cart and 'heading "Wegmans"' not in cart
    assert 'button "Replace with best match"' in cart
    assert re.fullmatch(
        r'Page: /store/wegmans/storefront\n- button "Go to checkout" \[ref=\w+\]', found
    )


# The worker image's browser: Playwright runs only the build it was released with.

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"


def browser_in_image(dockerfile: str) -> tuple[str, str]:
    """The Chrome Headless Shell build the worker image downloads, and the revision
    directory it puts it in (where Playwright looks for it)."""
    build = re.search(r"/cft/([\d.]+)/linux64/chrome-headless-shell-linux64\.zip", dockerfile)
    revision = re.search(r"/opt/ms-playwright/chromium_headless_shell-(\d+)\b", dockerfile)
    assert build and revision, "The Dockerfile no longer says which browser build it installs."
    return build.group(1), revision.group(1)


def test_the_worker_image_has_the_browser_the_locked_playwright_runs() -> None:
    import playwright

    browsers = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    [wanted] = [
        browser
        for browser in json.loads(browsers.read_text())["browsers"]
        if browser["name"] == "chromium-headless-shell"
    ]

    assert browser_in_image(DOCKERFILE.read_text()) == (
        wanted["browserVersion"],
        wanted["revision"],
    ), "Update docker/Dockerfile's Chrome Headless Shell (its build, checksum, and directory)."
