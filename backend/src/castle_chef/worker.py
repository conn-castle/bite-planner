"""Runs a job: the agent in a job directory (castle_chef.harness), then its result
(meals, a shopping list, its products, a quote, or a booking) saved."""

import json
import logging
import os
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, assert_never
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from castle_chef_agent import windows
from castle_chef_agent.checkout import (
    CLICK_ANSWER_FILE,
    CLICK_REQUEST_FILE,
    RENEWED_SESSION_FILE,
    SESSION_FILE,
    BookTarget,
    CheckoutJob,
    ClickAnswer,
    ClickReply,
)
from castle_chef_agent.checkout import load_state as load_checkout_state
from castle_chef_agent.instacart import InstacartError
from castle_chef_agent.jobfiles import replace_file
from castle_chef_agent.meals import MealInput, MealsJob, load_meals, meal_problems
from castle_chef_agent.products import ItemChoice
from castle_chef_agent.server import Tools
from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, col, select

from castle_chef import (
    booking,
    cart,
    orders,
    products,
    prompts,
    push,
    quotes,
    ratings,
    shopping,
)
from castle_chef.checkout import (
    SIGNED_OUT_ERROR,
    Booking,
    BookIssue,
    CartLine,
    CheckoutError,
    Quote,
    SessionStore,
    Swap,
    session_store,
)
from castle_chef.db import for_update, get_engine, get_session
from castle_chef.harness import AgentRunError, agent_lock, run_agent
from castle_chef.jobs import SUPERSEDED_ERROR, claim_job, create_shop, finish_job
from castle_chef.meals import add_meals
from castle_chef.models import (
    BookState,
    GroceryOrder,
    Household,
    Job,
    JobKind,
    ListSource,
    ProductGone,
    ShoppingListItem,
)
from castle_chef.notifications import ideas_ready, list_failed, list_ready, notify_booking
from castle_chef.products import InvalidProducts
from castle_chef.records import keeping_record
from castle_chef.settings import REPO_ROOT, current_release, get_settings
from castle_chef.shopping import InvalidList, ListEntry

log = logging.getLogger(__name__)

# Exit status when the database is behind this code: the release's migration
# did not run (in AWS, the failure handler reports the stopped task).
SCHEMA_BEHIND_EXIT_CODE = 78


class SchemaBehindError(Exception):
    pass


class JobDiscarded(Exception):
    """The job's draft was discarded (with the job) after it was claimed."""


@dataclass(frozen=True)
class Meals:
    """A generate_meals job's result."""

    meals: list[MealInput]


@dataclass(frozen=True)
class ListItems:
    """A build_list job's result: the checked items and staples, and the picks they're for."""

    items: list[ListEntry]
    source: ListSource


@dataclass(frozen=True)
class Products:
    """A shop job's result: each numbered item's product and alternatives, for a build's list."""

    build_id: UUID
    items: dict[int, UUID]
    choices: dict[int, ItemChoice]


@dataclass(frozen=True)
class Booked:
    """A book job's result: how booking the order went, and the list items whose
    product was gone and swapped for an alternative (item id: the alternative's
    product id), which the list keeps once it's booked."""

    state: BookState
    booking: Booking | None = None
    issue: BookIssue | None = None
    swapped: dict[UUID, str] = field(default_factory=dict)


type JobResult = Meals | ListItems | Products | Quote | Booked


def run_job(job_id: UUID) -> None:
    """Run a job: claim it, run the agent, and save its result (or its failure);
    then the job it says to run next in this task (a built list's shopping).

    Raises SchemaBehindError if the database is behind this code. A job someone
    else already claimed is left alone, and a job that no longer exists was
    discarded with its draft, which is not an error.
    """
    check_schema()
    next_id: UUID | None = job_id
    while next_id is not None:
        next_id = _run_one(next_id)


def _run_one(job_id: UUID) -> UUID | None:
    """Claim the job and run it; returns the job to run next, if any."""
    fields = {"job_id": str(job_id)}
    log.info("worker.started", extra=current_release() | fields)
    try:
        with get_session() as session:
            job = claim_job(session, job_id)
    except LookupError:
        log.info("job.discarded", extra=fields)
        return None
    if job is None:
        log.warning("job.already_claimed", extra=fields)
        return None
    log.info("job.claimed", extra=fields | {"kind": job.kind})
    # Whatever happens, what the agent was given and did is kept.
    with keeping_record(job_id):
        result = _process(job, fields)
        if result is None:
            return None
        saved, next_id = _save(job, result, fields)
        if saved and next_id is None:
            _notify_succeeded(job)
    return next_id


