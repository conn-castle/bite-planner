"""`castle-chef` command line: the agent's meal tool, the job worker, and admin helpers."""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pydantic import ValidationError

from castle_chef import family, jobs, worker
from castle_chef import log as logging_config
from castle_chef.db import get_session
from castle_chef.dispatch import JOB_ID_ENV, get_dispatcher
from castle_chef.meals import MealInput, save_meal
from castle_chef.settings import get_settings

app = typer.Typer(no_args_is_help=True)
meal_app = typer.Typer(no_args_is_help=True, help="Manage meals.")
job_app = typer.Typer(no_args_is_help=True, help="Create and run jobs.")
family_app = typer.Typer(no_args_is_help=True, help="Manage who can use the app (invite-only).")
app.add_typer(meal_app, name="meal")
app.add_typer(job_app, name="job")
app.add_typer(family_app, name="family")
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
    """Create a meal generation job and start it (SQS or a local worker, per
    CASTLE_CHEF_JOB_DISPATCH). Prints the job id."""
    params = jobs.GenerateMealsParams(count=count).model_dump()
    with get_session() as session:
        job = jobs.create_job(session, jobs.GENERATE_MEALS, params)
    get_dispatcher().dispatch(job.id)
    typer.echo(json.dumps({"job_id": str(job.id)}))


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
    except LookupError as error:
        log.error("job.not_found", extra={"job_id": str(job_id)})
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


@app.command("agent-login")
def agent_login(provider: Annotated[str, typer.Argument(help="Agent provider.")] = "grok") -> None:
    """Sign the worker's agent in to its subscription (device code). In AWS this
    runs as a one-off worker task with the command `agent-login grok`."""
    raise typer.Exit(worker.login_agent(provider))


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
