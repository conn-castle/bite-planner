"""The worker, through `castle-chef job run` (or `worker.run_job`) with a fake agent."""

import fcntl
import json
import logging
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, col, select
from typer.testing import CliRunner

from castle_chef import orders, push, shopping, worker
from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.jobs import SUPERSEDED_ERROR, claim_job, fail_job, submit_build_list
from castle_chef.meals import IngredientInput, MealInput, add_meals
from castle_chef.models import (
    AppUser,
    GroceryOrder,
    GroceryOrderMeal,
    Household,
    Job,
    JobKind,
    JobStatus,
    ListPick,
    ListSource,
    Meal,
    MealRating,
    PushDevice,
    ShoppingListItem,
)
from castle_chef.push import Notification, NotificationKind, Outcome, Sent

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def use_fake_agent(
    monkeypatch: pytest.MonkeyPatch, meals: int, stop_reason: str = "end_turn", exit_code: int = 0
) -> None:
    monkeypatch.setenv(
        "CASTLE_CHEF_AGENT_COMMAND",
        f"{sys.executable} {FAKE_AGENT} {meals} {stop_reason} {exit_code} 0",
    )


def batch_size(monkeypatch: pytest.MonkeyPatch, meals: int) -> None:
    monkeypatch.setenv("CASTLE_CHEF_MEALS_PER_BATCH", str(meals))


def run_job(job: Job, expected_exit: int = 0) -> str:
    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])
    assert result.exit_code == expected_exit, result.output
    return result.output


def reload(session: Session, job: Job) -> Job:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one()


def job_meals(session: Session, job: Job) -> list[Meal]:
    session.expire_all()
    return list(
        session.exec(select(Meal).where(Meal.job_id == job.id).order_by(col(Meal.name))).all()
    )


def log_events(logs_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (logs_dir / "castle-chef.jsonl").read_text().splitlines()]


def test_job_succeeds_when_agent_adds_a_batch_of_meals(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch_size(monkeypatch, 3)
    use_fake_agent(monkeypatch, meals=3)

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.SUCCEEDED
    assert job.error is None
    meals = job_meals(session, job)
    assert [meal.name for meal in meals] == [f"Sheet Pan Sausage and Peppers {i}" for i in range(3)]
    assert [(i.position, i.name) for i in meals[0].ingredients] == [
        (0, "Italian sausage"),
        (1, "bell peppers"),
    ]
    prompt = (tmp_path / "jobs" / str(job.id) / "fake-agent-prompt.md").read_text()
    assert "Create 3 distinct meals" in prompt


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Notification]] = []

    def send(self, token: str, notification: Notification) -> Sent:
        self.sent.append((token, notification))
        return Sent(Outcome.SENT)


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch) -> RecordingSender:
    recording = RecordingSender()
    monkeypatch.setattr(push, "get_sender", lambda: recording)
    return recording


def with_phone(session: Session, job: Job) -> str:
    """The member who asked for the batch, with a registered phone (its token)."""
    order = session.get_one(GroceryOrder, job.grocery_order_id)
    session.add(AppUser(user_id=job.requested_by, household_id=order.household_id))
    session.add(PushDevice(token="ab" * 32, user_id=job.requested_by))
    session.commit()
    return "ab" * 32


def test_whoever_asked_for_the_batch_hears_when_its_ideas_are_ready(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, sender: RecordingSender
) -> None:
    phone = with_phone(session, job)
    batch_size(monkeypatch, 3)
    use_fake_agent(monkeypatch, meals=3)

    run_job(job)

    [(token, notification)] = sender.sent
    assert token == phone
    # The household's first order: nothing yet to decide it by.
    assert (notification.kind, notification.title, notification.body, notification.order_id) == (
        NotificationKind.IDEAS_READY,
        "Your meal ideas are ready",
        "3 ideas for Order 1.",
        job.grocery_order_id,
    )


def test_a_failed_batch_sends_nothing(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, sender: RecordingSender
) -> None:
    with_phone(session, job)
    use_fake_agent(monkeypatch, meals=0, exit_code=3)

    run_job(job)

    assert sender.sent == []