def _process(job: Job, fields: dict[str, str]) -> JobResult | None:
    """Runs a claimed job; None if it didn't finish (it's failed, and said so)."""
    try:
        return process_job(job)
    except JobDiscarded:
        log.info("job.discarded", extra=fields)
        return None
    except (
        AgentRunError,
        ValidationError,
        InvalidList,
        InvalidProducts,
        InstacartError,
        cart.CartMismatch,
        cart.CartNotPutBack,
        CheckoutError,
    ) as error:
        log.error("job.failed", extra=fields | {"error": str(error)})
        _finish(job.id, error=str(error))
        _notify_failed(job, str(error))
        return None
    except Exception as error:
        log.exception("job.crashed", extra=fields)
        _finish(job.id, error=f"Unexpected worker error: {error!r}")
        _notify_failed(job, "Booking stopped with an unexpected error.")
        return None


def _counts(result: JobResult) -> dict[str, object]:
    """What the job's log lines say of its result."""
    match result:
        case Meals():
            return {"meal_count": len(result.meals)}
        case Quote():
            return {"blocked": result.blocked_because is not None}
        case Booked():
            return {"book_state": result.state.value}
        case ListItems() | Products():
            return {"item_count": len(result.items)}
        case _:
            assert_never(result)


def _save(job: Job, result: JobResult, fields: dict[str, str]) -> tuple[bool, UUID | None]:
    """Save the job's result and its success, in one transaction. Returns whether
    they were saved, and the job to run next (a built list's shopping)."""
    counts = _counts(result)
    shop_id: UUID | None = None
    try:
        with get_session() as session:
            # Locking the job keeps its draft from being discarded mid-save; a job
            # already gone was discarded with its draft, which is not an error.
            if session.exec(for_update(select(Job).where(Job.id == job.id))).first() is None:
                log.info("job.discarded", extra=fields | counts)
                return False, None
            # The result and the job's success are committed together, and only
            # if the job is still running (not failed as stale, or as superseded
            # by a newer list, meanwhile).
            match result:
                case Meals():
                    add_meals(session, job.id, result.meals)
                case Quote():
                    session.execute(update(Job).where(col(Job.id) == job.id).values(quote=result))
                case Booked():
                    if result.state == BookState.BOOKED:
                        # What was bought instead stays on the order's list.
                        for item_id, product_id in result.swapped.items():
                            item = session.get_one(ShoppingListItem, item_id)
                            orders.use_alternative(item, product_id)
                            session.add(item)
                    booking.finish(session, job.id, result.state, result.booking, result.issue)
                case Products():
                    current = shopping.current_build(session, job.grocery_order_id)
                    if current is None or current.id != result.build_id:
                        # Its list was replaced (the picks changed) while it shopped.
                        finish_job(session, job.id, error=SUPERSEDED_ERROR)
                        log.info("job.superseded", extra=fields | counts)
                        return False, None
                    products.save_choices(session, result.items, result.choices)
                case ListItems():
                    shopping.add_items(session, job.id, result.items, result.source)
                    # The list is shopped next, in this task (one wait for the member).
                    shop_id = create_shop(session, job, uuid4(), job.requested_by).id
                case _:
                    assert_never(result)
            if not finish_job(session, job.id):
                stopped = session.get(Job, job.id)
                if stopped is not None and stopped.error == SUPERSEDED_ERROR:
                    # A newer list for other picks replaced it: expected.
                    log.info("job.superseded", extra=fields | counts)
                else:
                    log.error("job.no_longer_running", extra=fields | counts)
                return False, None
    except Exception as error:
        # If the commit went through despite the error (a lost acknowledgement),
        # the job is no longer running and this changes nothing.
        log.exception("job.save_failed", extra=fields)
        _finish(job.id, error=f"Could not save the result: {error!r}")
        # A booking whose outcome couldn't be saved is settled now (checked on
        # Instacart once it was clicked), not left booking.
        _notify_failed(job, "Booking stopped before its result was saved.")
        return False, None
    log.info("job.succeeded", extra=fields | counts)
    return True, shop_id


