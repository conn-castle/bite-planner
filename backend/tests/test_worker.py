"""The worker, through `castle-chef job run` (or `worker.run_job`) with a fake agent."""

import fcntl
import io
import json
import logging
import os
import sys
import tarfile
import time
from datetime import date, timedelta
from datetime import time as clock
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from castle_chef_agent.checkout import RENEWED_SESSION_FILE, SESSION_FILE, Checkout
from castle_chef_agent.instacart import Product
from castle_chef_agent.meals import Color, Dish, Icon, IngredientInput, MealInput, StepInput
from castle_chef_agent.products import ProductCount
from castle_chef_agent.server import Tools
from castle_chef_agent.shopping import Aisle
from castle_chef_agent.windows import Window
from pydantic import BaseModel
from sqlmodel import Session, col, select
from typer.testing import CliRunner

from castle_chef import booking, orders, push, records, shopping, worker
from castle_chef.checkout import (
    SIGNED_OUT_ERROR,
    Booking,
    BookIssue,
    CartLine,
    CheckoutError,
    Quote,
    QuoteRequest,
    Swap,
)
from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.harness import run_agent
from castle_chef.jobs import SUPERSEDED_ERROR, claim_job, fail_job, submit_build_list
from castle_chef.meals import add_meals
from castle_chef.models import (
    AppUser,
    BookState,
    GroceryOrder,
    GroceryOrderMeal,
    GroceryOrderStatus,
    Household,
    Job,
    JobKind,
    JobStatus,
    Meal,
    MealRating,
    ProductGone,
    PushDevice,
    ShoppingListItem,
    utc_now,
)
from castle_chef.push import Notification, NotificationKind, Outcome, Sent
from castle_chef.settings import get_settings
from tests.conftest import BACKEND_DIR, OTHER_ADDRESS, TEST_ADDRESS

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def use_fake_agent(
    monkeypatch: pytest.MonkeyPatch, meals: int, stop_reason: str = "end_turn", exit_code: int = 0
) -> None:
    monkeypatch.setenv(
        "CASTLE_CHEF_AGENT_COMMAND",
        f"{sys.executable} {FAKE_AGENT} {meals} {stop_reason} {exit_code} 0",
    )
    get_settings.cache_clear()


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
    assert reload(session, job).error == "The agent didn't finish: 0 of 20 meals are added."


def test_agent_environment_has_no_database_credentials(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    env = json.loads((tmp_path / "jobs" / str(job.id) / "fake-agent-env.json").read_text())
    assert "DATABASE_URL" not in env
    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_the_agents_tools_start_from_the_worker_images_own_castle_chef(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # In the image, the `castle-chef` on the agent's PATH is docker/castle-chef (the agent's
    # app alone, not the backend's): the tools the worker names must start from it.
    image_launcher = BACKEND_DIR.parent / "docker" / "castle-chef"
    _, body = image_launcher.read_text().split("\n", 1)  # its shebang is the image's Python
    launcher = tmp_path / "agent-bin" / "castle-chef"
    launcher.parent.mkdir()
    launcher.write_text(f"#!{sys.executable}\n{body}")
    launcher.chmod(0o755)
    monkeypatch.setenv("CASTLE_CHEF_AGENT_PATH", f"{launcher.parent}{os.pathsep}{os.defpath}")
    batch_size(monkeypatch, 2)
    use_fake_agent(monkeypatch, meals=2)

    run_job(job)

    job = reload(session, job)
    assert (job.status, job.error) == (JobStatus.SUCCEEDED, None)
    assert len(job_meals(session, job)) == 2


def test_job_fails_when_agent_adds_too_few_meals(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_size(monkeypatch, 2)
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "The agent didn't finish: 1 of 2 meals are added."
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

    def process_then_go_stale(claimed: Job) -> worker.JobResult:
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

    def process_then_discard(claimed: Job) -> worker.JobResult:
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
                dish=Dish.PLATE,
                ingredients=[IngredientInput(name="onions", icon=Icon.ROUND, color=Color.CREAM)],
                steps=[StepInput(text="Cook.")],
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
                dish=Dish.SKILLET,
                ingredients=[
                    IngredientInput(
                        name="Italian sausage",
                        quantity=1.5,
                        unit="lb",
                        icon=Icon.GROUND_MEAT,
                        color=Color.BROWN,
                    ),
                    IngredientInput(
                        name="bell peppers",
                        quantity=3,
                        preparation="sliced",
                        icon=Icon.PEPPER,
                        color=Color.RED,
                    ),
                    IngredientInput(name="salt"),
                ],
                steps=[StepInput(text="Cook.")],
            )
            for index in range(len(servings))
        ],
    )
    meals = list(
        session.exec(select(Meal).where(Meal.job_id == job.id).order_by(col(Meal.name))).all()
    )
    for meal, count in zip(meals, servings, strict=True):
        assert job.grocery_order_id is not None
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
    # Stage 1: each recipe (for 4) scaled to its servings, meals numbered in the
    # list's order, each line with its id.
    source = shopping.current_source(session, draft.id)
    numbered = {pick.meal_id: number for number, pick in enumerate(source.picks, start=1)}
    one, two = numbered[first.id], numbered[second.id]
    assert (
        f"Meal {one}: Meal 0 (for 2)\n"
        f"- {one}a: 0.75 lb Italian sausage\n- {one}b: 1.5 bell peppers, sliced\n- {one}c: salt"
    ) in prompt
    assert (
        f"Meal {two}: Meal 1 (for 6)\n"
        f"- {two}a: 2.25 lb Italian sausage\n- {two}b: 4.5 bell peppers, sliced\n- {two}c: salt"
    ) in prompt
    assert "salt, pepper, olive oil, vegetable oil, water" in prompt
    assert "- other: anything none of the others fits" in prompt
    assert reload(session, build).status == JobStatus.SUCCEEDED
    items = list_items(session, build)
    assert [(item.name, item.on_hand, sorted(item.meal_ids)) for item in items] == [
        ("item 0", False, sorted([first.id, second.id])),
        ("item 1", False, sorted([first.id, second.id])),
        ("salt", True, sorted([first.id, second.id])),
    ]


