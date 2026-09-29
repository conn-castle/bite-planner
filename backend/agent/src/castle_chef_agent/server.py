"""The agent's tools as an MCP server (stdio), which the agent's harness starts for
the job: the job's tools are all the agent gets. Each tool is the matching
`castle-chef` command's function, so the checks are the same."""

import shlex
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from castle_chef_agent import products, shopping
from castle_chef_agent.instacart import Instacart, InstacartError, connect, describe
from castle_chef_agent.jobfiles import read_context
from castle_chef_agent.meals import MealInput, MealRefused, MealsJob, meal_problems, save_meal
from castle_chef_agent.shopping import Aisle, Staple


class Tools(StrEnum):
    """Which job's tools a server offers."""

    MEALS = "meals"
    SHOPPING_LIST = "shopping-list"
    PRODUCTS = "products"


class InstacartSettings(BaseModel):
    """How the products server reaches Instacart (the worker passes its settings)."""

    codex_home: Path
    lock_file: Path | None
    command: str
    store: str


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


class Alternative(BaseModel):
    product_id: str = Field(description="A product id from a search.")
    count: int = Field(ge=1, description="How many.")


Item = Annotated[int, Field(description="The item's number on the shopping list.")]
Alternatives = Annotated[
    list[Alternative],
    Field(
        max_length=products.MAX_ALTERNATIVES,
        description=f"Up to {products.MAX_ALTERNATIVES} products from searches the family "
        "could choose instead, each with how many.",
    ),
]


def _pairs(alternatives: list[Alternative]) -> list[tuple[str, int]]:
    return [(alternative.product_id, alternative.count) for alternative in alternatives]


def products_server(job_dir: Path, instacart: Instacart) -> MCPServer:
    server = MCPServer("castle-chef")

    @server.tool()
    def search(
        item: Item, query: Annotated[str, Field(min_length=1, description="What to look for.")]
    ) -> str:
        """Search the store on Instacart for list item `item` (its product and
        alternatives come from its searches). Returns up to 10 products, each with its
        id, name, size, price (per pound for weighed items, with the package's
        estimated weight and price), and whether the family has bought it before."""
        try:
            results = instacart.search(query)
            products.record_search(job_dir, item, [*results.matches, *results.related])
        except (InstacartError, products.ChoiceRefused) as error:
            raise ToolError(str(error)) from error
        lines = [f"Matches ({len(results.matches)}):", *map(describe, results.matches)]
        if not results.matches:
            lines.append("(none: the store may not carry it)")
        lines += [f"Related ({len(results.related)}):", *map(describe, results.related)]
        return "\n".join(lines)

    @server.tool()
    def choose(
        item: Item,
        product_id: Annotated[str, Field(description="The product's id, from a search.")],
        count: Annotated[int, Field(ge=1, description="How many to buy.")],
        alternatives: Alternatives = [],  # noqa: B006 (a tool's default, never mutated)
        why_none: Annotated[
            str | None, Field(description="Why there are no alternatives, if there are none.")
        ] = None,
    ) -> str:
        """Choose a product from a search, and how many, for list item `item`, with up
        to eight alternatives from searches (each with its count). With no
        alternatives, say why in `why_none`. Replaces an earlier choice. Refused: a
        product that didn't come from a search, a count below 1, too little to cover
        the item's amount (when the units compare), or no alternatives and no reason."""
        try:
            choice = products.choose(
                job_dir, item, product_id, count, _pairs(alternatives), why_none
            )
        except products.ChoiceRefused as error:
            raise ToolError(str(error)) from error
        assert choice.chosen is not None
        return (
            f"Item {item}: {choice.chosen.count} x {choice.chosen.product.name}, "
            f"{len(choice.alternatives)} alternatives."
        )

    @server.tool()
    def not_found(
        item: Item,
        alternatives: Alternatives = [],  # noqa: B006 (a tool's default, never mutated)
        why_skippable: Annotated[
            str | None, Field(description="Why the meals work without it.")
        ] = None,
    ) -> str:
        """Mark a list item the store doesn't have. Give alternatives from searches
        and/or why the meal works without it. Refused with neither."""
        try:
            choice = products.not_found(job_dir, item, _pairs(alternatives), why_skippable)
        except products.ChoiceRefused as error:
            raise ToolError(str(error)) from error
        return f"Item {item}: not found, {len(choice.alternatives)} alternatives."

    @server.tool()
    def done() -> str:
        """Check that every item has a product or is marked not found; lists the
        ones left."""
        job = read_context(job_dir, products.ShopJob)
        if problems := products.problems(job, products.load_state(job_dir)):
            raise ToolError(" ".join(problems))
        return f"Complete: all {len(job.items)} items are decided."

    return server


def serve(tools: Tools, job_dir: Path, instacart: InstacartSettings | None = None) -> None:
    """Serve the job's tools over stdio until the harness closes it."""
    if tools == Tools.PRODUCTS:
        if instacart is None:
            raise ValueError("The products tools need Instacart's settings.")
        # One Instacart session (and the Codex login's lock) for the server's life.
        with connect(
            instacart.codex_home,
            instacart.store,
            lock_file=instacart.lock_file,
            command=shlex.split(instacart.command),
        ) as store:
            products_server(job_dir, store).run("stdio")
        return
    server = meals_server(job_dir) if tools == Tools.MEALS else shopping_list_server(job_dir)
    server.run("stdio")