def _notify_succeeded(job: Job) -> None:
    """Say a job that succeeded is done, to whoever waits for it."""
    if job.kind == JobKind.BOOK:
        notify_booking(job.grocery_order_id)
    # A quote's member waits for it on screen (the final review): nothing to send.
    elif job.kind != JobKind.QUOTE:
        _notify_requester(job.id)


def _notify_requester(job_id: UUID) -> None:
    """ "Your meal ideas are ready", or "Your shopping list is ready" while it's
    still the list for the order's picks, to whoever started the job. The job's
    success stands whatever happens here; a failure is logged as an error."""
    try:
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None:  # discarded with its draft meanwhile
                return
            if job.kind == JobKind.GENERATE_MEALS:
                notification = ideas_ready(session, job)
            else:
                # A list is ready once it's shopped, while it's still for the picks.
                build = shopping.current_build(session, job.grocery_order_id)
                if job.kind != JobKind.SHOP or build is None or build.id != job.build_id:
                    log.info("job.notify_skipped", extra={"job_id": str(job_id)})
                    return
                notification = list_ready(session, build)
            requester = job.requested_by
        push.notify(requester, notification)
    except Exception:
        log.exception("job.notify_failed", extra={"job_id": str(job_id)})


def _notify_failed(job: Job, error: str) -> None:
    """ "Couldn't shop your list", to whoever is waiting for it, while it's still the
    list for the order's picks (a failed batch or build shows in the app); a
    failed booking settles its order (not booked, or check Instacart once it was
    clicked) and says so. A failure here is logged as an error."""
    if job.kind == JobKind.BOOK:
        try:
            with get_session() as session:
                order = booking.job_failed(session, job.id, error)
        except Exception:
            log.exception("booking.settle_failed", extra={"job_id": str(job.id)})
            return
        if order is not None:
            notify_booking(order.id)
        return
    if job.kind != JobKind.SHOP or job.build_id is None:
        return
    try:
        with get_session() as session:
            build = shopping.current_build(session, job.grocery_order_id)
            if build is None or build.id != job.build_id:
                return
            notification = list_failed(session, build)
        push.notify(job.requested_by, notification)
    except Exception:
        log.exception("job.notify_failed", extra={"job_id": str(job.id)})


def _finish(job_id: UUID, error: str) -> None:
    with get_session() as session:
        if finish_job(session, job_id, error=error):
            return
        if session.get(Job, job_id) is None:
            log.info("job.discarded", extra={"job_id": str(job_id)})
        else:
            log.error("job.no_longer_running", extra={"job_id": str(job_id)})


def check_schema() -> None:
    """Refuse to run against a database whose migrations are older than this code.

    A database with revisions this code does not know is newer (the worker was
    rolled back to an older release), which expand/contract migrations allow.
    """
    script = ScriptDirectory.from_config(Config(REPO_ROOT / "backend" / "alembic.ini"))
    with get_engine().connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    heads = set(script.get_heads())
    if current == heads:
        return
    fields = {"database": sorted(current), "code": sorted(heads)}
    if any(not _is_known_revision(script, revision) for revision in current):
        log.warning("schema.ahead", extra=fields)
        return
    raise SchemaBehindError(
        f"The database is at migration {sorted(current) or 'none'}, behind this code's "
        f"{sorted(heads)}. Its migration has not run: releases migrate Neon `main` "
        "before publishing; on a dev branch, run `alembic upgrade head`."
    )


def _is_known_revision(script: ScriptDirectory, revision: str) -> bool:
    try:
        script.get_revision(revision)
    except CommandError:
        return False
    return True


