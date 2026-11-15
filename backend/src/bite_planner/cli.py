"""`bite-planner` command line: the job worker, the agent's tools (`mcp`), and admin helpers."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from bite_planner_agent.cli import mcp_serve
from bite_planner_agent.instacart import InstacartError, Product, describe
from pydantic import ValidationError
from sqlmodel import Session

from bite_planner import (
    booking,
    checkout,
    harness,
    household,
    instacart,
    orders,
    push,
    reminders,
    worker,
)
from bite_planner import log as logging_config
from bite_planner.checkout import CheckoutError
from bite_planner.db import get_session
from bite_planner.dispatch import JOB_ID_ENV
from bite_planner.settings import get_settings, load_ssm_parameters

app = typer.Typer(no_args_is_help=True)
job_app = typer.Typer(no_args_is_help=True, help="Create and run jobs.")
household_app = typer.Typer(
    no_args_is_help=True, help="Manage households and who is in them (invite-only)."
)
instacart_app = typer.Typer(
    no_args_is_help=True,
    help="Check the household's Instacart store (read-only), or sign in to its website.",
)
app.add_typer(job_app, name="job")
app.add_typer(household_app, name="household")
app.add_typer(instacart_app, name="instacart")
app.command("mcp", hidden=True)(mcp_serve)
log = logging.getLogger(__name__)

HouseholdOption = Annotated[str, typer.Option("--household", help="The household's name.")]


@contextmanager
def household_session() -> Iterator[Session]:
    """A session for a household command; its errors end the command with their message."""
    with get_session() as session:
        try:
            yield session
        except household.HouseholdError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error


@app.callback()
def main(ctx: typer.Context) -> None:
    """Bite Planner backend."""
    # Secrets named in BITE_PLANNER_SSM_PARAMETERS, before settings are first read
    # (e.g. the APNs key for `household notify-test`).
    load_ssm_parameters()
    # The agent's tools (`mcp`) run in its sandbox, which can only write the job directory,
    # so they log to stderr only.
    log_file = (
        None if ctx.invoked_subcommand == "mcp" else get_settings().logs_dir / "bite-planner.jsonl"
    )
    logging_config.configure(log_file)


@job_app.command("run")
def job_run(
    job_id: Annotated[
        UUID, typer.Option(envvar=JOB_ID_ENV, help="The job to run (set per task in AWS).")
    ],
) -> None:
    """Run one job: claim it, run the agent, and save its meals. This is the worker."""
    try:
        worker.run_job(job_id)
    except worker.SchemaBehindError as error:
        log.error("schema.behind", extra={"error": str(error)})
        typer.echo(str(error), err=True)
        raise typer.Exit(worker.SCHEMA_BEHIND_EXIT_CODE) from error


@job_app.command("book-due")
def job_book_due() -> None:
    """Run the booking run now: start booking the approved orders that are due
    (in AWS, a schedule does this each hour from 9:00 AM to 9 PM store time)."""
    queued = reminders.book_due()
    typer.echo(f"Started booking {len(queued)} order(s).")


@app.command("recover-booking")
def recover_booking(
    order_id: UUID,
    household_name: HouseholdOption,
    receipt: Annotated[Path, typer.Option(help="Private JSON receipt verified against Instacart.")],
    check: Annotated[bool, typer.Option(help="Validate without changing any records.")] = False,
) -> None:
    """Recover an unconfirmed purchase's receipt; never place or retry an order.

    Check the original worker log first: cart.swapped requires shopping-list
    reconciliation and cannot be recovered with this command.
    """
    try:
        confirmed = booking.HistoricalReceipt.model_validate_json(receipt.read_text())
        with get_session() as session:
            house = household.get(session, household_name)
            restored = booking.recover_receipt(session, house.id, order_id, confirmed, check=check)
    except (
        OSError,
        UnicodeError,
        ValidationError,
        household.HouseholdError,
        booking.NotChangeable,
        orders.OrderNotFound,
        orders.OrderPlaced,
    ) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if not restored:
        typer.echo("Already recovered; no records changed.")
    elif check:
        typer.echo("Receipt matches the original purchase authorization; no records changed.")
    else:
        typer.echo("Restored the complete booking receipt; no order was placed or retried.")


@app.command("agent-login")
def agent_login(
    provider: Annotated[str, typer.Argument(help="codex (Instacart).")],
) -> None:
    """Connect Codex to the household's ChatGPT and Instacart accounts."""
    try:
        status = harness.login_agent(provider)
    except ValueError as error:  # an unknown provider
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    raise typer.Exit(status)


