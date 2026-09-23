"""`castle-chef` command line: the agent's meal tool, dev helpers, and the worker."""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from castle_chef import family, jobs, worker
from castle_chef import log as logging_config
from castle_chef.client_logs import save_client_logs
from castle_chef.db import get_session
from castle_chef.meals import MealInput, save_meal
from castle_chef.settings import get_settings

app = typer.Typer(no_args_is_help=True)
meal_app = typer.Typer(no_args_is_help=True, help="Manage meals.")
job_app = typer.Typer(no_args_is_help=True, help="Manage queued jobs.")
worker_app = typer.Typer(no_args_is_help=True, help="Process queued jobs.")
family_app = typer.Typer(no_args_is_help=True, help="Manage who can use the app (invite-only).")
logs_app = typer.Typer(no_args_is_help=True, help="Logs sent from the app.")
app.add_typer(meal_app, name="meal")
app.add_typer(job_app, name="job")
app.add_typer(worker_app, name="worker")
app.add_typer(family_app, name="family")
app.add_typer(logs_app, name="logs")
log = logging.getLogger(__name__)


@app.callback()
def main(ctx: typer.Context) -> None:
    """Castle Chef backend."""
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


@job_app.command("create")
def job_create(count: Annotated[int, typer.Option(min=1, max=14)]) -> None:
    """Queue a meal generation job, as the app does. Prints the job id."""
    params = jobs.GenerateMealsParams(count=count).model_dump()
    with get_session() as session:
        job = jobs.create_job(session, jobs.GENERATE_MEALS, params)
    typer.echo(json.dumps({"job_id": str(job.id)}))


@worker_app.command("run")
def worker_run(
    once: Annotated[
        bool, typer.Option(help="Process all pending jobs now, then exit (manual trigger).")
    ] = False,
) -> None:
    """Poll the job queue and run the agent for each job."""
    worker.run(once=once)


@family_app.command("invite")
def family_invite(
    email: str, name: Annotated[str, typer.Option(help="Name for their account.")]
) -> None:
    """Invite someone: create their account and add them to the household."""
    with get_session() as session:
        family.invite(session, email, name)
    typer.echo(
        f"Invited {email}. They sign in to the app with this email and the code Neon Auth "
        "emails them."
    )


@family_app.command("list")
def family_list() -> None:
    """List household members."""
    with get_session() as session:
        for email, name in family.members(session):
            typer.echo(f"{email}\t{name}")


@family_app.command("remove")
def family_remove(email: str) -> None:
    """Remove someone from the household and delete their account."""
    with get_session() as session:
        family.remove(session, email)
    typer.echo(f"Removed {email}.")


@logs_app.command("pull")
def logs_pull() -> None:
    """Save logs sent from the app to disk now (the worker also does this every cycle)."""
    settings = get_settings()
    with get_session() as session:
        saved = save_client_logs(session, settings.logs_dir)
    log.info("client_logs.pulled", extra={"batches": saved})
    typer.echo(f"Saved {saved} log batch(es) to {settings.logs_dir / 'client'}.")
