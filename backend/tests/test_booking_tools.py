"""The agent's booking tools (castle_chef_agent.checkout, a `book` job), in a
headless browser on stand-ins for Instacart's pages (fixtures/checkout): choosing
the window by the rule, what the report must show, pressing Place order only once
the worker says to, and reading the confirmation."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from castle_chef_agent import checkout
from castle_chef_agent.checkout import (
    CLICK_ANSWER_FILE,
    CLICK_REQUEST_FILE,
    SESSION_FILE,
    STATE_FILE,
    BookTarget,
    Browser,
    CheckoutJob,
    CheckoutRefused,
    ClickAnswer,
    ClickReply,
    ClickRequest,
    ReportRefs,
    load_state,
)
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.windows import Window

from tests.test_checkout_tools import SESSION, ref

# The stand-in's days are tomorrow and the four after it; its windows on each
# are 10am–Noon, Noon–2pm, 2pm–4pm, and 4pm–6pm.
DAY = date.today() + timedelta(days=3)
ADDRESS = "1 Main Street Rochester, NY 14604"
CARD = "Visa ending 4242"


def tab_name(day: date) -> str:
    """The stand-in's tab for the day: "Sun, Oct 4"."""
    return f"{day:%a, %b} {day.day}"


def make_job_dir(tmp_path: Path, site: str, **book: Any) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    sessions = tmp_path / "session"
    sessions.mkdir()
    target = {"day": DAY, "by": time(16), "address": ADDRESS, "card": CARD} | book
    write_context(
        directory,
        CheckoutJob(
            store="Wegmans",
            tip_percent=10,
            tip_max_cents=1500,
            cart_items=2,
            session_dir=sessions,
            site=site,
            book=BookTarget(**target),
        ),
    )
    (sessions / SESSION_FILE).write_text(json.dumps(SESSION))
    return directory


@pytest.fixture
def job_dir(tmp_path: Path, instacart_site: str) -> Path:
    return make_job_dir(tmp_path, instacart_site)


def in_browser(job_dir: Path, steps: Callable[[Browser], Awaitable[Any]]) -> Any:
    async def run() -> Any:
        async with checkout.browser(job_dir) as opened:
            return await steps(opened)

    return asyncio.run(run())


async def schedule_dialog(opened: Browser) -> str:
    """As a booking goes: the store, the cart, checkout, and Schedule's dialog."""
    page = await opened.open("/store/wegmans/storefront")
    page = await opened.click(ref(page, 'button "View cart"'))
    page = await opened.click(ref(page, 'button "Go to checkout"'))
    return await opened.click(ref(page, 'button "Schedule'))


async def the_days_windows(opened: Browser, day: date = DAY) -> tuple[str, str, list[str]]:
    """Schedule's dialog on the day's tab: (page, the tab's ref, its windows' refs)."""
    page = await schedule_dialog(opened)
    page = await opened.click(ref(page, f'tab "{tab_name(day)}'))
    windows = [
        ref(page, f'button "{name}') for name in ("10am–Noon", "Noon–2pm", "2pm–4pm", "4pm–6pm")
    ]
    return page, ref(page, f'tab "{tab_name(day)}'), windows


async def chosen_and_tipped(opened: Browser) -> str:
    """The window the rule chose, clicked, and the rule's tip set."""
    page, tab, windows = await the_days_windows(opened)
    said = await checkout.choose_window(opened.job_dir, opened, tab, windows)
    page = await opened.click(said.split("click ")[1].split(",")[0])
    page = await opened.click(ref(page, 'button "Other"'))
    page = await opened.type(ref(page, 'textbox "Other amount"'), "1.96")
    return await opened.click(ref(page, 'button "Save Tip"'))


def report_refs(page: str, delivery: str | None = None) -> ReportRefs:
    return ReportRefs(
        items=ref(page, "2 items"),
        lines=[ref(page, "Delivery with"), ref(page, "Service fee")],
        tip=ref(page, r"\$1\.96"),
        total=ref(page, "Total"),
        delivery=delivery or ref(page, f'button "{tab_name(DAY)}, '),
        address=ref(page, 'button "1 Main Street'),
        card=ref(page, r'button "\*4242'),
    )


async def reported(opened: Browser) -> str:
    page = await chosen_and_tipped(opened)
    await checkout.report(opened.job_dir, opened, report_refs(page))
    return page


