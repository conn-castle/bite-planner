"""Confirmed cooking consumption, tested through the public MCP and worker boundary."""

from pathlib import Path
from uuid import uuid4

import pytest
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.server import shopping_list_server
from castle_chef_agent.shopping import (
    ListJob,
    PreparedRecipe,
    PreparedRecipes,
    RecipeContext,
    RecipeIngredient,
    RecipeStep,
    recipe_lines,
)

from castle_chef import shopping
from tests.test_products_tools import call


def proportional_snapshot(recipes: list[shopping.RecipeMeal]) -> PreparedRecipes:
    """An explicit deterministic fake agent for unrelated pre-existing fixtures."""
    return PreparedRecipes(
        recipes=[
            PreparedRecipe(
                meal_id=r.meal.id,
                base_revision=r.meal.revision,
                servings=r.servings,
                quantities=[
                    None if i.quantity is None else i.quantity * r.servings / r.meal.servings
                    for i in r.meal.ingredients
                ],
                steps=[s.text for s in r.meal.steps],
            )
            for r in recipes
        ]
    )


def context(number: int = 1, servings: int = 5) -> RecipeContext:
    return RecipeContext(
        number=number,
        meal_id=uuid4(),
        base_revision=3,
        base_servings=2,
        servings=servings,
        ingredients=[
            RecipeIngredient(name="beans", quantity=0.75, unit="can", preparation="drained"),
            RecipeIngredient(name="lemon", quantity=0.25),
            RecipeIngredient(name="salt", quantity=None, preparation="to taste"),
        ],
        steps=[
            RecipeStep(
                text="Heat ½ can beans; reserve ¼ can. Add ¼ lemon and salt.",
                timer_minutes=7,
                ingredients=[0, 1, 2],
            ),
            RecipeStep(text="Fold in reserved ¼ can beans.", ingredients=[0]),
        ],
    )


def make_job(*recipes: RecipeContext) -> ListJob:
    return ListJob(
        recipes=list(recipes),
        lines=[
            line for r in recipes for line in recipe_lines(r, [i.quantity for i in r.ingredients])
        ],
    )


def adaptation(meal: int = 1) -> dict[str, object]:
    return {
        "meal": meal,
        "ingredients": [
            {"name": "beans", "quantity": 2},
            {"name": "lemon", "quantity": 0.25},
            {"name": "salt", "quantity": None},
        ],
        "steps": [
            "Heat 1½ cans beans; reserve ½ can. Add ¼ lemon and salt.",
            "Fold in reserved ½ can beans.",
        ],
    }


def test_confirm_then_cover_and_retry_without_changing_consumption(tmp_path: Path) -> None:
    job = make_job(context())
    write_context(tmp_path, job)
    tools = shopping_list_server(tmp_path)
    assert call(
        tools,
        "add_item",
        name="beans",
        more_alternatives_query="beans",
        quantity=2,
        unit="can",
        aisle="pantry",
        lines=["1a"],
    )[0]
    assert "needs adapt_recipe" in call(tools, "done")[1]
    refused, text = call(tools, "adapt_recipe", **adaptation())
    assert not refused, text
    assert "1a: 2 can beans, drained" in text and "1b: 0.25 lemon" in text
    assert not call(
        tools,
        "add_item",
        name="beans",
        more_alternatives_query="beans",
        quantity=2,
        unit="can",
        aisle="pantry",
        lines=["1a"],
    )[0]
    assert call(tools, "adapt_recipe", **adaptation())[0]
    assert not call(tools, "remove", name="beans")[0]
    assert not call(tools, "adapt_recipe", **adaptation())[0]
    for name, line, quantity, unit in [("beans", "1a", 2, "can"), ("lemon", "1b", 0.25, None)]:
        assert not call(
            tools,
            "add_item",
            name=name,
            more_alternatives_query=name,
            quantity=quantity,
            unit=unit,
            aisle="pantry",
            lines=[line],
        )[0]
    assert not call(tools, "add_staple", staple="salt", lines=["1c"])[0]
    assert not call(tools, "done")[0]
    prepared = shopping.finished_list(tmp_path, job).prepared
    assert prepared.recipes[0].quantities == [2, 0.25, None]
    assert prepared.recipes[0].steps == adaptation()["steps"]
    assert not prepared.legacy_scaled
    assert [i.quantity for i in shopping.finished_list(tmp_path, job).items] == [2, 1, None]


@pytest.mark.parametrize(
    "invalid",
    [
        {"meal": 7},
        {"ingredients": []},
        {"steps": ["Only one step."]},
        {"steps": ["", "Fold."]},
        {"ingredients": [{"name": "beans", "quantity": 2, "unit": "lb"}]},
        {"ingredients": [{"name": "beans", "quantity": float("inf")}]},
        {"ingredients": [{"name": "beans", "quantity": 0}]},
        {
            "ingredients": [
                {"name": "beans", "quantity": None},
                {"name": "lemon", "quantity": 0.25},
                {"name": "salt", "quantity": None},
            ]
        },
        {
            "ingredients": [
                {"name": "changed", "quantity": 2},
                {"name": "lemon", "quantity": 0.25},
                {"name": "salt", "quantity": None},
            ]
        },
    ],
)
def test_invalid_adaptation_is_refused(tmp_path: Path, invalid: dict[str, object]) -> None:
    job = make_job(context())
    write_context(tmp_path, job)
    assert call(shopping_list_server(tmp_path), "adapt_recipe", **(adaptation() | invalid))[0]
    assert "needs adapt_recipe" in call(shopping_list_server(tmp_path), "done")[1]


@pytest.mark.parametrize("mixed", [False, True])
def test_worker_supplies_identity_and_rejects_context_tampering(
    tmp_path: Path, mixed: bool
) -> None:
    identity = context(servings=2)
    recipes = [identity, context(2)] if mixed else [identity]
    job = make_job(*recipes)
    write_context(tmp_path, job)
    tools = shopping_list_server(tmp_path)
    assert call(tools, "adapt_recipe", **adaptation())[0]
    if mixed:
        assert not call(tools, "adapt_recipe", **adaptation(2))[0]
    for number in range(1, len(recipes) + 1):
        for suffix, name, quantity in [("a", "beans", 2), ("b", "lemon", 0.25)]:
            assert not call(
                tools,
                "add_item",
                name=f"{name} {number}",
                more_alternatives_query=name,
                quantity=quantity,
                aisle="pantry",
                lines=[f"{number}{suffix}"],
            )[0]
    assert not call(
        tools, "add_staple", staple="salt", lines=[f"{n}c" for n in range(1, len(recipes) + 1)]
    )[0]
    prepared = shopping.finished_list(tmp_path, job).prepared
    assert prepared.recipes[0].quantities == [0.75, 0.25, None]
    assert prepared.recipes[0].steps == [s.text for s in identity.steps]
    if mixed:
        assert prepared.recipes[1].quantities == [2, 0.25, None]
    # A writable context cannot authorize a foreign adaptation in the worker's copy.
    foreign = context(3)
    write_context(tmp_path, make_job(*recipes, foreign))
    assert not call(tools, "adapt_recipe", **adaptation(3))[0]
    with pytest.raises(shopping.InvalidList, match="no meal 3"):
        shopping.finished_list(tmp_path, job)
