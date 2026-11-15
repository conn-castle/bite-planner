"""The agent's tools as an MCP server (stdio), which the agent's harness starts for
the job: the job's tools are all the agent gets. Each tool's checks are in the
module for its job (meals, shopping, products, checkout)."""

import shlex
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, assert_never

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from bite_planner_agent import checkout, meal_edit, products, shopping
from bite_planner_agent.instacart import Instacart, InstacartError, connect, describe
from bite_planner_agent.jobfiles import locked, read_context
from bite_planner_agent.meals import (
    MealInput,
    MealRefused,
    MealsJob,
    batch_problems,
    check_titles,
    load_meals,
    save_meal,
)
from bite_planner_agent.shopping import Aisle, Staple
from bite_planner_agent.title_model import TitleModelError


class Tools(StrEnum):
    """Which job's tools a server offers."""

    MEALS = "meals"
    MEAL_EDIT = "meal-edit"
    SHOPPING_LIST = "shopping-list"
    PRODUCTS = "products"
    CHECKOUT = "checkout"


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
    server = MCPServer("bite-planner")

    @server.tool()
    def check_meal_titles(names: Annotated[list[str], Field(min_length=1, max_length=100)]) -> str:
        """Check proposed names together before writing recipes. Reserves nothing."""
        try:
            problems = check_titles(job_dir, names)
        except TitleModelError as error:
            raise ToolError(str(error)) from error
        return "\n".join(problems) if problems else "Names are distinct. add_meal will recheck."

    @server.tool()
    def add_meal(meal: MealInput) -> str:
        """Add one meal."""
        try:
            save_meal(job_dir, meal)
        except (MealRefused, TitleModelError) as error:
            raise ToolError(str(error)) from error
        return f'Added "{meal.name}".'

    @server.tool()
    def done() -> str:
        """Check that every meal is added. Says what's missing, or that it's complete."""
        try:
            with locked(job_dir):
                problems = batch_problems(read_context(job_dir, MealsJob), load_meals(job_dir))
        except TitleModelError as error:
            raise ToolError(str(error)) from error
        if problems:
            raise ToolError(" ".join(problems))
        return "Complete."

    return server


def meal_edit_server(job_dir: Path) -> MCPServer:
    server = MCPServer("bite-planner")

    @server.tool()
    def propose_recipe(recipe: meal_edit.ProposedRecipe) -> str:
        """Propose the complete two-serving recipe. Replaces any earlier outcome."""
        try:
            meal_edit.propose(job_dir, recipe)
        except (meal_edit.EditRefused, ValidationError) as error:
            raise ToolError(str(error)) from error
        return "Proposal saved for review. Call done when finished."

    @server.tool()
    def cannot_change(reason: Annotated[str, Field(min_length=1, max_length=300)]) -> str:
        """Explain why this request cannot produce a usable recipe. Replaces any proposal."""
        try:
            meal_edit.cannot_change(job_dir, reason)
        except ValidationError as error:
            raise ToolError(str(error)) from error
        return "Reason saved. Call done when finished."

    @server.tool()
    def done() -> str:
        """Finish the last proposal or refusal; requires an outcome."""
        try:
            meal_edit.done(job_dir)
        except meal_edit.EditRefused as error:
            raise ToolError(str(error)) from error
        return "Complete."

    return server


def shopping_list_server(job_dir: Path) -> MCPServer:
    server = MCPServer("bite-planner")

    @server.tool()
    def adapt_recipe(
        meal: Annotated[int, Field(description="The meal number.")],
        ingredients: list[shopping.RecipeAmounts],
        steps: list[str],
    ) -> str:
        """Confirm complete ordered ingredient amounts and matching step texts.
        Keep original names, units, preparation, step order, methods, and divided uses.
        """
        try:
            lines = shopping.adapt_recipe(
                job_dir,
                shopping.RecipeAdaptation(
                    meal=meal,
                    ingredients=ingredients,
                    steps=steps,
                ),
            )
        except (ValidationError, shopping.ListRefused) as error:
            raise ToolError(str(error)) from error
        return "Confirmed:\n" + "\n".join(f"- {line.id}: {line.text}" for line in lines)

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
        more_alternatives_query: Annotated[
            shopping.AlternativesQuery,
            Field(
                description="One broader product search for More alternatives, e.g. "
                "'sausage' for chicken sausage. Keep the product category, remove optional "
                "variety or brand restrictions; never combine queries."
            ),
        ],
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
                name=name,
                quantity=quantity,
                unit=unit,
                aisle=aisle,
                lines=lines,
                more_alternatives_query=more_alternatives_query,
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
    server = MCPServer("bite-planner")

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


