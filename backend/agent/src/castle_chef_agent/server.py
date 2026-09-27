"""The agent's tools as an MCP server (stdio), which the agent's harness starts for
the job: the job's tools are all the agent gets. Each tool is the matching
`castle-chef` command's function, so the checks are the same."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from castle_chef_agent import shopping
from castle_chef_agent.jobfiles import read_context
from castle_chef_agent.meals import MealInput, MealRefused, MealsJob, meal_problems, save_meal
from castle_chef_agent.shopping import Aisle, Staple


class Tools(StrEnum):
    """Which job's tools a server offers."""

    MEALS = "meals"
    SHOPPING_LIST = "shopping-list"


Lines = Annotated[
    list[str],
    Field(
        min_length=1, description="The ids of the ingredient lines it covers, e.g. ['1a', '3c']."
    ),
]


def meals_server(job_dir: Path) -> MCPServer:
    server = MCPServer("castle-chef")

    @server.tool()
    def add_meal(meal: MealInput) -> str:
        """Add one meal."""
        try:
            save_meal(job_dir, meal)
        except MealRefused as error:
            raise ToolError(str(error)) from error
        return f'Added "{meal.name}".'

    @server.tool()
    def done() -> str:
        """Check that every meal is added. Says what's missing, or that it's complete."""
        if problems := meal_problems(job_dir, read_context(job_dir, MealsJob).count):
            raise ToolError(" ".join(problems))
        return "Complete."

    return server


def shopping_list_server(job_dir: Path) -> MCPServer:
    server = MCPServer("castle-chef")

    @server.tool()
    def add_item(
        name: Annotated[
            str,
            Field(
                description="What to buy, as a shopper looks for it (no amount, unit, or "
                "preparation)."
            ),
        ],
        aisle: Annotated[Aisle, Field(description="Where it is in the store.")],
        lines: Lines,
        quantity: Annotated[
            float | None,
            Field(
                description="The total its lines need, in `unit`, or a count without `unit` "
                "(rounded up to whole ones). Leave it out only when none of its lines gives an "
                "amount."
            ),
        ] = None,
        unit: Annotated[
            str | None,
            Field(
                description="The quantity's unit, e.g. lb, oz, cup, tbsp. Leave it out for a count."
            ),
        ] = None,
    ) -> str:
        """Add an item to buy, covering the ingredient lines it's bought for."""
        try:
            item = shopping.ListItemInput(
                name=name, quantity=quantity, unit=unit, aisle=aisle, lines=lines
            )
            saved = shopping.add_item(job_dir, item)
        except (ValidationError, shopping.ListRefused) as error:
            raise ToolError(str(error)) from error
        amount = (
            " ".join([f"{saved.quantity:g}", *filter(None, [saved.unit])])
            if saved.quantity is not None
            else "no amount"
        )
        return f'Added "{saved.name}": {amount}, {saved.aisle.value}.'

    @server.tool()
    def add_staple(
        staple: Annotated[Staple, Field(description="The staple.")], lines: Lines
    ) -> str:
        """Add a staple the meals use (never bought), covering the ingredient lines that use it."""
        try:
            shopping.add_staple(job_dir, shopping.StapleInput(staple=staple, lines=lines))
        except (ValidationError, shopping.ListRefused) as error:
            raise ToolError(str(error)) from error
        return f"Added the staple {staple.value}."

    @server.tool()
    def remove(
        name: Annotated[str, Field(description="The item's or staple's name.")],
    ) -> str:
        """Take an item or staple off the list, e.g. to add it again differently."""
        try:
            shopping.remove(job_dir, name)
        except shopping.ListRefused as error:
            raise ToolError(str(error)) from error
        return f'Removed "{name}".'

    @server.tool()
    def done() -> str:
        """Check that the list is complete: every ingredient line in exactly one item or
        staple, and something to buy. Says what's wrong, or that it's complete."""
        job = read_context(job_dir, shopping.ListJob)
        draft = shopping.load_draft(job_dir)
        if problems := shopping.list_problems(job, draft):
            raise ToolError(" ".join(problems))
        return (
            f"Complete: {len(draft.items)} items and {len(draft.staples)} staples cover all "
            f"{len(job.lines)} lines."
        )

    return server


def serve(tools: Tools, job_dir: Path) -> None:
    """Serve the job's tools over stdio until the harness closes it."""
    server = meals_server(job_dir) if tools == Tools.MEALS else shopping_list_server(job_dir)
    server.run("stdio")