def process_job(job: Job) -> JobResult:
    settings = get_settings()
    job_dir = settings.jobs_dir / str(job.id)
    match job.kind:
        case JobKind.QUOTE:
            return process_quote(job, job_dir)
        case JobKind.BOOK:
            return process_book(job, job_dir)
        case JobKind.SHOP:
            if settings.codex_home is None:
                raise AgentRunError(
                    "No Codex login here (CASTLE_CHEF_CODEX_HOME): can't reach Instacart."
                )
            with get_session() as session:
                build = session.get(Job, job.build_id) if job.build_id else None
                if build is None:
                    raise JobDiscarded
                context = products.shop_context(session, build, settings.instacart_store)
            lock = settings.codex_lock_file
            instacart_args = [
                "--codex-home", str(settings.codex_home),
                *(["--codex-lock-file", str(lock)] if lock else []),
                "--codex-command", settings.codex_command,
                "--store", settings.instacart_store,
            ]  # fmt: skip
            run_agent(job_dir, context.prompt, Tools.PRODUCTS, context.job, instacart_args)
            # Checked against the items as the worker numbered them.
            return Products(
                build_id=build.id,
                items=context.items,
                choices=products.finished_choices(job_dir, context.job),
            )
        case JobKind.BUILD_LIST:
            if job.list_source is None:
                raise AgentRunError(f"List job {job.id} has no picks to build from.")
            with get_session() as session:
                scaled = shopping.scaled_meals(session, job.list_source)
            lines = shopping.list_job(scaled)
            run_agent(job_dir, shopping.build_list_prompt(scaled), Tools.SHOPPING_LIST, lines)
            # Checked against the lines as the worker made them: the agent can write job.json.
            return ListItems(items=shopping.finished_list(job_dir, lines), source=job.list_source)
        case JobKind.GENERATE_MEALS:
            count = settings.meals_per_batch
            with get_session() as session:
                prompt = generate_meals_prompt(session, job, count)
            run_agent(job_dir, prompt, Tools.MEALS, MealsJob(count=count))
            if problems := meal_problems(job_dir, count):
                raise AgentRunError(f"The agent didn't finish: {' '.join(problems)}")
            return Meals(meals=load_meals(job_dir))
        case _:
            assert_never(job.kind)


def process_quote(job: Job, job_dir: Path) -> Quote:
    """Fill the household's cart with the order's products (its own items set
    aside meanwhile), and have the agent read what checkout shows, in a browser
    signed in with the household's Instacart session."""
    request = job.quote_request
    if request is None:
        raise AgentRunError(f"Quote job {job.id} has nothing to price.")
    with get_session() as session:
        order = session.get(GroceryOrder, job.grocery_order_id)
        if order is None:
            raise JobDiscarded
        household_id = order.household_id
        names = quotes.product_names(session, order.id)
    store = get_settings().instacart_store
    sessions = session_store()
    # The agent's lock before the Codex login's (the cart), the order shopping takes
    # them in (its tools reach Instacart while its agent runs), so they can't deadlock.
    with agent_lock(job_id=str(job.id)), cart.filled(household_id, request.lines) as filled:
        cart.check(filled.cart, request.lines, names)
        if filled.cart.below_minimum:
            return Quote(blocked_because=filled.cart.blocked_because)
        if not filled.cart.can_check_out:
            # Not something more groceries would fix: said as it is.
            raise CheckoutError(
                f"Instacart won't check this cart out: {filled.cart.blocked_because}"
            )
        prompt = prompts.fill("checkout_quote", store=store, slug=store.lower())
        with _checkout(
            job_dir,
            sessions,
            tip_percent=request.tip_percent,
            tip_max_cents=request.tip_max_cents,
            cart_items=len(request.lines),
        ) as context:
            run_agent(job_dir, prompt, Tools.CHECKOUT, context, agent_locked=True)
    read = load_checkout_state(job_dir)
    if read.signed_out:
        raise CheckoutError(SIGNED_OUT_ERROR)
    if read.checkout is None:
        raise AgentRunError(f"The agent didn't report checkout; see {job_dir}.")
    return Quote(checkout=read.checkout)


@dataclass(frozen=True)
class _ToBook:
    """What a book job books, read from its order as it starts."""

    household_id: UUID
    lines: list[CartLine]
    tip_percent: int
    tip_max_cents: int
    target: BookTarget
    product_gone: ProductGone
    # Each product's name, the list items that buy it, and its alternatives.
    names: dict[str, str]
    items: dict[str, list[ShoppingListItem]]
    alternatives: dict[str, list[CartLine]]