async def worker_answers(job_dir: Path, answer: ClickAnswer) -> ClickRequest:
    """Stands in for the worker: waits for the click request, then answers it."""
    request = job_dir / CLICK_REQUEST_FILE
    while not request.exists():
        await asyncio.sleep(0.05)
    asked = ClickRequest.model_validate_json(request.read_text())
    (job_dir / CLICK_ANSWER_FILE).write_text(ClickReply(answer=answer).model_dump_json())
    return asked


def test_the_window_is_the_latest_on_the_day_that_ends_by_the_time(job_dir: Path) -> None:
    async def steps(opened: Browser) -> str:
        _, tab, windows = await the_days_windows(opened)
        return await checkout.choose_window(job_dir, opened, tab, windows)

    said = in_browser(job_dir, steps)

    assert said.startswith("Book 2–4 PM: click ")
    assert load_state(job_dir).window == Window(start=time(14), end=time(16))


def test_with_no_window_ending_by_the_time_its_the_one_the_time_falls_in(
    tmp_path: Path, instacart_site: str
) -> None:
    job_dir = make_job_dir(tmp_path, instacart_site, by=time(11, 30))

    async def steps(opened: Browser) -> str:
        _, tab, windows = await the_days_windows(opened)
        return await checkout.choose_window(job_dir, opened, tab, windows)

    assert in_browser(job_dir, steps).startswith("Book 10 AM–12 PM")


def test_a_day_with_no_window_for_the_time_needs_another_day_or_time(
    tmp_path: Path, instacart_site: str
) -> None:
    job_dir = make_job_dir(tmp_path, instacart_site, by=time(9))

    async def steps(opened: Browser) -> str:
        _, tab, windows = await the_days_windows(opened)
        return await checkout.choose_window(job_dir, opened, tab, windows)

    said = in_browser(job_dir, steps)

    assert "Stop here" in said
    state = load_state(job_dir)
    assert (state.window, state.no_window) == (None, True)


def test_the_window_is_chosen_only_on_the_orders_day_once_its_tab_is_selected(
    job_dir: Path,
) -> None:
    async def steps(opened: Browser) -> list[str]:
        refused = []
        page = await schedule_dialog(opened)
        # Tomorrow's tab is the one selected: not the order's day.
        tomorrow = ref(page, f'tab "{tab_name(date.today() + timedelta(days=1))}')
        windows = [ref(page, 'button "2pm–4pm'), ref(page, 'button "4pm–6pm')]
        theirs = ref(page, f'tab "{tab_name(DAY)}')
        for day, offered in [(tomorrow, windows), (theirs, windows)]:
            try:
                await checkout.choose_window(job_dir, opened, day, offered)
            except CheckoutRefused as error:
                refused.append(str(error))
        page = await opened.click(theirs)
        try:
            # Not a window.
            await checkout.choose_window(
                job_dir,
                opened,
                ref(page, f'tab "{tab_name(DAY)}'),
                [ref(page, f'tab "{tab_name(date.today() + timedelta(days=1))}')],
            )
        except CheckoutRefused as error:
            refused.append(str(error))
        return refused

    refused = in_browser(job_dir, steps)

    assert refused[0].startswith("That's ") and f"tab for {tab_name(DAY)}" in refused[0]
    assert refused[1] == "Click that day's tab first, then give its windows."
    assert "isn't a delivery window" in refused[2]
    assert load_state(job_dir).window is None


def test_the_report_must_show_the_window_chosen(job_dir: Path) -> None:
    async def steps(opened: Browser) -> list[str]:
        refused = []
        page, tab, windows = await the_days_windows(opened)
        try:
            # Refused before anything is read.
            await checkout.report(
                job_dir, opened, ReportRefs("e1", [], "e2", "e3", "e4", "e5", "e6")
            )
        except CheckoutRefused as error:
            refused.append(str(error))
        await checkout.choose_window(job_dir, opened, tab, windows)
        # Another window than the one chosen.
        page = await opened.click(ref(page, 'button "4pm–6pm'))
        page = await opened.click(ref(page, 'button "Other"'))
        page = await opened.type(ref(page, 'textbox "Other amount"'), "1.96")
        page = await opened.click(ref(page, 'button "Save Tip"'))
        try:
            await checkout.report(job_dir, opened, report_refs(page))
        except CheckoutRefused as error:
            refused.append(str(error))
        return refused

    first, second = in_browser(job_dir, steps)

    assert first == "Choose the delivery window first (`choose_window`)."
    assert f"not the window to book ({tab_name(DAY)}, 2–4 PM)" in second
    assert load_state(job_dir).checkout is None


