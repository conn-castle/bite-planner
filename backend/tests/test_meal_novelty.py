"""Novelty through the public MCP, worker and persistence boundaries, offline."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.meals import MealInput, MealsJob, MealTitle, load_meals
from castle_chef_agent.server import Tools, meals_server
from castle_chef_agent.title_similarity import encoder
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from sqlmodel import Session

from castle_chef import orders, ratings, worker
from castle_chef.db import get_engine
from castle_chef.meals import novelty_snapshot
from castle_chef.models import GroceryOrder, Household, Job, JobKind, JobStatus, MealRating
from tests.test_meals_tools import MEAL, add
from tests.test_products_tools import call
from tests.test_worker import job_meals, new_job, reload, run_job, suggest

NOW: datetime = datetime(2026, 3, 31, 12, 15, 30, tzinfo=UTC)
REGRESSIONS: list[tuple[str, str]] = [
    (
        "Sheet-Pan Lemon Chicken, Potatoes and Green Beans",
        "Sheet-Pan Lemon Chicken, Broccoli and Potatoes",
    ),
    ("Honey-Soy Salmon with Rice and Broccoli", "Honey-Soy Salmon with Snap Peas and Rice"),
    ("Mild Coconut Chicken Curry with Rice", "Mild Coconut Chicken Curry with Rice"),
]
DISTINCT: list[str] = [
    "Sheet Pan Sausage and Peppers",
    "Mushroom Risotto",
    "Lemon Salmon",
    "Chickpea Curry",
    "Beef Tacos",
    "Pork Dumplings",
    "Spinach Lasagna",
    "Chicken Noodle Soup",
    "Shrimp Fried Rice",
    "Eggplant Parmesan",
    "Tuna Nicoise Salad",
    "Black Bean Enchiladas",
    "Turkey Meatloaf",
    "Lamb Kofta",
    "Butternut Squash Soup",
    "Crab Cakes",
    "Paneer Tikka",
    "Falafel Wraps",
    "Beef Stroganoff",
    "Scallop Linguine",
]


def title(name: str) -> MealTitle:
    return MealTitle(name=name, created_at=NOW)


@pytest.mark.parametrize(("earlier", "candidate"), REGRESSIONS)
@pytest.mark.parametrize("category", ["current_draft", "recent"])
def test_twenty_more_refuses_the_three_reported_repeats(
    tmp_path: Path, earlier: str, candidate: str, category: str
) -> None:
    snapshot = MealsJob(count=20)
    getattr(snapshot, category).append(title(earlier))
    write_context(tmp_path, snapshot)
    tools = meals_server(tmp_path)

    refused, message = add(tools, MEAL | {"name": candidate})

    assert refused and earlier in message
    assert ("current draft" if category == "current_draft" else "recent suggestion") in message
    assert load_meals(tmp_path) == []
    assert not add(tools, MEAL | {"name": "Ginger Tofu Stir-Fry with Broccoli"})[0]


@pytest.mark.parametrize("rating", [None, *MealRating])
def test_current_rating_controls_recent_restriction_and_draft_veto(
    session: Session, job: Job, household_id: UUID, tmp_path: Path, rating: MealRating | None
) -> None:
    earlier, candidate = REGRESSIONS[0]
    [meal] = suggest(session, job, (earlier, None))
    meal.created_at = NOW - timedelta(days=3)
    session.add(meal)
    if rating is not None:
        ratings.write(session, household_id, meal.id, rating)
    session.commit()
    same_draft = novelty_snapshot(session, job, 1, NOW + timedelta(days=100))
    draft = session.get_one(GroceryOrder, job.grocery_order_id)
    orders.discard(session, household_id, draft.id, draft.created_by)
    later = new_job(session, household_id)
    snapshot = novelty_snapshot(session, later, 1, NOW)
    write_context(tmp_path, snapshot)
    refused, message = add(meals_server(tmp_path), MEAL | {"name": candidate})
    assert refused == (rating not in (MealRating.UP, MealRating.FAVORITE)), message

    # Positives never waive the same-draft veto, even in a much older draft.
    (tmp_path / "same-draft").mkdir()
    write_context(tmp_path / "same-draft", same_draft)
    refused, message = add(meals_server(tmp_path / "same-draft"), MEAL | {"name": candidate})
    assert refused and "current draft" in message


def test_positive_removes_only_the_matched_history_title(tmp_path: Path) -> None:
    candidate = "Black Bean and Corn Quesadillas"
    positive = "Cheesy Black Bean Quesadillas with Sweetcorn"
    restricted = "Corn and Black Bean Quesadillas with Salsa"
    # Actual non-transitive model matches: candidate matches both, history doesn't match positive.
    write_context(
        tmp_path, MealsJob(count=1, recent=[title(restricted)], positive=[title(positive)])
    )
    refused, message = add(meals_server(tmp_path), MEAL | {"name": candidate})
    assert refused and restricted in message

    # A historical renamed positive is exempt even with a different meal UUID/name.
    earlier, variant = REGRESSIONS[1]
    write_context(tmp_path, MealsJob(count=1, recent=[title(earlier)], positive=[title(variant)]))
    assert not add(meals_server(tmp_path), MEAL | {"name": earlier})[0]


@pytest.mark.parametrize("now", [NOW, NOW.replace(year=2028), NOW.replace(month=1)])
def test_calendar_month_inclusive_window_and_household_isolation(
    session: Session, job: Job, household_id: UUID, tmp_path: Path, now: datetime
) -> None:
    cutoff = {
        (2026, 3): datetime(2026, 2, 28, 12, 15, 30, tzinfo=UTC),
        (2028, 3): datetime(2028, 2, 29, 12, 15, 30, tzinfo=UTC),
        (2026, 1): datetime(2025, 12, 31, 12, 15, 30, tzinfo=UTC),
    }[(now.year, now.month)]
    names = ["Mushroom Risotto", "Beef Tacos", "Crab Cakes", "Paneer Tikka", "Falafel Wraps"]
    meals = suggest(session, job, *((name, None) for name in names))
    by_name = {meal.name: meal for meal in meals}
    for name, instant in zip(
        names,
        [
            cutoff,
            cutoff - timedelta(microseconds=1),
            now,
            now + timedelta(microseconds=1),
            cutoff - timedelta(days=40),
        ],
        strict=True,
    ):
        meal = by_name[name]
        meal.created_at = instant
        session.add(meal)
    ratings.write(session, household_id, by_name[names[-1]].id, MealRating.FAVORITE)
    other = Household(name="Other household")
    session.add(other)
    session.commit()
    other_job = new_job(session, other.id)
    [other_meal] = suggest(session, other_job, ("Spinach Lasagna", None))
    other_meal.created_at = now
    session.add(other_meal)
    ratings.write(session, other.id, other_meal.id, MealRating.UP)
    session.commit()
    draft = session.get_one(GroceryOrder, job.grocery_order_id)
    orders.discard(session, household_id, draft.id, draft.created_by)
    later = new_job(session, household_id)
    # Pass a non-UTC representation of the same instant; boundaries remain UTC.
    from datetime import timezone

    snapshot = novelty_snapshot(session, later, 1, now.astimezone(timezone(timedelta(hours=-5))))
    assert snapshot.snapshot_at == now
    assert {entry.name for entry in snapshot.recent} == {names[0], names[2]}
    assert [entry.name for entry in snapshot.positive] == [names[-1]]
    write_context(tmp_path, snapshot)
    tools = meals_server(tmp_path)
    for name in [names[0], names[2]]:
        refused, message = add(tools, MEAL | {"name": name})
        assert refused and name in message
    assert not add(tools, MEAL | {"name": names[1]})[0]


def test_discard_retains_history_current_names_and_current_rating_snapshot(
    session: Session, job: Job, draft: GroceryOrder, household_id: UUID, tmp_path: Path
) -> None:
    [meal] = suggest(session, job, ("Lemon Chicken", None))
    # Accepted edits update this canonical field; suggestion time stays unchanged.
    meal.name = REGRESSIONS[0][0]
    meal.created_at = NOW - timedelta(days=1)
    session.add(meal)
    ratings.write(session, household_id, meal.id, MealRating.FAVORITE)
    session.commit()
    orders.discard(session, household_id, draft.id, draft.created_by)
    session.refresh(meal)
    assert meal.job_id is None and meal.household_id == household_id
    later = new_job(session, household_id)
    original = novelty_snapshot(session, later, 1, NOW)
    ratings.clear(session, household_id, meal.id)
    session.commit()
    cleared = novelty_snapshot(session, later, 1, NOW)
    assert original.positive and not cleared.positive
    ratings.write(session, household_id, meal.id, MealRating.DOWN)
    session.commit()
    replaced = novelty_snapshot(session, later, 1, NOW)
    assert not replaced.positive and replaced.recent == cleared.recent
    for index, (snapshot, should_refuse) in enumerate(
        [(original, False), (cleared, True), (replaced, True)]
    ):
        directory = tmp_path / str(index)
        directory.mkdir()
        write_context(directory, snapshot)
        refused, _ = add(meals_server(directory), MEAL | {"name": REGRESSIONS[0][1]})
        assert refused == should_refuse


def test_preflight_reserves_nothing_and_parallel_semantic_adds_are_atomic(tmp_path: Path) -> None:
    write_context(tmp_path, MealsJob(count=2))
    tools = meals_server(tmp_path)
    earlier, variant = REGRESSIONS[0]
    check = call(tools, "check_meal_titles", names=[earlier, variant, "Beef Tacos"])
    assert not check[0] and earlier in check[1] and "current draft" in check[1]
    assert not load_meals(tmp_path)

    async def at_once() -> None:
        results = await asyncio.gather(
            *(
                tools.call_tool("add_meal", {"meal": MEAL | {"name": name}})
                for name in (earlier, variant)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ToolError) for result in results) == 1

    asyncio.run(at_once())
    assert len(load_meals(tmp_path)) == 1
    assert "1 of 2" in call(tools, "done")[1]
    # Earlier preflight approval cannot bypass a title saved in the meantime.
    refused, message = add(tools, MEAL | {"name": variant})
    assert refused
    assert "current draft" in message or "already added" in message
    assert not add(tools, MEAL | {"name": "Beef Tacos"})[0]
    assert call(tools, "done") == (False, "Complete.")
    assert add(tools, MEAL | {"name": "Crab Cakes"})[0]


def test_worker_uses_original_snapshot_and_publishes_no_partial_batch(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    [earlier] = suggest(session, job, (REGRESSIONS[0][0], None))
    job.status = JobStatus.SUCCEEDED
    later = Job(
        id=uuid4(),
        grocery_order_id=job.grocery_order_id,
        kind=JobKind.GENERATE_MEALS,
        requested_by=job.requested_by,
    )
    session.add(job)
    session.add(later)
    session.commit()
    monkeypatch.setenv("CASTLE_CHEF_MEALS_PER_BATCH", "2")

    def tamper(directory: Path, prompt: str, tools: Tools, context: BaseModel) -> None:
        assert isinstance(context, MealsJob) and context.current_draft
        assert REGRESSIONS[0][0] not in prompt  # No history lists in the prompt.
        # The agent rewrites count/history, then writes the right count directly.
        directory.mkdir(parents=True)
        write_context(directory, MealsJob(count=1))
        (directory / "meals").mkdir()
        for index, name in enumerate(["Beef Tacos", REGRESSIONS[0][1]]):
            (directory / "meals" / f"{index:03}.json").write_text(
                MealInput.model_validate(MEAL | {"name": name}).model_dump_json()
            )

    monkeypatch.setattr(worker, "run_agent", tamper)
    run_job(later)
    failed = reload(session, later)
    assert failed.status == JobStatus.FAILED and failed.error is not None
    assert earlier.name in failed.error and "current draft" in failed.error
    assert job_meals(session, later) == []
    assert [meal.id for meal in job_meals(session, job)] == [earlier.id]
    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_rejection_heavy_twenty_meal_batch_validates_in_worker(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    suggest(session, job, *((pair[0], None) for pair in REGRESSIONS))
    monkeypatch.setenv("CASTLE_CHEF_MEALS_PER_BATCH", "20")

    def agent(directory: Path, prompt: str, tools: Tools, context: BaseModel) -> None:
        directory.mkdir(parents=True)
        write_context(directory, context)
        server = meals_server(directory)
        # 20 rejected names in one title-only round, then one replacement round.
        rejected = call(
            server, "check_meal_titles", names=[REGRESSIONS[i % 3][1] for i in range(20)]
        )
        assert rejected[1].count("Choose a different meal idea") == 20
        assert not load_meals(directory)
        assert "Names are distinct" in call(server, "check_meal_titles", names=DISTINCT)[1]
        for name in DISTINCT:
            assert not add(server, MEAL | {"name": name})[0]
        assert call(server, "done") == (False, "Complete.")

    monkeypatch.setattr(worker, "run_agent", agent)
    result = worker.process_job(job)
    assert isinstance(result, worker.Meals) and len(result.meals) == 20


@pytest.mark.parametrize("corrupt", [False, True])
def test_unavailable_model_is_actionable_tool_and_worker_failure(
    session: Session, job: Job, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    directory = tmp_path / "broken-model"
    directory.mkdir()
    if corrupt:
        (directory / "config.json").write_text("corrupt")
    monkeypatch.setenv("CASTLE_CHEF_TITLE_MODEL_DIR", str(directory))
    write_context(tmp_path, MealsJob(count=1))
    refused, message = add(meals_server(tmp_path), MEAL)
    assert refused and ("checksum mismatch" if corrupt else "Cannot read") in message
    assert "make title-model" in message and not load_meals(tmp_path)
    run_job(job)
    failed = reload(session, job)
    assert failed.status == JobStatus.FAILED and failed.error is not None
    assert "make title-model" in failed.error
    assert job_meals(session, job) == []


def test_cosine_includes_the_exact_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    model = encoder()

    # Exercise the production comparator's numeric boundary with controlled vectors.
    def vector(name: str) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
        return np.asarray([1.0 if name == "left" else 0.80], dtype=np.float64)

    monkeypatch.setattr(model, "vector", vector)
    assert model.matches("left", "at cutoff")

    def below(name: str) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
        return np.asarray(
            [1.0 if name == "left" else np.nextafter(0.80, 0.0)],
            dtype=np.float64,
        )

    monkeypatch.setattr(model, "vector", below)
    assert not model.matches("left", "below cutoff")


def test_runtime_parity_with_frozen_qualification_scores() -> None:
    model = encoder()
    # Frozen narrow-runtime qualification fixtures, no threshold fitting in tests.
    scores: list[tuple[str, str, float]] = [
        (
            "Black Bean and Corn Quesadillas",
            "Cheesy Black Bean Quesadillas with Sweetcorn",
            0.8024390936,
        ),
        (
            "Black Bean and Corn Quesadillas",
            "Corn and Black Bean Quesadillas with Salsa",
            0.8791278005,
        ),
        ("Shakshuka with Crusty Bread", "Tomato and Pepper Shakshuka with Toast", 0.6468706131),
    ]
    for left, right, expected in scores:
        assert float(model.vector(left) @ model.vector(right)) == pytest.approx(expected, abs=1e-5)


def test_worker_feedback_and_novelty_share_one_current_rating_snapshot(
    session: Session, job: Job, household_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    [meal] = suggest(session, job, ("Lemon Salmon", None))
    ratings.write(session, household_id, meal.id, MealRating.UP)
    session.commit()
    original_prompt = worker.generate_meals_prompt

    def prompt_then_rating_changes(reading: Session, pending: Job, count: int) -> str:
        prompt = original_prompt(reading, pending, count)
        with Session(get_engine()) as updating:
            ratings.clear(updating, household_id, meal.id)
            updating.commit()
        return prompt

    def agent(directory: Path, prompt: str, tools: Tools, context: BaseModel) -> None:
        assert "- Lemon Salmon" in prompt
        assert isinstance(context, MealsJob)
        assert [entry.name for entry in context.positive] == ["Lemon Salmon"]
        directory.mkdir(parents=True)
        write_context(directory, context)
        assert not add(meals_server(directory), MEAL | {"name": "Beef Tacos"})[0]

    monkeypatch.setattr(worker, "generate_meals_prompt", prompt_then_rating_changes)
    monkeypatch.setattr(worker, "run_agent", agent)
    result = worker.process_job(job)
    assert isinstance(result, worker.Meals)
    assert not novelty_snapshot(session, job, 1, datetime.now(UTC)).positive


def test_simultaneous_distinct_adds_and_preflight_cannot_overfill(tmp_path: Path) -> None:
    write_context(tmp_path, MealsJob(count=1))
    tools = meals_server(tmp_path)

    async def at_once() -> None:
        results = await asyncio.gather(
            tools.call_tool("add_meal", {"meal": MEAL | {"name": "Beef Tacos"}}),
            tools.call_tool("add_meal", {"meal": MEAL | {"name": "Mushroom Risotto"}}),
            tools.call_tool("check_meal_titles", {"names": ["Beef Tacos", "Mushroom Risotto"]}),
            return_exceptions=True,
        )
        refused = [result for result in results if isinstance(result, ToolError)]
        assert len(refused) == 1 and "All 1 meals are already added" in str(refused[0])
        assert not isinstance(results[-1], BaseException)

    asyncio.run(at_once())
    assert len(load_meals(tmp_path)) == 1
    assert call(tools, "done") == (False, "Complete.")