def test_whoever_started_the_list_hears_when_it_is_shopped(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    sender: RecordingSender,
    codex_home: Path,
) -> None:
    phone = with_phone(session, job)
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)

    run_job(build)

    [(token, notification)] = sender.sent
    assert (token, notification.kind, notification.title, notification.body) == (
        phone,
        NotificationKind.LIST_READY,
        "Your shopping list is ready",
        "2 products for Order 1.",  # the salt, a staple, isn't one to buy
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


def test_a_list_the_agent_did_not_finish_fails_the_job(
    session: Session, draft: GroceryOrder, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=0)  # adds only the salt

    run_job(build)

    failed = reload(session, build)
    assert failed.status == JobStatus.FAILED
    assert failed.error == (
        "Not covered yet: 1a (1.5 lb Italian sausage); 1b (3 bell peppers, sliced). "
        "The list has nothing to buy."
    )
    assert list_items(session, build) == []


# Shopping a list (stage 3): right after it's built, in the same run.


def shop_of(session: Session, build: Job) -> Job:
    session.expire_all()
    [shop] = session.exec(select(Job).where(Job.build_id == build.id)).all()
    return shop


def test_a_built_list_is_shopped_right_after(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    codex_home: Path,
) -> None:
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)

    run_job(build)

    shop = shop_of(session, build)
    assert (shop.kind, shop.status, shop.requested_by) == (
        JobKind.SHOP,
        JobStatus.SUCCEEDED,
        build.requested_by,
    )
    prompt = (tmp_path / "jobs" / str(shop.id) / "fake-agent-prompt.md").read_text()
    # What to buy first (the items, their amounts, and their meals), then the
    # meals, for reference.
    assert "## Shopping list\n\n1. item 0: 2 (Meal 0)\n2. item 1: 2 (Meal 0)\n" in prompt
    assert "## Meals" in prompt
    assert "Meal 0, 4 servings: 1.5 lb Italian sausage; 3 bell peppers, sliced; salt" in prompt
    # Each item to buy gets its product and alternatives; the staple gets none.
    items = list_items(session, build)
    assert [(i.name, i.product.product.name if i.product else None) for i in items] == [
        ("item 0", "Wegmans Onions, Yellow"),
        ("item 1", "Wegmans Onions, Yellow"),
        ("salt", None),
    ]
    assert [a.product.name for a in items[0].alternatives] == ["Sweet Onions"]
    assert [item.more_alternatives_query for item in items] == ["item 0", "item 1", None]
    assert len(items[0].more_alternatives) == 5
    assert not items[2].more_alternatives
    # Broad searches run automatically for every item before the model's searches.
    requests = [
        json.loads(line) for line in (codex_home / "requests.jsonl").read_text().splitlines()
    ]
    searches = [
        r["params"]["arguments"]["query"]
        for r in requests
        if r.get("method") == "mcpServer/tool/call"
        and r["params"]["tool"] == "instacart.search_products"
    ]
    assert searches == ["item 0", "item 1", "item 0", "item 1"]
    assert items[0].product is not None and items[0].product.count == 1
    assert not any(item.not_found for item in items)