Ref = Annotated[str, Field(description="An element's ref from the page, e.g. e12.")]


def checkout_server(job_dir: Path) -> MCPServer:
    """The browser tools: the browser starts with the server and closes with it.
    A booking gets the tools to choose the window and place the order; a quote,
    the one to record the cart's out-of-stock settings."""
    opened: list[checkout.Browser] = []
    booking = read_context(job_dir, checkout.CheckoutJob).book is not None

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        async with checkout.browser(job_dir) as browser:
            opened.append(browser)
            yield

    server = MCPServer("bite-planner", lifespan=lifespan)

    async def act[T](action: Awaitable[T]) -> T:
        """Run a browser action; what went wrong goes back to the agent to handle."""
        from playwright.async_api import Error as PlaywrightError

        try:
            return await action
        except checkout.CheckoutRefused as error:
            raise ToolError(str(error)) from error
        except PlaywrightError as error:
            first = str(error).splitlines()[0] if str(error) else type(error).__name__
            raise ToolError(f"The browser couldn't do that: {first}") from error

    @server.tool()
    async def look(
        ref: Annotated[
            str | None,
            Field(description="Just this element and what's in it; the whole page if left out."),
        ] = None,
    ) -> str:
        """Show what's on screen now: the page's path, and the elements (role, name,
        and ref) of the dialog that's open, or else of the page."""
        return await act(opened[0].snapshot(ref))

    @server.tool()
    async def find(
        text: Annotated[
            str, Field(min_length=2, description="Text the element shows or is named.")
        ],
    ) -> str:
        """Find elements anywhere on the page (dialogs and all) by their text: each
        matching element's line, with its ref."""
        return await act(opened[0].find(text))

    @server.tool()
    async def open_page(
        path: Annotated[
            str, Field(description="A path on Instacart's site, e.g. /store/wegmans/storefront.")
        ],
    ) -> str:
        """Go to a page on Instacart's site. Returns the page."""
        return await act(opened[0].open(path))

    @server.tool()
    async def click(ref: Ref) -> str:
        """Click an element (never Place order: refused). Returns the page after."""
        return await act(opened[0].click(ref))

    @server.tool()
    async def type_text(ref: Ref, text: Annotated[str, Field(description="What to type.")]) -> str:
        """Replace a text field's contents with `text`. Returns the page after."""
        return await act(opened[0].type(ref, text))

    @server.tool()
    async def tip(
        items: Annotated[str, Field(description="The ref of the items' total line.")],
    ) -> str:
        """The tip to set, by the household's rule, from the items' total shown there."""

        async def amount() -> str:
            job = opened[0].job
            items_cents = checkout.money(await opened[0].element_text(items))
            cents = checkout.tip_cents(items_cents, job.tip_percent, job.tip_max_cents)
            return f"Set the tip to {checkout.cents_text(cents)} (type {cents / 100:.2f})."

        return await act(amount())

    if not booking:

        @server.tool()
        async def record_replacements(
            refs: Annotated[
                list[Ref],
                Field(
                    min_length=1,
                    description="The ref of each cart item's out-of-stock (replacement) setting.",
                ),
            ],
        ) -> str:
            """Record what Instacart does when each cart item is out of stock, from the cart."""
            settings = await act(checkout.record_replacements(job_dir, opened[0], refs))
            return f"Recorded: {', '.join(settings)}."

    @server.tool()
    async def record_day(
        day: Annotated[str, Field(description="The ref of a delivery-day tab, selected.")],
        windows: Annotated[
            list[Ref], Field(description="Every enabled window on this day; [] if none.")
        ],
    ) -> str:
        """Read actual available windows. Visit and record every day in the schedule's tablist."""
        found = await act(checkout.record_day(job_dir, opened[0], day, windows))
        return f"Recorded {len(found)} available windows. Record the other days too."

    @server.tool()
    async def report(
        items: Annotated[str, Field(description="The ref of the items' total line.")],
        lines: Annotated[
            list[Ref],
            Field(
                description="The refs of every other summary line (delivery, fees, taxes, "
                "...), not the tip or total."
            ),
        ],
        tip: Annotated[str, Field(description="The ref of the element showing the tip amount.")],
        total: Annotated[str, Field(description="The ref of the total line.")],
        delivery: Annotated[
            str,
            Field(description="The ref of the delivery option chosen (its day and time window)."),
        ],
        address: Annotated[str, Field(description="The ref of the delivery address.")],
        card: Annotated[str, Field(description="The ref of the default payment card.")],
    ) -> str:
        """Report checkout: each part is read from the element you point at (each a
        different one), and checked: it must add up, the tip must be the rule's, and
        the delivery must be a scheduled day and window. Says what's wrong, or done."""
        refs = checkout.ReportRefs(items, lines, tip, total, delivery, address, card)
        read = await act(checkout.report(job_dir, opened[0], refs))
        return f"Done: total {checkout.cents_text(read.total_cents)}."

    @server.tool()
    async def signed_out(
        ref: Annotated[str, Field(description="The ref of the sign-in control Instacart shows.")],
    ) -> str:
        """Instacart asks to sign in: record it and stop (someone signs it in again)."""
        await act(checkout.mark_signed_out(job_dir, opened[0], ref))
        return "Recorded: signed out. Stop here."

    if booking:

        @server.tool()
        async def propose_delivery() -> str:
            """After reading every day, record the next available delivery for member approval.
            Never orders. Only before any purchase request, when the approved time is unavailable.
            """
            return await act(checkout.propose_delivery(job_dir, opened[0]))

        @server.tool()
        async def choose_window(
            day: Annotated[str, Field(description="The ref of the delivery day's tab, selected.")],
            windows: Annotated[
                list[Ref],
                Field(
                    description="The ref of each enabled time window offered that day; [] if none."
                ),
            ],
        ) -> str:
            """Choose which of the day's delivery windows to book (by the household's
            rule), or find that none works. Says which to click, or to stop."""
            return await act(checkout.choose_window(job_dir, opened[0], day, windows))

        @server.tool()
        async def place_order(
            ref: Annotated[str, Field(description="The ref of the Place order button.")],
        ) -> str:
            """Place the order, once, after checkout is reported. Says what happened
            and what to do next."""
            return await act(checkout.place_order(job_dir, opened[0], ref))

        @server.tool()
        async def confirmed(
            number: Annotated[str, Field(description="The ref of Instacart's order number.")],
            window: Annotated[str, Field(description="The ref of the delivery window booked.")],
            day: Annotated[
                str,
                Field(
                    description="The ref of the element naming the delivery day (Today, "
                    "Tomorrow, or the date): the window's own ref when it shows the day."
                ),
            ],
            total: Annotated[
                str,
                Field(
                    description="The ref of the amount the payment method was authorized "
                    "for (not the order's Total, which Instacart re-estimates)."
                ),
            ],
        ) -> str:
            """Record what Instacart confirmed after Place order. Then you're done."""
            read = await act(
                checkout.record_confirmation(job_dir, opened[0], number, window, day, total)
            )
            return f"Recorded order {read.order_number}. You're done."

        @server.tool()
        async def not_confirmed() -> str:
            """Place order was pressed, but there's no confirmation, and the order
            isn't in your orders: record that, and stop (someone checks)."""
            await act(checkout.mark_not_confirmed(job_dir))
            return "Recorded. Stop here."

    return server


