"""The products the agent chooses for a shopping list (a `shop` job): for each item,
one product from its searches and how many, with alternatives, or that the store
doesn't have it. Kept in the job directory for the worker; every choice is
checked as it's made."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from castle_chef_agent.instacart import Product
from castle_chef_agent.jobfiles import locked, read_context, replace_file

STATE_FILE = "products.json"
MAX_ALTERNATIVES = 8
# How far short of an item's amount a choice may fall (package sizes and
# weighed items' estimates are approximate).
SHORTFALL = 0.05


class ShopItem(BaseModel):
    """An item of the list to shop for, as the agent sees it."""

    number: int
    name: str
    # The amount the list needs, in `unit` (a count when null); null: none given.
    quantity: float | None
    unit: str | None
    meals: list[str]


class ShopJob(BaseModel):
    """The worker's context for the checks: the list's items to shop for."""

    items: list[ShopItem]


class ProductCount(BaseModel):
    """A product and how many of it: packages, bags, or pieces."""

    model_config = ConfigDict(frozen=True)

    product: Product
    count: int = Field(ge=1)


class ItemChoice(BaseModel):
    """What the agent decided for one item."""

    item: int
    # The product to buy, or null: the store doesn't have the item.
    chosen: ProductCount | None
    alternatives: list[ProductCount] = Field(max_length=MAX_ALTERNATIVES)
    # Why there are no alternatives (a chosen item with none).
    no_alternatives_reason: str | None = None
    # Why the meals work without it (an item the store doesn't have).
    skippable_reason: str | None = None


class ShopState(BaseModel):
    """What the tools recorded, in the job directory (STATE_FILE)."""

    # The products each item's searches returned, by item and id: what may be
    # chosen for it (a product found for one item can't be chosen for another).
    searched: dict[int, dict[str, Product]] = {}
    choices: dict[int, ItemChoice] = {}


class ChoiceRefused(Exception):
    """A choice breaks a rule; the message says which and how to fix it."""


def load_state(job_dir: Path) -> ShopState:
    path = job_dir / STATE_FILE
    return ShopState.model_validate_json(path.read_text()) if path.exists() else ShopState()


@contextmanager
def _changing(job_dir: Path) -> Iterator[ShopState]:
    """The searches and choices, to change under the job directory's lock: saved
    when the block ends, unless it raises."""
    with locked(job_dir):
        state = load_state(job_dir)
        yield state
        replace_file(job_dir / STATE_FILE, state.model_dump_json())


def record_search(job_dir: Path, item: int, products: list[Product]) -> None:
    """Remember the products a search for the item returned, so they can be chosen for it."""
    _item(read_context(job_dir, ShopJob), item)
    with _changing(job_dir) as state:
        state.searched.setdefault(item, {}).update({p.product_id: p for p in products})


# Amounts in comparable units: ounces of weight, fluid ounces of volume, and counts.
_WEIGHT = {"lb": 16.0, "lbs": 16.0, "pound": 16.0, "oz": 1.0, "ounce": 1.0, "g": 0.035274,
           "gram": 0.035274, "kg": 35.274}  # fmt: skip
_VOLUME = {"fl oz": 1.0, "cup": 8.0, "tbsp": 0.5, "tablespoon": 0.5, "tsp": 1 / 6,
           "teaspoon": 1 / 6, "pt": 16.0, "pint": 16.0, "qt": 32.0, "quart": 32.0,
           "gal": 128.0, "gallon": 128.0, "ml": 0.033814, "l": 33.814, "liter": 33.814}  # fmt: skip
_COUNT = {"each": 1.0, "ct": 1.0, "count": 1.0}


def _measure(quantity: float, unit: str | None) -> tuple[str, float] | None:
    """(kind, amount) in the kind's base unit; None if the unit isn't one of them."""
    if unit is None:
        return "count", quantity
    unit = unit.strip().lower().rstrip(".")
    unit = unit[:-1] if unit.endswith("s") and unit[:-1] in _WEIGHT | _VOLUME | _COUNT else unit
    for kind, table in (("weight", _WEIGHT), ("volume", _VOLUME), ("count", _COUNT)):
        if unit in table:
            return kind, quantity * table[unit]
    return None


def _package(product: Product) -> tuple[str, float] | None:
    """How much one of the product holds, if its size says."""
    if product.estimated_lb is not None:
        measured: tuple[str, float] | None = ("weight", product.estimated_lb * 16)
    else:
        match = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z .]+?)\s*", product.size or "")
        try:
            measured = _measure(float(match.group(1)), match.group(2)) if match else None
        except ValueError:
            measured = None
    # A size of nothing says nothing about how much it holds.
    return measured if measured is not None and measured[1] > 0 else None


def _shortfall(item: ShopItem, choice: ProductCount) -> str | None:
    """Why the choice doesn't cover the item's amount, when the units compare."""
    if item.quantity is None:
        return None
    needed, package = _measure(item.quantity, item.unit), _package(choice.product)
    if needed is None or package is None or needed[0] != package[0]:
        return None
    if package[1] * choice.count >= needed[1] * (1 - SHORTFALL):
        return None
    amount = " ".join([f"{item.quantity:g}", *filter(None, [item.unit])])
    fits = int(-(-needed[1] // package[1]))  # packages that would cover it
    return (
        f"{choice.count} of {choice.product.name} ({choice.product.size}) doesn't cover "
        f"{amount} of {item.name}: it takes at least {fits}."
    )


def _counted(state: ShopState, item: int, product_id: str, count: int) -> ProductCount:
    product = state.searched.get(item, {}).get(product_id)
    if product is None:
        raise ChoiceRefused(
            f"No search for item {item} returned product {product_id}; choose from its results."
        )
    if count < 1:
        raise ChoiceRefused("Choose at least 1.")
    return ProductCount(product=product, count=count)


def _item(job: ShopJob, number: int) -> ShopItem:
    for item in job.items:
        if item.number == number:
            return item
    raise ChoiceRefused(f"There's no item {number} on the list.")


def _alternatives(
    state: ShopState, item: ShopItem, alternatives: list[tuple[str, int]], chosen: str | None
) -> list[ProductCount]:
    """The alternatives, each from the item's searches and covering its amount too
    (the member may pick any of them)."""
    if len(alternatives) > MAX_ALTERNATIVES:
        raise ChoiceRefused(f"Give at most {MAX_ALTERNATIVES} alternatives.")
    ids = [product_id for product_id, _ in alternatives]
    if len(set(ids)) != len(ids) or (chosen is not None and chosen in ids):
        raise ChoiceRefused("Give each alternative once, and not the product chosen.")
    counted = [
        _counted(state, item.number, product_id, count) for product_id, count in alternatives
    ]
    for alternative in counted:
        if problem := _shortfall(item, alternative):
            raise ChoiceRefused(f"Alternative: {problem}")
    return counted


def choose(
    job_dir: Path,
    item: int,
    product_id: str,
    count: int,
    alternatives: list[tuple[str, int]],
    no_alternatives_reason: str | None,
) -> ItemChoice:
    """Choose the item's product and how many, with its alternatives (or why there
    are none); replaces an earlier decision for the item."""
    job = read_context(job_dir, ShopJob)
    with _changing(job_dir) as state:
        shop_item = _item(job, item)
        chosen = _counted(state, item, product_id, count)
        if problem := _shortfall(shop_item, chosen):
            raise ChoiceRefused(problem)
        others = _alternatives(state, shop_item, alternatives, product_id)
        reason = (no_alternatives_reason or "").strip() or None
        if not others and reason is None:
            raise ChoiceRefused("Give alternatives, or say why there are none.")
        choice = ItemChoice(
            item=item,
            chosen=chosen,
            alternatives=others,
            no_alternatives_reason=None if others else reason,
        )
        state.choices[item] = choice
    return choice


def not_found(
    job_dir: Path, item: int, alternatives: list[tuple[str, int]], skippable_reason: str | None
) -> ItemChoice:
    """Record that the store doesn't have the item, with alternatives to buy and/or
    why the meals work without it; replaces an earlier decision for the item."""
    job = read_context(job_dir, ShopJob)
    with _changing(job_dir) as state:
        shop_item = _item(job, item)
        if not state.searched.get(item):
            raise ChoiceRefused(
                f"Search for item {item} first: it's not found only if a search says so."
            )
        others = _alternatives(state, shop_item, alternatives, None)
        reason = (skippable_reason or "").strip() or None
        if not others and reason is None:
            raise ChoiceRefused(
                "Give alternatives to buy, or say why the meals work without it (or both)."
            )
        choice = ItemChoice(item=item, chosen=None, alternatives=others, skippable_reason=reason)
        state.choices[item] = choice
    return choice


def problems(job: ShopJob, state: ShopState) -> list[str]:
    """What's left to decide: every item needs a product or to be marked not found."""
    missing = [item for item in job.items if item.number not in state.choices]
    if not missing:
        return []
    names = ", ".join(f"{item.number} ({item.name})" for item in missing)
    return [f"Still to decide: {names}."]