def test_a_changed_address_or_card_is_recorded_and_nothing_is_ordered(
    tmp_path: Path, instacart_site: str
) -> None:
    job_dir = make_job_dir(
        tmp_path,
        instacart_site,
        address="9 Elm Street Rochester, NY 14620",
        card="Amex ending 0005",
    )

    async def steps(opened: Browser) -> str:
        page = await chosen_and_tipped(opened)
        with pytest.raises(CheckoutRefused) as refused:
            await checkout.report(job_dir, opened, report_refs(page))
        return str(refused.value)

    said = in_browser(job_dir, steps)

    assert said.endswith("That's recorded, and nothing is ordered: stop here.")
    state = load_state(job_dir)
    assert state.changed == (
        f"Instacart now delivers to {ADDRESS}, not 9 Elm Street Rochester, NY 14620. "
        "Instacart now charges Visa ending 4242, not Amex ending 0005."
    )
    assert state.checkout is None


def test_place_order_presses_it_once_the_worker_says_go_then_the_confirmation_is_read(
    job_dir: Path,
) -> None:
    async def steps(opened: Browser) -> tuple[str, ClickRequest, str]:
        page = await reported(opened)
        answering = asyncio.create_task(worker_answers(job_dir, ClickAnswer.GO))
        said = await checkout.place_order(job_dir, opened, ref(page, 'button "Place order"'))
        asked = await answering
        page = await opened.snapshot()
        confirmed = await checkout.record_confirmation(
            job_dir, opened, ref(page, '"Order #'), ref(page, "Arrives"), ref(page, "Total")
        )
        return said, asked, confirmed.order_number

    said, asked, number = in_browser(job_dir, steps)

    assert said.startswith("Place order was pressed.")
    assert asked == ClickRequest(total_cents=1959 + 249 + 196)
    assert number == "18234567"
    state = load_state(job_dir)
    assert (state.click, state.clicked) == (ClickAnswer.GO, True)
    assert state.confirmation is not None
    assert state.confirmation.window == Window(start=time(14), end=time(16))
    assert state.confirmation.total_cents == 1959 + 249 + 196


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (ClickAnswer.DRY_RUN, "This is a dry run"),
        (ClickAnswer.OFF, "Booking isn't turned on yet"),
        (ClickAnswer.REFUSED, "This order mustn't be placed now"),
    ],
)
def test_place_order_isnt_pressed_unless_the_worker_says_go(
    job_dir: Path, answer: ClickAnswer, said: str
) -> None:
    async def steps(opened: Browser) -> tuple[str, str]:
        page = await reported(opened)
        answering = asyncio.create_task(worker_answers(job_dir, answer))
        reply = await checkout.place_order(job_dir, opened, ref(page, 'button "Place order"'))
        await answering
        return reply, opened.path()

    reply, path = in_browser(job_dir, steps)

    assert reply.startswith(said)
    assert path == "/store/checkout_v4"  # still at checkout: nothing ordered
    state = load_state(job_dir)
    assert (state.click, state.clicked) == (answer, False)


def test_without_the_workers_answer_place_order_isnt_pressed(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checkout, "CLICK_ANSWER_TIMEOUT_S", 0.5)

    async def steps(opened: Browser) -> tuple[str, str]:
        page = await reported(opened)
        with pytest.raises(CheckoutRefused) as refused:
            await checkout.place_order(job_dir, opened, ref(page, 'button "Place order"'))
        return str(refused.value), opened.path()

    said, path = in_browser(job_dir, steps)

    assert said == "Booking didn't answer, so nothing was ordered. Stop here."
    assert path == "/store/checkout_v4"
    assert load_state(job_dir).clicked is False


def test_place_order_is_asked_for_once_after_the_report_and_only_of_place_order(
    job_dir: Path,
) -> None:
    async def steps(opened: Browser) -> list[str]:
        refused = []
        page = await chosen_and_tipped(opened)
        place = ref(page, 'button "Place order"')
        try:
            await checkout.place_order(job_dir, opened, place)  # before the report
        except CheckoutRefused as error:
            refused.append(str(error))
        await checkout.report(job_dir, opened, report_refs(page))
        try:
            await checkout.place_order(job_dir, opened, ref(page, 'button "Other"'))
        except CheckoutRefused as error:
            refused.append(str(error))
        answering = asyncio.create_task(worker_answers(job_dir, ClickAnswer.DRY_RUN))
        await checkout.place_order(job_dir, opened, place)
        await answering
        for again in (
            checkout.place_order(job_dir, opened, place),
            checkout.report(job_dir, opened, report_refs(page)),
        ):
            try:
                await again
            except CheckoutRefused as error:
                refused.append(str(error))
        return refused

    refused = in_browser(job_dir, steps)

    assert refused == [
        "Report checkout first (`report`); it's checked before ordering.",
        "That isn't the Place order button.",
        "Place order was asked for already: it's never asked for twice.",
        "Place order was asked for already: don't report again.",
    ]