def serve(tools: Tools, job_dir: Path, instacart: InstacartSettings | None = None) -> None:
    """Serve the job's tools over stdio until the harness closes it."""
    match tools:
        case Tools.MEAL_EDIT:
            meal_edit_server(job_dir).run("stdio")
        case Tools.MEALS:
            meals_server(job_dir).run("stdio")
        case Tools.SHOPPING_LIST:
            shopping_list_server(job_dir).run("stdio")
        case Tools.PRODUCTS:
            if instacart is None:
                raise ValueError("The products tools need Instacart's settings.")
            address = read_context(job_dir, products.ShopJob).delivery_address
            if address is None:
                raise InstacartError(
                    "Delivery address: enter your address in Settings before shopping."
                )
            try:
                # One Instacart session (and the Codex login's lock) for the server's life.
                with connect(
                    instacart.codex_home,
                    instacart.store,
                    lock_file=instacart.lock_file,
                    command=shlex.split(instacart.command),
                    address=address,
                ) as store:
                    # Run saved broad searches before the model receives any shopping tools.
                    products.prefetch_more_alternatives(job_dir, store)
                    products_server(job_dir, store).run("stdio")
            except InstacartError as error:
                products.record_instacart_error(job_dir, str(error))
                raise
        case Tools.CHECKOUT:
            checkout_server(job_dir).run("stdio")
        case _:
            assert_never(tools)