def _to_book(job: Job) -> _ToBook:
    """What the job books: its order's list as approved (it can't change while
    approved), the household's tip rule and setting for a product that's gone,
    and the address and card the approved total showed."""
    with get_session() as session:
        order = session.get(GroceryOrder, job.grocery_order_id)
        if order is None:  # cancelled, with its jobs
            raise JobDiscarded
        if order.book_job_id != job.id or order.book_state != BookState.BOOKING:
            raise CheckoutError(f"Order {order.id} isn't being booked by this job.")
        household = session.get_one(Household, order.household_id)
        approved = session.get(Job, order.quote_id) if order.quote_id else None
        if approved is None or approved.quote is None or approved.quote.checkout is None:
            raise CheckoutError("The order has no approved total to book it by.")
        if order.delivery_date is None:
            raise CheckoutError("The order has no delivery date.")
        try:
            request = quotes.request_for(session, order)
        except quotes.NothingToQuote as error:
            raise CheckoutError(str(error)) from error
        build = shopping.newest_build(session, order.id)
        bought = products.items_to_buy(session, build.id) if build else []
        items: dict[str, list[ShoppingListItem]] = {}
        alternatives: dict[str, list[CartLine]] = {}
        names: dict[str, str] = {}
        for item in bought:
            if item.product is None:
                continue
            product_id = item.product.product.product_id
            names[product_id] = item.product.product.name
            items.setdefault(product_id, []).append(item)
            alternatives.setdefault(product_id, []).extend(
                CartLine(product_id=a.product.product_id, quantity=a.count)
                for a in item.alternatives
            )
            for alternative in item.alternatives:
                names.setdefault(alternative.product.product_id, alternative.product.name)
        return _ToBook(
            household_id=order.household_id,
            lines=request.lines,
            tip_percent=household.tip_percent,
            tip_max_cents=household.tip_max_cents,
            target=BookTarget(
                day=order.delivery_date,
                by=order.delivery_by or household.delivery_time,
                address=approved.quote.checkout.address,
                card=approved.quote.checkout.card,
            ),
            product_gone=household.product_gone,
            names=names,
            items=items,
            alternatives=alternatives,
        )


def process_book(job: Job, job_dir: Path) -> Booked:
    """Book an approved order: fill the household's cart with its products (a
    product that's gone swapped for an alternative, if the household says so),
    and have the agent choose the window, read checkout, and place it, in a
    browser signed in with the household's Instacart session. Place order is
    pressed only after the worker records the click (answer_clicks)."""
    to_book = _to_book(job)
    settings = get_settings()
    store = settings.instacart_store
    outcome: Booked | None = None
    try:
        with (
            agent_lock(job_id=str(job.id)),
            cart.filled(to_book.household_id, to_book.lines) as filled,
        ):
            outcome = _book_filled(job, job_dir, to_book, filled)
    except cart.CartNotPutBack:
        # Once it's ordered, the cart not being put back mustn't hide that (the
        # ledger stays, and the next run puts it back).
        if outcome is None or outcome.state not in (BookState.BOOKED, BookState.CHECK_INSTACART):
            raise
        log.error("booking.cart_not_put_back", extra={"job_id": str(job.id)})
    if outcome is None:
        raise AgentRunError(f"Booking at {store} didn't finish.")
    return outcome