def test_shopping_holds_its_job_lock_before_starting_the_agent(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    codex_home: Path,
) -> None:
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)
    real_run_agent = run_agent
    saw_products_lock = False

    def checked_run_agent(
        job_dir: Path,
        prompt: str,
        tools: Tools,
        context: BaseModel,
        tool_args: list[str] | None = None,
    ) -> None:
        nonlocal saw_products_lock
        if tools is Tools.PRODUCTS:
            with (codex_home / "job.lock").open("a") as other, pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            saw_products_lock = True
        real_run_agent(job_dir, prompt, tools, context, tool_args)

    monkeypatch.setattr(worker, "run_agent", checked_run_agent)

    run_job(build)

    assert saw_products_lock
    assert shop_of(session, build).status == JobStatus.SUCCEEDED


def test_shopping_without_an_instacart_connection_fails_but_keeps_the_list(
    session: Session, draft: GroceryOrder, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)

    run_job(build)

    assert reload(session, build).status == JobStatus.SUCCEEDED
    shop = shop_of(session, build)
    assert shop.status == JobStatus.FAILED
    assert shop.error is not None and "No Codex login here" in shop.error
    assert [item.product for item in list_items(session, build)] == [None, None, None]


def test_a_superseded_build_isnt_shopped(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    codex_home: Path,
) -> None:
    [meal] = picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job

    def process_then_change_picks(claimed: Job) -> worker.JobResult:
        result = real_process_job(claimed)
        if claimed.kind == JobKind.BUILD_LIST:
            with Session(get_engine()) as member:
                pick = member.get_one(GroceryOrderMeal, (draft.id, meal.id))
                pick.servings = 2
                member.add(pick)
                member.commit()
                start_list(member, draft)
        return result

    monkeypatch.setattr(worker, "process_job", process_then_change_picks)

    run_job(build)

    session.expire_all()
    assert session.exec(select(Job).where(Job.build_id == build.id)).all() == []


def test_shopping_a_list_replaced_meanwhile_saves_nothing(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    codex_home: Path,
) -> None:
    [meal] = picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job

    def shop_then_change_picks(claimed: Job) -> worker.JobResult:
        result = real_process_job(claimed)
        if claimed.kind == JobKind.SHOP:  # the member changes servings while it shops
            with Session(get_engine()) as member:
                pick = member.get_one(GroceryOrderMeal, (draft.id, meal.id))
                pick.servings = 2
                member.add(pick)
                member.commit()
                start_list(member, draft)
        return result

    monkeypatch.setattr(worker, "process_job", shop_then_change_picks)

    run_job(build)

    shop = shop_of(session, build)
    assert (shop.status, shop.error) == (JobStatus.FAILED, SUPERSEDED_ERROR)
    assert [item.product for item in list_items(session, build)] == [None, None]


def test_a_changed_address_discards_inflight_shopping_results(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    codex_home: Path,
) -> None:
    from castle_chef import delivery

    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=1)
    real_process_job = worker.process_job

    def shop_then_change_address(claimed: Job) -> worker.JobResult:
        result = real_process_job(claimed)
        if claimed.kind == JobKind.SHOP:
            with Session(get_engine()) as member:
                household = member.get_one(Household, draft.household_id)
                household.delivery_address = OTHER_ADDRESS
                member.add(household)
                member.commit()
        return result

    monkeypatch.setattr(worker, "process_job", shop_then_change_address)
    run_job(build)

    shop = shop_of(session, build)
    assert (shop.status, shop.error) == (JobStatus.FAILED, delivery.ADDRESS_CHANGED)
    assert all(item.product is None for item in list_items(session, build))


def test_whoever_is_waiting_hears_when_shopping_fails(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    monkeypatch: pytest.MonkeyPatch,
    sender: RecordingSender,
) -> None:
    phone = with_phone(session, job)
    picked(session, job, 4)
    build = start_list(session, draft)
    use_fake_agent(monkeypatch, meals=2)

    run_job(build)  # no Instacart connection here: shopping fails

    [(token, notification)] = sender.sent
    assert (token, notification.kind, notification.title, notification.body) == (
        phone,
        NotificationKind.LIST_FAILED,
        "Couldn't shop your list",
        "Shopping Order 1's list at Wegmans stopped. Open it to try again.",
    )


# Quotes: the order's total, read at checkout by the agent, in a browser (on
# stand-ins for Instacart's pages: fixtures/checkout).

SESSION: dict[str, Any] = {"cookies": [], "origins": []}


