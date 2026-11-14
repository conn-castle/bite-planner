"""Shopping a draft's list at the Instacart store (spec/requirements.md "Shopping
list", stage 3): a `shop` job runs right after its list is built. The agent
searches the store and chooses each item's product, how many, and alternatives
(or that the store doesn't have it) through bite_planner_agent.products, which
checks each choice; the worker checks the result and saves it on the items."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from bite_planner_agent.products import (
    ItemChoice,
    ShopItem,
    ShopJob,
    load_state,
    more_choices,
    problems,
)
from bite_planner_agent.shopping import recipe_lines
from sqlmodel import Session, col, select

from bite_planner import prompts, shopping
from bite_planner.models import Job, ShoppingListItem


class InvalidProducts(Exception):
    """The agent didn't decide every item; the message says which."""


@dataclass(frozen=True)
class ShopContext:
    """What a shop job works on: the build's items to buy, numbered for the agent."""

    job: ShopJob
    # Each number's item.
    items: dict[int, UUID]
    prompt: str


def items_to_buy(session: Session, build_id: UUID) -> list[ShoppingListItem]:
    """The build's items the order buys: not assumed on hand, not taken off."""
    return [
        item
        for item in shopping.items_of(session, build_id)
        if not item.on_hand and not item.removed
    ]


def shop_context(session: Session, build: Job, store: str) -> ShopContext:
    """The agent's items, context, and instructions for shopping a build's list."""
    if build.list_source is None:
        raise InvalidProducts(f"Build {build.id} has no picks.")
    prepared = shopping.prepared_meals(session, build)
    names = {meal.meal.id: meal.meal.name for meal, _ in prepared}
    items = items_to_buy(session, build.id)
    shop_items = [
        ShopItem(
            number=number,
            name=item.name,
            quantity=shopping.ordered_quantity(item),
            unit=item.unit,
            meals=[names[meal_id] for meal_id in item.meal_ids if meal_id in names],
            more_alternatives_query=item.more_alternatives_query,
        )
        for number, item in enumerate(items, start=1)
    ]

    def amount(item: ShopItem) -> str:
        if item.quantity is None:
            return ""
        return ": " + " ".join([f"{item.quantity:g}", *filter(None, [item.unit])])

    prompt = prompts.fill(
        "shop_list",
        store=store,
        items="\n".join(
            f"{item.number}. {item.name}{amount(item)} ({', '.join(item.meals)})"
            for item in shop_items
        ),
        meals="\n".join(
            f"{meal.meal.name}, {meal.servings} servings: "
            + "; ".join(line.text for line in recipe_lines(meal.context, recipe.quantities))
            + " Steps: "
            + " ".join(recipe.steps)
            for meal, recipe in prepared
        ),
    )
    return ShopContext(
        job=ShopJob(items=shop_items),
        items={number: item.id for number, item in enumerate(items, start=1)},
        prompt=prompt,
    )


def finished_choices(job_dir: Path, job: ShopJob) -> dict[int, ItemChoice]:
    """The agent's decisions for the job's items (the worker's context, not the
    agent-writable job.json); InvalidProducts if any item is undecided."""
    state = load_state(job_dir)
    if missing := problems(job, state):
        raise InvalidProducts(" ".join(missing))
    choices: dict[int, ItemChoice] = {}
    for item in job.items:
        if item.more_alternatives_query is not None and item.number not in state.more_results:
            raise InvalidProducts(f"More alternatives weren't fetched for {item.name}.")
        choices[item.number] = state.choices[item.number].model_copy(
            update={
                "more_alternatives": more_choices(item, state.more_results.get(item.number, []))
            }
        )
    return choices


def save_choices(session: Session, items: dict[int, UUID], choices: dict[int, ItemChoice]) -> None:
    """Stage each item's product and alternatives (the caller commits)."""
    rows = {
        row.id: row
        for row in session.exec(
            select(ShoppingListItem).where(col(ShoppingListItem.id).in_(list(items.values())))
        ).all()
    }
    for number, item_id in items.items():
        choice, row = choices[number], rows[item_id]
        row.product = choice.chosen
        row.alternatives = choice.alternatives
        row.more_alternatives = choice.more_alternatives
        row.no_alternatives_reason = choice.no_alternatives_reason
        row.not_found = choice.chosen is None
        row.skippable_reason = choice.skippable_reason
        session.add(row)