def _book_filled(job: Job, job_dir: Path, to_book: _ToBook, filled: cart.FilledCart) -> Booked:
    store = get_settings().instacart_store
    swaps: dict[str, CartLine] = {}
    gone: list[CartLine] = []
    for line in filled.missing(to_book.lines):
        # Bought for more than one item, the alternatives' counts can't stand in
        # for it: someone chooses.
        use = (
            to_book.product_gone == ProductGone.USE_ALTERNATIVE
            and len(to_book.items.get(line.product_id, [])) == 1
        )
        alternative = (
            filled.swap(line, to_book.alternatives.get(line.product_id, [])) if use else None
        )
        if alternative is None:
            gone.append(line)
        else:
            swaps[line.product_id] = alternative
    if gone:
        names = [to_book.names.get(line.product_id, line.product_id) for line in gone]
        left = (
            " (none of its alternatives is left)"
            if to_book.product_gone == ProductGone.USE_ALTERNATIVE
            else ""
        )
        return Booked(
            state=BookState.NEEDS_PRODUCT,
            issue=BookIssue(
                message=f"{store} doesn't have {', '.join(names)} any more{left}.",
                item_ids=[
                    item.id for line in gone for item in to_book.items.get(line.product_id, [])
                ],
            ),
        )
    lines = [swaps.get(line.product_id, line) for line in to_book.lines]
    cart.check(filled.cart, lines, to_book.names)
    if filled.cart.below_minimum:
        return Booked(
            state=BookState.NOT_BOOKED,
            issue=BookIssue(message=f"{store} won't take it: {filled.cart.blocked_because}."),
        )
    if not filled.cart.can_check_out:
        raise CheckoutError(f"Instacart won't check this cart out: {filled.cart.blocked_because}")

    sessions = session_store()
    target = to_book.target
    prompt = prompts.fill(
        "checkout_book",
        store=store,
        slug=store.lower(),
        day=f"{target.day:%A, %b} {target.day.day}",
        by=windows.time_label(target.by),
    )
    with (
        _checkout(
            job_dir,
            sessions,
            tip_percent=to_book.tip_percent,
            tip_max_cents=to_book.tip_max_cents,
            cart_items=len(lines),
            book=target,
        ) as context,
        answering_clicks(job_dir, job.id),
    ):
        try:
            run_agent(job_dir, prompt, Tools.CHECKOUT, context, agent_locked=True)
        except AgentRunError as error:
            # Once Place order was pressed, what the tools recorded says how it went.
            if not load_checkout_state(job_dir).clicked:
                raise
            log.error(
                "booking.agent_failed_after_click",
                extra={"job_id": str(job.id), "error": str(error)},
            )
    return _outcome(job_dir, to_book, swaps)


def _outcome(job_dir: Path, to_book: _ToBook, swaps: dict[str, CartLine]) -> Booked:
    """How the booking went, from what the tools recorded: once Place order was
    pressed, it's booked only if Instacart confirmed it."""
    read = load_checkout_state(job_dir)
    swapped = {
        item.id: alternative.product_id
        for gone, alternative in swaps.items()
        for item in to_book.items.get(gone, [])
    }
    swap_names = [
        Swap(
            item=item.name,
            gone=to_book.names.get(gone, gone),
            bought=to_book.names.get(alternative.product_id, alternative.product_id),
        )
        for gone, alternative in swaps.items()
        for item in to_book.items.get(gone, [])[:1]
    ]
    if read.clicked:
        if read.confirmation is None:
            return Booked(
                state=BookState.CHECK_INSTACART,
                issue=BookIssue(
                    message="Place order was pressed, but Instacart didn't confirm it."
                ),
            )
        confirmed = read.confirmation
        return Booked(
            state=BookState.BOOKED,
            booking=Booking(
                order_number=confirmed.order_number,
                window=confirmed.window,
                total_cents=confirmed.total_cents,
                swaps=swap_names,
            ),
            swapped=swapped,
        )
    if read.click == ClickAnswer.DRY_RUN and read.checkout is not None:
        return Booked(
            state=BookState.BOOKED,
            booking=Booking(
                order_number=booking.DRY_RUN,
                window=read.window,
                total_cents=read.checkout.total_cents,
                swaps=swap_names,
            ),
            swapped=swapped,
        )
    if read.click == ClickAnswer.OFF:
        return Booked(state=BookState.NOT_BOOKED, issue=BookIssue(message=booking.BOOKING_OFF))
    if read.no_window:
        day = to_book.target.day
        return Booked(
            state=BookState.NEEDS_TIME,
            issue=BookIssue(
                message=f"No delivery window is left on {day:%a, %b} {day.day} for the time "
                "it needs."
            ),
        )
    if read.changed is not None:
        return Booked(
            state=BookState.NOT_BOOKED,
            issue=BookIssue(message=f"{read.changed} Check your Instacart account."),
        )
    if read.signed_out:
        return Booked(state=BookState.NOT_BOOKED, issue=BookIssue(message=SIGNED_OUT_ERROR))
    raise AgentRunError(f"The agent didn't book the order; see {job_dir}.")


