"""`castle-chef` command line: the agent's meal tool, the job worker, and admin helpers."""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pydantic import ValidationError
from sqlmodel import Session

from castle_chef import household, push, worker
from castle_chef import log as logging_config
from castle_chef.db import get_session
from castle_chef.dispatch import JOB_ID_ENV
from castle_chef.meals import MealInput, save_meal
from castle_chef.settings import get_settings, load_ssm_parameters

app = typer.Typer(no_args_is_help=True)
meal_app = typer.Typer(no_args_is_help=True, help="Manage meals.")
job_app = typer.Typer(no_args_is_help=True, help="Create and run jobs.")
household_app = typer.Typer(
    no_args_is_help=True, help="Manage households and who is in them (invite-only)."
)
app.add_typer(meal_app, name="meal")
app.add_typer(job_app, name="job")
app.add_typer(household_app, name="household")
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
    """Castle Chef backend."""
    # Secrets named in CASTLE_CHEF_SSM_PARAMETERS, before settings are first read
    # (e.g. the APNs key for `household notify-test`).
    load_ssm_parameters()
    # `meal add` runs inside the agent's sandbox, which can only write the job
    # directory, so it logs to stderr only (the agent sees it).
    log_file = (
        None if ctx.invoked_subcommand == "meal" else get_settings().logs_dir / "castle-chef.jsonl"
    )
    logging_config.configure(log_file)


@meal_app.command("add")
def meal_add(
    job_dir: Annotated[
        Path,
        typer.Option(
            envvar="CASTLE_CHEF_JOB_DIR",
            exists=True,
            file_okay=False,
            help="Job directory the worker created.",
        ),
    ],
) -> None:
    """Add one meal, read as JSON from stdin. The worker stores it after the agent finishes."""
    try:
        meal_input = MealInput.model_validate_json(sys.stdin.read())
    except ValidationError as error:
        typer.echo(f"Invalid meal JSON:\n{error}", err=True)
        raise typer.Exit(1) from error
    path = save_meal(job_dir, meal_input)
    typer.echo(json.dumps({"saved": path.name, "meal": meal_input.name}))


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


@app.command("agent-login")
def agent_login(provider: Annotated[str, typer.Argument(help="Agent provider.")] = "grok") -> None:
    """Sign the worker's agent in to its subscription (device code). In AWS this
    runs as a one-off worker task with the command `agent-login grok`."""
    raise typer.Exit(worker.login_agent(provider))


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
    """Invite someone: create their account if needed and add them to the household."""
    with household_session() as session:
        household.invite(session, email, name, household_name)
    typer.echo(
        f"Invited {email} to {household_name!r}. They sign in to the app with this email and "
        "the code Neon Auth emails them."
    )


@household_app.command("members")
def household_members(household_name: Annotated[str, typer.Argument(metavar="household")]) -> None:
    """List a household's members: email and name."""
    with household_session() as session:
        for person in household.members(session, household_name):
            typer.echo(f"{person.email}\t{person.name}")


@household_app.command("remove")
def household_remove(email: str) -> None:
    """Remove someone from their household and delete their account."""
    with household_session() as session:
        household.remove(session, email)
    typer.echo(f"Removed {email}.")


@household_app.command("notify-test")
def household_notify_test(email: str) -> None:
    """Send a test notification to this member's phones (checks push end to end).
    Sends through APNs with CASTLE_CHEF_PUSH=apns and the key (see README "Notifications")."""
    with household_session() as session:
        user_id = household.member_id(session, email)
    push.notify(
        user_id,
        push.Notification(
            kind=push.NotificationKind.TEST,
            title="Test notification",
            body="Castle Chef can reach this phone.",
            expires_in=timedelta(minutes=10),
        ),
    )
    typer.echo(f"Sent to {email}'s phones; see the log for each one.")