@household_app.command("create")
def household_create(name: str) -> None:
    """Create a household."""
    with household_session() as session:
        household.create(session, name)
    typer.echo(f"Created the household {name!r}.")


@household_app.command("list")
def household_list() -> None:
    """List households: name, member count, order count."""
    with household_session() as session:
        for name, member_count, job_count in household.households(session):
            typer.echo(f"{name}\t{member_count}\t{job_count}")


@household_app.command("invite")
def household_invite(
    email: str,
    name: Annotated[str, typer.Option(help="Name for their account.")],
    household_name: HouseholdOption,
) -> None:
    """Invite someone by email, as the app does: they join the household when they
    first sign in with it."""
    with household_session() as session:
        household.invite_by_name(session, household_name, email, name)
    typer.echo(
        f"Invited {email} to {household_name!r}. They join when they first sign in to the app "
        "with this email and the code Neon Auth emails them."
    )


@household_app.command("members")
def household_members(household_name: Annotated[str, typer.Argument(metavar="household")]) -> None:
    """List a household's members (email and name), then its invitations."""
    with household_session() as session:
        for person in household.members(session, household_name):
            typer.echo(f"{person.email}\t{person.name}")
        for invited in household.invites_of(session, household.get(session, household_name).id):
            typer.echo(f"{invited.email}\t{invited.name}\tinvited")


@household_app.command("remove")
def household_remove(email: str) -> None:
    """Remove someone from their household, as the app does (their account stays)."""
    with household_session() as session:
        household.remove(session, email)
    typer.echo(f"Removed {email} from their household.")


@household_app.command("notify-test")
def household_notify_test(email: str) -> None:
    """Send a test notification to this member's phones (checks push end to end).
    Sends through APNs with BITE_PLANNER_PUSH=apns and the key.
    See docs/operations/aws.md "Diagnostics".
    """
    with household_session() as session:
        user_id = household.member_id(session, email)
    push.notify(
        user_id,
        push.Notification(
            kind=push.NotificationKind.TEST,
            title="Test notification",
            body="Bite Planner can reach this phone.",
            expires_in=timedelta(minutes=10),
        ),
    )
    typer.echo(f"Sent to {email}'s phones; see the log for each one.")


def _product_line(product: Product, quantity: float | None = None) -> str:
    return f"  {f'{quantity:g} x ' if quantity is not None else ''}{describe(product)}"


@instacart_app.command("search")
def instacart_search(query: str) -> None:
    """Search the store, as the agent's search tool does."""
    try:
        with instacart.store() as store:
            results = store.search(query)
    except InstacartError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"Matches ({len(results.matches)}):")
    for product in results.matches:
        typer.echo(_product_line(product))
    typer.echo(f"Related ({len(results.related)}):")
    for product in results.related:
        typer.echo(_product_line(product))


@instacart_app.command("sign-in")
def instacart_sign_in() -> None:
    """Sign Bite Planner in to Instacart's website for checkout (a browser opens on
    this machine; the session is saved to SSM). `make instacart-login` runs it."""
    try:
        checkout.sign_in(get_settings().instacart_store)
    except CheckoutError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo("Signed in: checkout will use this session.")


@instacart_app.command("cart")
def instacart_cart() -> None:
    """Show the store's cart."""
    try:
        with instacart.store() as store:
            cart = store.cart()
    except InstacartError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    subtotal = f"${cart.subtotal_cents / 100:.2f}" if cart.subtotal_cents is not None else "unknown"
    typer.echo(f"{len(cart.items)} items, subtotal {subtotal}.")
    for item in cart.items:
        typer.echo(_product_line(item.product, item.quantity))
    typer.echo(
        "Checkout allowed." if cart.can_check_out else f"Checkout blocked: {cart.blocked_because}"
    )