@contextmanager
def answering_clicks(job_dir: Path, job_id: UUID) -> Iterator[None]:
    """While the agent books, answer its request to press Place order (the
    checkout tools wait for it): `go` once the click is recorded on the order
    (booking.claim_click), unless booking isn't live (a dry run, or turned off).
    No answer (this failed) means no click."""
    stop = threading.Event()
    request, answer = job_dir / CLICK_REQUEST_FILE, job_dir / CLICK_ANSWER_FILE

    def answer_once() -> None:
        while not request.exists():
            if stop.wait(0.25):
                return
        try:
            mode = get_settings().instacart_booking
            if mode == "live":
                with get_session() as session:
                    claimed = booking.claim_click(session, job_id)
                reply = ClickAnswer.GO if claimed else ClickAnswer.REFUSED
            else:
                reply = ClickAnswer.DRY_RUN if mode == "dry_run" else ClickAnswer.OFF
            replace_file(answer, ClickReply(answer=reply).model_dump_json())
            log.info("booking.click_answered", extra={"job_id": str(job_id), "answer": reply})
        except Exception:
            log.exception("booking.click_answer_failed", extra={"job_id": str(job_id)})

    thread = threading.Thread(target=answer_once, name=f"clicks-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


@contextmanager
def _checkout(
    job_dir: Path,
    sessions: SessionStore,
    *,
    tip_percent: int,
    tip_max_cents: int,
    cart_items: int,
    book: BookTarget | None = None,
) -> Iterator[CheckoutJob]:
    """The checkout tools' context for the job, with the household's session
    handed over to them for the block (read then, under the caller's locks: a
    job before this one may have renewed it), and the one they hand back saved
    after it."""
    settings = get_settings()
    # The session goes in a directory of its own for the tools: the job directory
    # is kept after the job (copied out, locally), the session never is.
    session_dir = settings.jobs_dir.parent / f"session-{job_dir.name}"
    context = CheckoutJob(
        site=settings.instacart_site,
        settle_check_ms=settings.checkout_settle_check_ms,
        store=settings.instacart_store,
        tip_percent=tip_percent,
        tip_max_cents=tip_max_cents,
        cart_items=cart_items,
        session_dir=session_dir,
        book=book,
    )
    try:
        _hand_over_session(session_dir, sessions.load())
        yield context
    finally:
        _take_back_session(job_dir, session_dir, sessions)


def _hand_over_session(session_dir: Path, state: dict[str, Any]) -> None:
    """Put the session where the checkout tools read it, for the agent's user only."""
    session_dir.mkdir(mode=0o700, parents=True)
    path = session_dir / SESSION_FILE
    path.write_text(json.dumps(state))
    path.chmod(0o600)
    uid = get_settings().agent_uid
    if uid is not None:
        for owned in (session_dir, path):
            os.chown(owned, uid, uid)


def _take_back_session(job_dir: Path, session_dir: Path, sessions: SessionStore) -> None:
    """Save the session the tools handed back with their report (Instacart renews
    it), and remove the session's directory."""
    renewed = session_dir / RENEWED_SESSION_FILE
    try:
        if not renewed.exists():
            return
        read = load_checkout_state(job_dir)
        if read.signed_out:
            return
        try:
            sessions.save(json.loads(renewed.read_text())["cookies"])
        except CheckoutError as error:
            # Once Place order was asked for, a session that can't be saved mustn't
            # hide how the booking went (the next checkout reads the one saved before).
            if read.click is None and not read.clicked:
                raise
            log.error(
                "instacart.session_not_saved", extra={"job_id": job_dir.name, "error": str(error)}
            )
            return
        log.info("instacart.session_saved", extra={"job_id": job_dir.name})
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


def generate_meals_prompt(session: Session, job: Job, count: int) -> str:
    """The agent's instructions for a batch of `count` meals for the job's
    household, which avoid meals like the ones it marked "don't show meals like
    this". Raises JobDiscarded if the job's draft is gone."""
    household_id = session.exec(
        select(GroceryOrder.household_id).where(GroceryOrder.id == job.grocery_order_id)
    ).first()
    if household_id is None:
        raise JobDiscarded(f"Job {job.id}'s draft was discarded.")
    avoid = ""
    if not_for_us := ratings.not_for_us_meals(session, household_id):
        listed = "\n".join(
            f"- {meal.name}: {meal.description}" if meal.description else f"- {meal.name}"
            for meal in not_for_us
        )
        avoid = (
            'The family marked these meals "don\'t show meals like this". '
            f"Don't suggest meals like them:\n\n{listed}\n\n"
        )
    return prompts.fill("generate_meals", count=count, avoid=avoid)