def test_no_confirmation_is_recorded_before_place_order_was_pressed(job_dir: Path) -> None:
    async def steps(opened: Browser) -> list[str]:
        refused = []
        page = await reported(opened)
        total = ref(page, "Total")
        for attempt in (
            checkout.record_confirmation(job_dir, opened, total, total, total),
            checkout.mark_not_confirmed(job_dir),
        ):
            try:
                await attempt
            except CheckoutRefused as error:
                refused.append(str(error))
        return refused

    assert in_browser(job_dir, steps) == [
        "Nothing was ordered: there's no confirmation to record.",
        "Nothing was ordered: there's nothing to confirm.",
    ]


def test_a_press_with_no_confirmation_to_be_found_is_recorded(job_dir: Path) -> None:
    async def steps(opened: Browser) -> None:
        page = await reported(opened)
        answering = asyncio.create_task(worker_answers(job_dir, ClickAnswer.GO))
        await checkout.place_order(job_dir, opened, ref(page, 'button "Place order"'))
        await answering
        await checkout.mark_not_confirmed(job_dir)

    in_browser(job_dir, steps)

    state = load_state(job_dir)
    assert (state.clicked, state.confirmation, state.not_confirmed) == (True, None, True)


def test_place_order_asked_for_twice_at_once_is_pressed_once(job_dir: Path) -> None:
    async def steps(opened: Browser) -> tuple[list[str | BaseException], str]:
        page = await reported(opened)
        place = ref(page, 'button "Place order"')
        answering = asyncio.create_task(worker_answers(job_dir, ClickAnswer.GO))
        results = await asyncio.gather(
            checkout.place_order(job_dir, opened, place),
            checkout.place_order(job_dir, opened, place),
            return_exceptions=True,
        )
        await answering
        return results, opened.path()

    results, path = in_browser(job_dir, steps)

    pressed = [r for r in results if isinstance(r, str) and r.startswith("Place order was pressed")]
    refused = [str(r) for r in results if isinstance(r, CheckoutRefused)]
    assert len(pressed) == 1
    assert refused == ["Place order was asked for already: it's never asked for twice."]
    assert path == "/store/orders/confirmation"


def test_the_report_must_show_the_orders_day_too_not_the_same_window_on_another(
    job_dir: Path,
) -> None:
    async def steps(opened: Browser) -> str:
        page, tab, windows = await the_days_windows(opened)
        await checkout.choose_window(job_dir, opened, tab, windows)
        # The same window, on the day after.
        page = await opened.click(ref(page, f'tab "{tab_name(DAY + timedelta(days=1))}'))
        page = await opened.click(ref(page, 'button "2pm–4pm'))
        page = await opened.click(ref(page, 'button "Other"'))
        page = await opened.type(ref(page, 'textbox "Other amount"'), "1.96")
        page = await opened.click(ref(page, 'button "Save Tip"'))
        other_day = ref(page, f'button "{tab_name(DAY + timedelta(days=1))}, ')
        with pytest.raises(CheckoutRefused) as refused:
            await checkout.report(job_dir, opened, report_refs(page, other_day))
        return str(refused.value)

    said = in_browser(job_dir, steps)

    assert f"not the window to book ({tab_name(DAY)}, 2–4 PM)" in said
    assert load_state(job_dir).checkout is None


def test_a_confirmation_that_isnt_this_orders_isnt_recorded(job_dir: Path) -> None:
    async def steps(opened: Browser) -> str:
        page = await reported(opened)
        answering = asyncio.create_task(worker_answers(job_dir, ClickAnswer.GO))
        await checkout.place_order(job_dir, opened, ref(page, 'button "Place order"'))
        await answering
        # As if checkout had shown another total than the order found.
        state = load_state(job_dir)
        assert state.checkout is not None
        state.checkout = state.checkout.model_copy(update={"total_cents": 9999})
        (job_dir / STATE_FILE).write_text(state.model_dump_json())
        page = await opened.snapshot()
        with pytest.raises(CheckoutRefused) as refused:
            await checkout.record_confirmation(
                job_dir, opened, ref(page, '"Order #'), ref(page, "Arrives"), ref(page, "Total")
            )
        return str(refused.value)

    said = in_browser(job_dir, steps)

    assert said.startswith("That isn't this order")
    assert said.endswith("call `not_confirmed`.")
    assert load_state(job_dir).confirmation is None