def test_a_batch_stays_succeeded_when_its_notification_cannot_be_sent(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_phone(session, job)
    use_fake_agent(monkeypatch, meals=1)

    def broken() -> None:
        raise RuntimeError("Set CASTLE_CHEF_APNS_KEY and CASTLE_CHEF_APNS_KEY_ID.")

    monkeypatch.setattr(push, "get_sender", broken)

    run_job(job)

    assert reload(session, job).status == JobStatus.SUCCEEDED
    [failed] = [e for e in log_events(tmp_path / "logs") if e["event"] == "job.notify_failed"]
    assert failed["level"] == "error"


def test_a_batch_is_20_meals_by_default(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CASTLE_CHEF_MEALS_PER_BATCH")
    use_fake_agent(monkeypatch, meals=0)

    run_job(job)

    prompt = (tmp_path / "jobs" / str(job.id) / "fake-agent-prompt.md").read_text()
    assert "Create 20 distinct meals" in prompt
    assert reload(session, job).error == "Requested 20 meals, but the agent added 0."


def test_agent_environment_has_no_database_credentials(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    env = json.loads((tmp_path / "jobs" / str(job.id) / "fake-agent-env.json").read_text())
    assert "DATABASE_URL" not in env
    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_job_fails_when_agent_adds_too_few_meals(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_size(monkeypatch, 2)
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "Requested 2 meals, but the agent added 1."
    assert job_meals(session, job) == []


def test_job_fails_when_agent_exits_with_error(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=0, exit_code=3)

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "exited with code 3" in job.error


def test_job_fails_when_agent_stops_abnormally(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1, stop_reason="max_turns")

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "'max_turns'" in job.error


def test_job_fails_when_its_meals_cannot_be_saved(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    def fail_to_save(*_args: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr("castle_chef.worker.add_meals", fail_to_save)

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "Could not save the result" in job.error
    assert job_meals(session, job) == []


def test_job_already_claimed_is_left_alone(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A duplicate delivery: another task already claimed this job.
    use_fake_agent(monkeypatch, meals=1)
    job.status = JobStatus.RUNNING
    session.add(job)
    session.commit()

    run_job(job)

    assert reload(session, job).status == JobStatus.RUNNING
    assert not (tmp_path / "jobs" / str(job.id)).exists()


def test_a_job_discarded_before_it_starts_ends_quietly(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A member discarded the draft while its batch was still waiting for a worker.
    use_fake_agent(monkeypatch, meals=1)
    job_id = job.id
    order = session.get(GroceryOrder, job.grocery_order_id)
    assert order is not None
    orders.discard(session, order.household_id, order.id, order.created_by)

    result = runner.invoke(app, ["job", "run", "--job-id", str(job_id)])

    assert result.exit_code == 0, result.output
    events = log_events(tmp_path / "logs")
    assert "job.discarded" in [event["event"] for event in events]
    assert [event for event in events if event["level"] == "error"] == []
    assert not (tmp_path / "jobs" / str(job_id)).exists()  # the agent never ran


def test_a_job_discarded_just_after_it_was_claimed_ends_quietly(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job_id = job.id
    order = session.get(GroceryOrder, job.grocery_order_id)
    assert order is not None

    def claim_then_discard(claim_session: Session, claimed_id: UUID) -> Job | None:
        claimed = claim_job(claim_session, claimed_id)
        orders.discard(session, order.household_id, order.id, order.created_by)
        return claimed

    monkeypatch.setattr(worker, "claim_job", claim_then_discard)

    run_job(job)

    events = log_events(tmp_path / "logs")
    assert "job.discarded" in [event["event"] for event in events]
    assert [event for event in events if event["level"] == "error"] == []
    assert not (tmp_path / "jobs" / str(job_id)).exists()  # the agent never ran


def test_meals_are_discarded_if_the_job_was_failed_meanwhile(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The job was marked failed as stale while its agent was still running.
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job

    def process_then_go_stale(claimed: Job) -> list[MealInput]:
        meals = real_process_job(claimed)
        with Session(get_engine()) as other:
            fail_job(other, claimed.id, "stale")
        return meals

    monkeypatch.setattr(worker, "process_job", process_then_go_stale)

    run_job(job)

    job = reload(session, job)
    assert (job.status, job.error) == (JobStatus.FAILED, "stale")
    assert job_meals(session, job) == []


def test_a_job_whose_draft_was_discarded_while_it_ran_ends_quietly(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job
    household_id, order_id, job_id = draft.household_id, draft.id, job.id

    def process_then_discard(claimed: Job) -> list[MealInput]:
        meals = real_process_job(claimed)
        with Session(get_engine()) as member:  # a member discards the draft meanwhile
            orders.discard(member, household_id, order_id, uuid4())
        return meals

    monkeypatch.setattr(worker, "process_job", process_then_discard)

    with caplog.at_level(logging.INFO):
        run_job(job, expected_exit=0)

    session.expire_all()
    assert session.get(GroceryOrder, order_id) is None
    assert session.get(Job, job_id) is None
    assert session.exec(select(Meal).where(Meal.job_id == job_id)).all() == []
    errors = [e["event"] for e in log_events(tmp_path / "logs") if e["level"] == "error"]
    assert errors == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_agent_timeout_stops_everything_the_agent_started(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawner = Path(__file__).parent / "fake_agent_spawner.py"
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {spawner}")
    monkeypatch.setenv("CASTLE_CHEF_AGENT_TIMEOUT_SECONDS", "2")

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "timed out" in job.error
    child_pid = int((tmp_path / "jobs" / str(job.id) / "child.pid").read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # the agent's own child is gone too


def test_parallel_jobs_wait_for_the_agent_lock(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "agent.lock"
    monkeypatch.setenv("CASTLE_CHEF_AGENT_LOCK_FILE", str(lock_path))
    use_fake_agent(monkeypatch, meals=1)
    agent_started = tmp_path / "jobs" / str(job.id) / "fake-agent-prompt.md"

    with lock_path.open("a") as other_job:  # another job's agent is running
        fcntl.flock(other_job, fcntl.LOCK_EX)
        waiting = threading.Thread(target=worker.run_job, args=(job.id,))
        waiting.start()
        time.sleep(1)
        assert not agent_started.exists()
        assert reload(session, job).status == JobStatus.RUNNING
    waiting.join(timeout=30)

    assert agent_started.exists()
    assert reload(session, job).status == JobStatus.SUCCEEDED


# The prompt.


def suggest(session: Session, job: Job, *meals: tuple[str, str | None]) -> list[Meal]:
    """Save the job's meals (name, description) as its finished batch would."""
    add_meals(
        session,
        job.id,
        [
            MealInput(
                name=name,
                description=description,
                servings=4,
                ingredients=[IngredientInput(name="onions")],
                steps=["Cook."],
            )
            for name, description in meals
        ],
    )
    session.commit()
    return list(
        session.exec(select(Meal).where(Meal.job_id == job.id).order_by(col(Meal.name))).all()
    )


def new_job(session: Session, household_id: UUID) -> Job:
    """A pending batch for a new draft of the household."""
    draft = GroceryOrder(household_id=household_id, created_by=uuid4())
    session.add(draft)
    session.commit()
    pending = Job(
        id=uuid4(), grocery_order_id=draft.id, kind=JobKind.GENERATE_MEALS, requested_by=uuid4()
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    return pending


def test_the_prompt_asks_to_avoid_meals_like_the_households_not_for_us_ones(
    session: Session, job: Job, draft: GroceryOrder, household_id: UUID
) -> None:
    liver, rated_down, tofu = suggest(
        session, job, ("Liver and Onions", "Pan-fried liver."), ("Meatloaf", None), ("Tofu", None)
    )
    for meal, rating in [
        (liver, MealRating.NOT_FOR_US),
        (rated_down, MealRating.DOWN),
        (tofu, MealRating.NOT_FOR_US),
    ]:
        orders.rate_meal(session, household_id, meal.id, rating, None)
    neighbors = Household(name="Neighbors")
    session.add(neighbors)
    session.commit()
    neighbors_job = new_job(session, neighbors.id)
    [neighbors_meal] = suggest(session, neighbors_job, ("Tacos", "Crunchy."))
    orders.rate_meal(session, neighbors.id, neighbors_meal.id, MealRating.NOT_FOR_US, None)
    # The marks outlast the draft they were made on.
    orders.discard(session, household_id, draft.id, draft.created_by)

    prompt = worker.generate_meals_prompt(session, new_job(session, household_id), 5)

    assert (
        'The family marked these meals "don\'t show meals like this". '
        "Don't suggest meals like them:\n\n"
        "- Liver and Onions: Pan-fried liver.\n"
        "- Tofu\n\n"
        "Add each meal"
    ) in prompt
    assert "Meatloaf" not in prompt
    assert "Tacos" not in prompt


def test_the_prompt_lists_the_same_idea_marked_twice_once(
    session: Session, job: Job, household_id: UUID
) -> None:
    both = suggest(
        session, job, ("Liver and Onions", "Pan-fried liver."), ("liver and onions", None)
    )
    for meal in both:
        orders.rate_meal(session, household_id, meal.id, MealRating.NOT_FOR_US, None)

    prompt = worker.generate_meals_prompt(session, job, 5)

    assert prompt.lower().count("liver and onions") == 1


def test_the_prompt_has_no_meals_to_avoid_when_none_are_marked(
    session: Session, job: Job, household_id: UUID
) -> None:
    [meal] = suggest(session, job, ("Meatloaf", None))
    orders.rate_meal(session, household_id, meal.id, MealRating.DOWN, None)

    prompt = worker.generate_meals_prompt(session, job, 5)

    assert prompt.startswith(
        "Create 5 distinct meals for a family. Every meal must be low effort to cook.\n\n"
        "Add each meal"
    )
    assert "Meatloaf" not in prompt


# Shopping lists.

STALE_AFTER = (timedelta(minutes=5), timedelta(hours=1))


def picked(session: Session, job: Job, *servings: int) -> list[Meal]:
    """The batch's meals, each picked for the draft at these servings (their
    recipes serve 4: 1.5 lb sausage, 3 bell peppers, sliced)."""
    add_meals(
        session,
        job.id,
        [
            MealInput(
                name=f"Meal {index}",
                servings=4,
                ingredients=[
                    IngredientInput(name="Italian sausage", quantity=1.5, unit="lb"),
                    IngredientInput(name="bell peppers", quantity=3, preparation="sliced"),
                    IngredientInput(name="salt"),
                ],
                steps=["Cook."],
            )
            for index in range(len(servings))
        ],
    )
    meals = list(
        session.exec(select(Meal).where(Meal.job_id == job.id).order_by(col(Meal.name))).all()
    )
    for meal, count in zip(meals, servings, strict=True):
        session.add(
            GroceryOrderMeal(grocery_order_id=job.grocery_order_id, meal_id=meal.id, servings=count)
        )
    session.commit()
    return meals


def start_list(session: Session, draft: GroceryOrder) -> Job:
    """Start building the draft's list, as the API does."""
    order = session.get_one(GroceryOrder, draft.id)
    build, created = submit_build_list(
        session,
        order,
        draft.created_by,
        uuid4(),
        shopping.current_source(session, draft.id),
        STALE_AFTER,
    )
    assert created
    return build


def list_items(session: Session, build: Job) -> list[ShoppingListItem]:
    session.expire_all()
    return shopping.items_of(session, build.id)


def test_a_list_is_built_from_the_picks_scaled_to_their_servings(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first, second = picked(session, job, 2, 6)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)

    run_job(build)

    prompt = (tmp_path / "jobs" / str(build.id) / "fake-agent-prompt.md").read_text()
    # Stage 1: each recipe (for 4) scaled to its servings, meals numbered in the list's order.
    source = shopping.current_source(session, draft.id)
    numbered = {pick.meal_id: number for number, pick in enumerate(source.picks, start=1)}
    assert (
        f"{numbered[first.id]}. Meal 0 (for 2):\n"
        "- 0.75 lb Italian sausage\n- 1.5 bell peppers, sliced\n- salt"
    ) in prompt
    assert (
        f"{numbered[second.id]}. Meal 1 (for 6):\n"
        "- 2.25 lb Italian sausage\n- 4.5 bell peppers, sliced\n- salt"
    ) in prompt
    assert "salt, pepper, olive oil, vegetable oil, water" in prompt
    assert reload(session, build).status == JobStatus.SUCCEEDED
    items = list_items(session, build)
    assert [(item.name, item.on_hand, sorted(item.meal_ids)) for item in items] == [
        ("item 0", False, sorted([first.id, second.id])),
        ("item 1", False, sorted([first.id, second.id])),
        ("salt", True, sorted([first.id, second.id])),
    ]


def test_whoever_started_the_list_hears_when_it_is_ready(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    sender: RecordingSender,
) -> None:
    phone = with_phone(session, job)
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=3)

    run_job(build)

    [(token, notification)] = sender.sent
    assert (token, notification.kind, notification.title, notification.body) == (
        phone,
        NotificationKind.LIST_READY,
        "Your shopping list is ready",
        "3 items for Order 1.",  # the on-hand salt isn't one to buy
    )


def test_a_list_for_picks_that_changed_meanwhile_sends_nothing(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    sender: RecordingSender,
) -> None:
    with_phone(session, job)
    [meal] = picked(session, job, 4)
    build = start_list(session, draft)
    pick = session.get_one(GroceryOrderMeal, (draft.id, meal.id))
    pick.servings = 6
    session.add(pick)
    session.commit()
    use_fake_agent(monkeypatch, meals=1)

    run_job(build)

    assert reload(session, build).status == JobStatus.SUCCEEDED
    assert sender.sent == []


def test_a_superseded_build_saves_nothing(
    session: Session, draft: GroceryOrder, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    [meal] = picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job

    def process_then_change_picks(claimed: Job) -> worker.JobResult:
        result = real_process_job(claimed)
        with Session(get_engine()) as member:  # the member changes servings and leaves again
            pick = member.get_one(GroceryOrderMeal, (draft.id, meal.id))
            pick.servings = 2
            member.add(pick)
            member.commit()
            start_list(member, draft)
        return result

    monkeypatch.setattr(worker, "process_job", process_then_change_picks)

    run_job(build)

    superseded = reload(session, build)
    assert (superseded.status, superseded.error) == (JobStatus.FAILED, SUPERSEDED_ERROR)
    assert list_items(session, build) == []


@pytest.mark.parametrize(
    ("items", "problem"),
    [
        ([{"name": "tortillas", "aisle": "pantry", "meals": [2]}], r"is for meals \[2\]"),
        (
            [{"name": "sea salt", "aisle": "pantry", "meals": [1], "on_hand": True}],
            "isn't assumed on hand",
        ),
        ([{"name": "Olive Oil", "aisle": "pantry", "meals": [1]}], "is assumed on hand"),
        ([{"name": "salt", "aisle": "pantry", "meals": [1], "on_hand": True}], "nothing to buy"),
        (
            [
                {"name": "tortillas", "aisle": "pantry", "meals": [1]},
                {"name": "salt", "aisle": "pantry", "meals": [1], "on_hand": True},
                {"name": "salt", "aisle": "pantry", "meals": [1], "on_hand": True},
            ],
            "on hand twice",
        ),
    ],
    ids=["meal", "not on hand", "on hand bought", "nothing", "twice"],
)
def test_a_list_that_breaks_the_rules_is_refused(items: list[dict[str, Any]], problem: str) -> None:
    source = ListSource(picks=[ListPick(meal_id=uuid4(), servings=4)])

    with pytest.raises(shopping.InvalidList, match=problem):
        shopping.check_items(
            [shopping.ListItemInput.model_validate(item) for item in items], source
        )