class RecordingSessions:
    """The household's Instacart session, in place of SSM."""

    def __init__(self) -> None:
        self.saved: list[list[dict[str, Any]]] = []

    def load(self) -> dict[str, Any]:
        return SESSION

    def save(self, cookies: list[dict[str, Any]]) -> None:
        self.saved.append(list(cookies))


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> RecordingSessions:
    recording = RecordingSessions()
    monkeypatch.setattr(worker, "session_store", lambda: recording)
    return recording


def quote_job(session: Session, draft: GroceryOrder, *lines: tuple[str, float]) -> Job:
    request = QuoteRequest(
        lines=[CartLine(product_id=pid, quantity=quantity) for pid, quantity in lines],
        tip_percent=10,
        tip_max_cents=1500,
        delivery_address=TEST_ADDRESS,
    )
    pending = Job(
        id=uuid4(),
        grocery_order_id=draft.id,
        kind=JobKind.QUOTE,
        requested_by=draft.created_by,
        quote_request=request,
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    return pending


def start_cart(codex_home: Path, items: dict[str, float], **config: Any) -> None:
    lines = [{"product_id": pid, "quantity": quantity} for pid, quantity in items.items()]
    (codex_home / "fake.json").write_text(json.dumps({"cart": lines, **config}))


def cart_updates(codex_home: Path) -> list[list[dict[str, Any]]]:
    """Each change the worker made to the cart, in order."""
    return [
        request["params"]["arguments"]["item_updates"]
        for request in map(json.loads, (codex_home / "requests.jsonl").read_text().splitlines())
        if request.get("method") == "mcpServer/tool/call"
        and "item_updates" in request["params"]["arguments"]
    ]


def test_the_agent_reads_checkout_for_the_orders_products_with_the_households_items_set_aside(
    session: Session,
    draft: GroceryOrder,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    start_cart(codex_home, {"17499438": 1})  # the household's own onions
    quote = quote_job(session, draft, ("3371870", 5), ("777", 2))
    use_fake_agent(monkeypatch, meals=1)

    run_job(quote)

    done = reload(session, quote)
    assert done.status == JobStatus.SUCCEEDED, done.error
    assert done.quote is not None and done.quote.checkout is not None
    read = done.quote.checkout
    # The stand-in checkout: $19.59 of items, $2.49 service, and the rule's tip (10%).
    assert (read.items_cents, read.tip_cents, read.total_cents) == (1959, 196, 2404)
    assert (read.card, read.replacements) == ("Visa ending 4242", ["Replace with best match"])
    prompt = (tmp_path / "jobs" / str(quote.id) / "fake-agent-prompt.md").read_text()
    assert "Open `/store/wegmans/storefront`" in prompt
    # The household's onions out, the order's products in, then all put back.
    assert cart_updates(codex_home) == [
        [
            {"product_id": "17499438", "quantity": 0},
            {"product_id": "3371870", "quantity": 5},
            {"product_id": "777", "quantity": 2},
        ],
        [
            {"product_id": "3371870", "quantity": 0},
            {"product_id": "777", "quantity": 0},
            {"product_id": "17499438", "quantity": 1},
        ],
    ]
    # The renewed session is saved, and no copy of it stays anywhere.
    assert len(sessions.saved) == 1
    assert not (tmp_path / f"session-{quote.id}").exists()
    assert not list((tmp_path / "jobs" / str(quote.id)).glob("session*"))


def test_a_cart_below_the_stores_minimum_is_a_quote_that_says_so(
    session: Session,
    draft: GroceryOrder,
    codex_home: Path,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    quote = quote_job(session, draft, ("17499438", 1))  # $2.29
    use_fake_agent(monkeypatch, meals=1)

    run_job(quote)

    done = reload(session, quote)
    assert (done.status, done.quote) == (
        JobStatus.SUCCEEDED,
        Quote(blocked_because="$10 Min. to checkout"),
    )
    assert sessions.saved == []  # checkout isn't opened


def test_a_cart_instacart_wont_check_out_for_another_reason_fails_the_quote_with_it(
    session: Session,
    draft: GroceryOrder,
    codex_home: Path,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {}, refuse_checkout="Store is closed")
    quote = quote_job(session, draft, ("3371870", 10))
    use_fake_agent(monkeypatch, meals=1)

    run_job(quote)

    failed = reload(session, quote)
    assert (failed.status, failed.quote) == (JobStatus.FAILED, None)
    assert failed.error == "Instacart won't check this cart out: Store is closed"


def test_a_signed_out_session_fails_the_quote_saying_so_and_isnt_saved(
    session: Session,
    draft: GroceryOrder,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    quote = quote_job(session, draft, ("3371870", 5))
    use_fake_agent(monkeypatch, meals=0)  # finds Instacart asking to sign in

    run_job(quote)

    failed = reload(session, quote)
    assert (failed.status, failed.error) == (JobStatus.FAILED, SIGNED_OUT_ERROR)
    assert sessions.saved == []
    assert json.loads((codex_home / "cart-state.json").read_text()) == {}


def test_an_agent_that_doesnt_report_checkout_fails_the_quote(
    session: Session,
    draft: GroceryOrder,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {"17499438": 1})
    quote = quote_job(session, draft, ("3371870", 5), ("777", 2))
    use_fake_agent(monkeypatch, meals=1, stop_reason="max_turns")

    run_job(quote)

    failed = reload(session, quote)
    assert failed.status == JobStatus.FAILED
    assert failed.error is not None and "max_turns" in failed.error
    assert json.loads((codex_home / "cart-state.json").read_text()) == {"17499438": 1}


# Booking an approved order: the cart filled with its products, the window the
# rule chooses, checkout reported and checked, and Place order pressed once (on
# the stand-in pages; development's dry run unless the test says otherwise).

ADDRESS = "1 Main Street Rochester, NY 14604"
CARD = "Visa ending 4242"


def product(product_id: str, name: str, count: int = 2) -> ProductCount:
    return ProductCount(
        product=Product(
            product_id=product_id,
            name=name,
            size=None,
            price_cents=500,
            price_per_lb_cents=None,
            estimated_lb=None,
            image_url=None,
            bought_before=False,
        ),
        count=count,
    )


def approved(
    session: Session,
    draft: GroceryOrder,
    batch: Job,
    *,
    alternatives: list[ProductCount] | None = None,
    by: clock = clock(16),
    card: str = CARD,
) -> Job:
    """The draft approved, its booking started (as the API does): picked meals,
    a list buying sweet onions (3371870, five) and chicken (777, two; with these
    alternatives), its total read for that address and card, delivery in three
    days by `by`. Returns the book job."""
    picked(session, batch, 4)
    build = Job(
        id=uuid4(),
        grocery_order_id=draft.id,
        kind=JobKind.BUILD_LIST,
        requested_by=draft.created_by,
        status=JobStatus.SUCCEEDED,
        list_source=shopping.current_source(session, draft.id),
    )
    session.add(build)
    session.flush()
    for position, (name, chosen, others) in enumerate(
        [
            ("sweet onions", product("3371870", "Sweet Onions", count=5), []),
            ("chicken thighs", product("777", "Chicken Thighs"), alternatives or []),
        ]
    ):
        session.add(
            ShoppingListItem(
                job_id=build.id,
                position=position,
                name=name,
                quantity=2,
                aisle=Aisle.PRODUCE,
                meal_ids=[],
                product=chosen,
                alternatives=others,
            )
        )
    quote = Job(
        id=uuid4(),
        grocery_order_id=draft.id,
        kind=JobKind.QUOTE,
        requested_by=draft.created_by,
        status=JobStatus.SUCCEEDED,
        quote_request=QuoteRequest(
            lines=[
                CartLine(product_id="3371870", quantity=5),
                CartLine(product_id="777", quantity=2),
            ],
            tip_percent=10,
            tip_max_cents=1500,
            delivery_address=TEST_ADDRESS,
        ),
        quote=Quote(
            checkout=Checkout(
                items_cents=2000,
                fees=[],
                tip_cents=200,
                total_cents=2200,
                address=ADDRESS,
                card=card,
                replacements=["Replace with best match"],
            )
        ),
    )
    session.add(quote)
    session.flush()
    order = session.get_one(GroceryOrder, draft.id)
    order.status, order.placed_at, order.placed_by = (
        GroceryOrderStatus.PLACED,
        utc_now(),
        draft.created_by,
    )
    order.delivery_date = BOOK_DAY
    order.delivery_by = by
    order.next_delivery_date = BOOK_DAY + timedelta(days=4)
    order.quote_id, order.book_state = quote.id, BookState.WAITING
    session.add(order)
    session.commit()
    return booking.start(session, session.get_one(GroceryOrder, draft.id))


BOOK_DAY = date.today() + timedelta(days=3)


def booked_order(session: Session, book: Job) -> GroceryOrder:
    session.expire_all()
    return session.get_one(GroceryOrder, book.grocery_order_id)


def use_booking(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("CASTLE_CHEF_INSTACART_BOOKING", mode)
    get_settings.cache_clear()


def test_development_books_as_a_dry_run_that_shows_as_booked_and_puts_the_cart_back(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    sender: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    start_cart(codex_home, {"17499438": 1})  # the household's own onions
    book = approved(session, draft, job)
    token = with_phone(session, book)
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    assert reload(session, book).status == JobStatus.SUCCEEDED
    order = booked_order(session, book)
    assert (order.book_state, order.clicked_at, order.book_job_id) == (BookState.BOOKED, None, None)
    # The window the rule chose (by 4:00 PM: 2-4 PM), and checkout's total.
    assert order.booking == Booking(
        order_number="Dry run",
        window=Window(start=clock(14), end=clock(16)),
        total_cents=1959 + 249 + 196,
    )
    prompt = (tmp_path / "jobs" / str(book.id) / "fake-agent-prompt.md").read_text()
    assert f"for delivery on {BOOK_DAY:%A, %b} {BOOK_DAY.day} by 4:00 PM" in " ".join(
        prompt.split()
    )
    assert json.loads((codex_home / "cart-state.json").read_text()) == {"17499438": 1}
    assert len(sessions.saved) == 1
    [(sent_to, notification)] = sender.sent
    assert (sent_to, notification.kind, notification.order_id) == (
        token,
        NotificationKind.BOOKING,
        draft.id,
    )
    assert notification.title.endswith("is booked (dry run)")
    assert notification.body.startswith(f"Arrives {BOOK_DAY:%A, %b} {BOOK_DAY.day}, 2–4 PM.")
    # The log says what Instacart confirmed.
    [settled] = [e for e in log_events(tmp_path / "logs") if e["event"] == "booking.settled"]
    assert settled == settled | {
        "state": "booked",
        "instacart_order": "Dry run",
        "window": {"start": "14:00:00", "end": "16:00:00"},
        "total_cents": 1959 + 249 + 196,
        "swaps": 0,
    }


def test_booking_keeps_its_approved_address_after_settings_changes(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    household = session.get_one(Household, draft.household_id)
    household.delivery_address = OTHER_ADDRESS
    session.add(household)
    session.commit()
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    assert reload(session, book).status == JobStatus.SUCCEEDED
    assert booked_order(session, book).book_state == BookState.BOOKED
    assert (codex_home / "selected-address.txt").read_text() == "101"


def test_a_live_booking_records_the_click_before_pressing_and_keeps_the_confirmation(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=1)
    claims: list[GroceryOrder] = []
    claim = booking.claim_click

    def recorded(db: Session, job_id: UUID) -> bool:
        claimed = claim(db, job_id)
        claims.append(booked_order(session, book))
        return claimed

    monkeypatch.setattr(booking, "claim_click", recorded)

    run_job(book)

    # Recorded (committed) before the tools pressed it.
    [at_claim] = claims
    assert at_claim.clicked_at is not None and at_claim.book_state == BookState.BOOKING
    order = booked_order(session, book)
    assert order.book_state == BookState.BOOKED
    assert order.clicked_at == at_claim.clicked_at
    assert order.booking == Booking(
        order_number="18234567",
        window=Window(start=clock(14), end=clock(16)),
        total_cents=1959 + 249 + 196,
    )


def test_a_confirmed_booking_stays_booked_when_the_renewed_session_cant_be_saved(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class UnsavedSessions(RecordingSessions):
        def save(self, cookies: list[dict[str, Any]]) -> None:
            raise CheckoutError("Couldn't save the Instacart session to SSM: throttled")

    monkeypatch.setattr(worker, "session_store", UnsavedSessions)
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    assert reload(session, book).status == JobStatus.SUCCEEDED
    order = booked_order(session, book)
    assert order.book_state == BookState.BOOKED
    assert order.booking is not None and order.booking.order_number == "18234567"
    [unsaved] = [
        e for e in log_events(tmp_path / "logs") if e["event"] == "instacart.session_not_saved"
    ]
    assert unsaved["level"] == "error" and "throttled" in unsaved["error"]


def test_booking_while_its_turned_off_stops_before_place_order_and_isnt_booked(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_booking(monkeypatch, "off")
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    assert (order.book_state, order.clicked_at, order.booking) == (BookState.NOT_BOOKED, None, None)
    assert order.book_issue == BookIssue(message="Booking at Instacart isn't turned on yet.")


def test_a_day_with_no_window_left_for_the_time_needs_another_day_or_time(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job, by=clock(9))  # the stand-in's start at 10 AM
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.NEEDS_TIME
    assert order.book_issue == BookIssue(
        message=f"No delivery window is left on {BOOK_DAY:%a, %b} {BOOK_DAY.day} for the time "
        "it needs."
    )


def test_a_gone_product_is_swapped_for_its_first_alternative_the_cart_takes(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {}, unavailable=["777", "888"])
    book = approved(
        session,
        draft,
        job,
        alternatives=[product("888", "Chicken Breasts"), product("999", "Chicken Legs")],
    )
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.BOOKED and order.booking is not None
    assert order.booking.swaps == [
        Swap(item="chicken thighs", gone="Chicken Thighs", bought="Chicken Legs")
    ]
    # The list keeps what was bought; the one gone is an alternative now.
    [chicken] = [item for item in list_items_of(session, draft) if item.name == "chicken thighs"]
    assert chicken.product is not None and chicken.product.product.product_id == "999"
    assert [a.product.product_id for a in chicken.alternatives] == ["888", "777"]
    assert json.loads((codex_home / "cart-state.json").read_text()) == {}


def test_a_gone_product_with_the_household_asking_needs_one_chosen(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    start_cart(codex_home, {}, unavailable=["777"])
    household = session.get_one(Household, draft.household_id)
    household.product_gone = ProductGone.ASK
    session.add(household)
    session.commit()
    book = approved(session, draft, job, alternatives=[product("999", "Chicken Legs")])
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    [chicken] = [item for item in list_items_of(session, draft) if item.name == "chicken thighs"]
    assert order.book_state == BookState.NEEDS_PRODUCT
    assert order.book_issue == BookIssue(
        message="Wegmans doesn't have Chicken Thighs any more.", item_ids=[chicken.id]
    )
    # Nothing was opened at checkout.
    assert not (tmp_path / "jobs" / str(book.id) / "fake-agent-prompt.md").exists()
    assert json.loads((codex_home / "cart-state.json").read_text()) == {}


def test_a_card_changed_since_the_order_was_approved_isnt_booked(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job, card="Amex ending 0005")
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    assert (order.book_state, order.clicked_at) == (BookState.NOT_BOOKED, None)
    assert order.book_issue == BookIssue(
        message="Instacart now charges Visa ending 4242, not Amex ending 0005. "
        "Check your Instacart account."
    )


def test_a_signed_out_session_isnt_booked_and_says_so(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_fake_agent(monkeypatch, meals=0)

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.NOT_BOOKED
    assert order.book_issue == BookIssue(message=SIGNED_OUT_ERROR)
    assert sessions.saved == []


def test_a_press_with_no_confirmation_needs_someone_to_check_instacart(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    sender: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    with_phone(session, book)
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=3)  # finds no confirmation

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.CHECK_INSTACART and order.clicked_at is not None
    [(_, notification)] = sender.sent
    assert notification.title.startswith("Check Instacart for Order")


def test_once_place_order_was_pressed_an_agent_that_stops_badly_still_books_what_it_confirmed(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=1, stop_reason="max_turns")

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.BOOKED
    assert order.booking is not None and order.booking.order_number == "18234567"


def test_a_booking_that_fails_before_place_order_isnt_booked(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_fake_agent(monkeypatch, meals=1, exit_code=1)

    run_job(book)

    order = booked_order(session, book)
    assert (order.book_state, order.clicked_at) == (BookState.NOT_BOOKED, None)
    assert order.book_issue is not None and "Agent exited with code 1" in order.book_issue.message


def list_items_of(session: Session, draft: GroceryOrder) -> list[ShoppingListItem]:
    session.expire_all()
    build = shopping.newest_build(session, draft.id)
    assert build is not None
    return shopping.items_of(session, build.id)


def test_a_gone_product_bought_for_more_than_one_item_needs_one_chosen(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {}, unavailable=["777"])
    book = approved(session, draft, job, alternatives=[product("999", "Chicken Legs")])
    # The onions' item buys the chicken too: one alternative's count can't stand in for both.
    [onions] = [item for item in list_items_of(session, draft) if item.name == "sweet onions"]
    onions.product = product("777", "Chicken Thighs", count=5)
    session.add(onions)
    session.commit()
    use_fake_agent(monkeypatch, meals=1)

    run_job(book)

    order = booked_order(session, book)
    assert order.book_state == BookState.NEEDS_PRODUCT
    assert order.book_issue is not None
    assert sorted(order.book_issue.item_ids) == sorted(
        item.id for item in list_items_of(session, draft)
    )


def test_a_booking_whose_result_cant_be_saved_after_the_press_needs_checking(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    instacart_site: str,
    sessions: RecordingSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cart(codex_home, {})
    book = approved(session, draft, job)
    use_booking(monkeypatch, "live")
    use_fake_agent(monkeypatch, meals=1)

    def unsaved(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(booking, "finish", unsaved)

    run_job(book)

    order = booked_order(session, book)
    assert (order.book_state, order.book_job_id) == (BookState.CHECK_INSTACART, None)
    assert reload(session, book).status == JobStatus.FAILED


# Job records.


class RecordingStore:
    def __init__(self, fails: bool = False) -> None:
        self.kept: dict[str, bytes] = {}
        self.fails = fails

    def put(self, key: str, data: bytes) -> None:
        if self.fails:
            raise ConnectionError("S3 is unreachable")
        self.kept[key] = data


def record_files(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {
            member.name: (archive.extractfile(member) or io.BytesIO()).read()
            for member in archive.getmembers()
            if member.isfile()
        }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> RecordingStore:
    recording = RecordingStore()
    monkeypatch.setattr(records, "record_store", lambda: recording)
    return recording


def test_a_jobs_record_keeps_what_its_agent_was_given_and_did(
    job: Job, monkeypatch: pytest.MonkeyPatch, store: RecordingStore
) -> None:
    batch_size(monkeypatch, 1)
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    files = record_files(store.kept[f"jobs/{job.id}.tar.gz"])
    assert b"Create 1 distinct meals" in files["job/fake-agent-prompt.md"]
    assert "job/agent-output.json" in files
    assert "job/tools.json" in files


def test_a_failed_jobs_record_is_kept_too(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, store: RecordingStore
) -> None:
    use_fake_agent(monkeypatch, meals=0, exit_code=3)

    run_job(job)

    assert reload(session, job).status == JobStatus.FAILED
    assert "job/agent-stderr.log" in record_files(store.kept[f"jobs/{job.id}.tar.gz"])


def test_a_record_never_keeps_the_instacart_session(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    (job_dir / "session").mkdir(parents=True)
    (job_dir / "report.json").write_text("{}")
    (job_dir / "session" / SESSION_FILE).write_text('{"cookies": []}')
    (job_dir / RENEWED_SESSION_FILE).write_text('{"cookies": []}')

    data, parts = records.pack(job_dir)

    assert list(record_files(data)) == ["job/report.json"]
    assert parts == ["job"]


def test_a_record_that_cant_be_kept_is_logged_and_the_job_still_succeeds(
    session: Session, job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(records, "record_store", lambda: RecordingStore(fails=True))
    batch_size(monkeypatch, 1)
    use_fake_agent(monkeypatch, meals=1)

    run_job(job)

    assert reload(session, job).status == JobStatus.SUCCEEDED
    [failed] = [e for e in log_events(tmp_path / "logs") if e["event"] == "job.record_failed"]
    assert (failed["level"], failed["final"]) == ("error", True)
    assert "S3 is unreachable" in failed["exception"]


def test_a_jobs_record_is_also_kept_while_it_runs(
    job: Job, monkeypatch: pytest.MonkeyPatch, store: RecordingStore, tmp_path: Path
) -> None:
    # A task that's killed mid-job (out of memory, the time cap) never ends it.
    monkeypatch.setattr(records, "SAVE_EVERY_SECONDS", 0.2)
    batch_size(monkeypatch, 1)
    # An agent that takes a second.
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {FAKE_AGENT} 1 end_turn 0 1")

    run_job(job)

    saved = [e for e in log_events(tmp_path / "logs") if e["event"] == "job.record_saved"]
    assert [e["final"] for e in saved].count(True) == 1 and saved[-1]["final"] is True
    assert False in [e["final"] for e in saved]
    assert saved[-1]["parts"] == ["job"]


@pytest.mark.parametrize("missing", [True, False])
def test_a_saved_address_startup_failure_reaches_the_shopping_error(
    session: Session,
    draft: GroceryOrder,
    job: Job,
    codex_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    from tests.conftest import TEST_ADDRESSES

    picked(session, job, 4)
    build = start_list(session, draft)
    saved = TEST_ADDRESSES[0].model_dump() | {"is_default": False}
    addresses = [] if missing else [saved, saved | {"id": "303"}]
    (codex_home / "fake.json").write_text(json.dumps({"addresses": addresses}))
    use_fake_agent(monkeypatch, meals=1)

    run_job(build)

    shop = shop_of(session, build)
    assert shop.status == JobStatus.FAILED
    assert shop.error is not None and shop.error.startswith("Delivery address:")
    assert ("isn't saved" if missing else "more than one") in shop.error
    assert all(item.product is None for item in list_items(session, build))
    assert not (codex_home / "selected-address.txt").exists()
