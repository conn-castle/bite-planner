"""The agent's commands. The backend's `castle-chef` mounts the same groups; in the
worker image the agent's `castle-chef` is this app alone."""

import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from castle_chef_agent import shopping
from castle_chef_agent.jobfiles import read_context
from castle_chef_agent.meals import MealInput, MealRefused, MealsJob, meal_problems, save_meal
from castle_chef_agent.server import Tools, serve
from castle_chef_agent.shopping import Aisle

app = typer.Typer(no_args_is_help=True)
meal_app = typer.Typer(no_args_is_help=True, help="Add the meals you make.")
shopping_list_app = typer.Typer(
    no_args_is_help=True,
    help="Make the shopping list: add the items to buy and the staples the meals use.",
)
app.add_typer(meal_app, name="meal")
app.add_typer(shopping_list_app, name="shopping-list")

JobDirOption = Annotated[
    Path,
    typer.Option(
        envvar="CASTLE_CHEF_JOB_DIR",
        exists=True,
        file_okay=False,
        # Set by the worker; the agent's help shows only what it gives.
        hidden=True,
    ),
]
LinesOption = Annotated[
    list[str],
    typer.Option(
        "--line",
        help="An ingredient line it covers, by its id (e.g. 2c). Repeat it for each line.",
    ),
]


def refuse(message: str) -> typer.Exit:
    """End an agent's command with the reason on stderr."""
    typer.echo(message, err=True)
    return typer.Exit(1)


@meal_app.command("add")
def meal_add(job_dir: JobDirOption) -> None:
    """Add one meal, read as JSON from stdin. The worker stores it after the agent finishes."""
    try:
        meal_input = MealInput.model_validate_json(sys.stdin.read())
    except ValidationError as error:
        raise refuse(f"Invalid meal JSON:\n{error}") from error
    try:
        save_meal(job_dir, meal_input)
    except MealRefused as error:
        raise refuse(str(error)) from error
    typer.echo(f'Added "{meal_input.name}".')


@meal_app.command("done")
def meal_done(job_dir: JobDirOption) -> None:
    """Check that every meal is added. Says what's missing, or that it's complete."""
    if problems := meal_problems(job_dir, read_context(job_dir, MealsJob).count):
        raise refuse(" ".join(problems))
    typer.echo("Complete.")


@shopping_list_app.command("add")
def shopping_list_add(
    job_dir: JobDirOption,
    name: Annotated[
        str,
        typer.Option(
            help="What to buy, as a shopper looks for it (no amount, unit, or preparation)."
        ),
    ],
    aisle: Annotated[Aisle, typer.Option(help="Where it is in the store.")],
    lines: LinesOption,
    quantity: Annotated[
        float | None,
        typer.Option(
            help="The total its lines need, in --unit, or a count without --unit (rounded up "
            "to whole ones). Leave it out only when none of its lines gives an amount."
        ),
    ] = None,
    unit: Annotated[
        str | None,
        typer.Option(help="The quantity's unit, e.g. lb, oz, cup, tbsp. Leave it out for a count."),
    ] = None,
) -> None:
    """Add an item to buy, covering the ingredient lines it's bought for."""
    try:
        item = shopping.ListItemInput(
            name=name, quantity=quantity, unit=unit, aisle=aisle, lines=lines
        )
    except ValidationError as error:
        raise refuse(f"Invalid item:\n{error}") from error
    try:
        saved = shopping.add_item(job_dir, item)
    except shopping.ListRefused as error:
        raise refuse(str(error)) from error
    amount = " ".join(
        [f"{saved.quantity:g}", *filter(None, [saved.unit])]
        if saved.quantity is not None
        else ["no amount"]
    )
    typer.echo(f'Added "{saved.name}": {amount}, {saved.aisle.value}.')


@shopping_list_app.command("staple")
def shopping_list_staple(
    job_dir: JobDirOption,
    staple: Annotated[shopping.Staple, typer.Argument(help="The staple.")],
    lines: LinesOption,
) -> None:
    """Add a staple the meals use (never bought), covering the ingredient lines that use it."""
    try:
        shopping.add_staple(job_dir, shopping.StapleInput(staple=staple, lines=lines))
    except (ValidationError, shopping.ListRefused) as error:
        raise refuse(str(error)) from error
    typer.echo(f"Added the staple {staple.value}.")


@shopping_list_app.command("remove")
def shopping_list_remove(
    job_dir: JobDirOption,
    name: Annotated[str, typer.Argument(help="The item's or staple's name.")],
) -> None:
    """Take an item or staple off the list, e.g. to add it again differently."""
    try:
        shopping.remove(job_dir, name)
    except shopping.ListRefused as error:
        raise refuse(str(error)) from error
    typer.echo(f'Removed "{name}".')


@shopping_list_app.command("done")
def shopping_list_done(job_dir: JobDirOption) -> None:
    """Check that the list is complete: every ingredient line in exactly one item or
    staple. Says what's wrong, or that it's complete."""
    job = read_context(job_dir, shopping.ListJob)
    draft = shopping.load_draft(job_dir)
    if problems := shopping.list_problems(job, draft):
        raise refuse(" ".join(problems))
    typer.echo(
        f"Complete: {len(draft.items)} items and {len(draft.staples)} staples cover all "
        f"{len(job.lines)} lines."
    )


@app.command("mcp", hidden=True)
def mcp_serve(
    tools: Annotated[Tools, typer.Argument(help="Which job's tools to offer.")],
    job_dir: JobDirOption,
) -> None:
    """Serve the job's tools to the agent's harness over MCP (stdio)."""
    serve(tools, job_dir)
