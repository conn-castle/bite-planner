"""The worker, through `castle-chef job run` (or `worker.run_job`) with a fake agent."""

import fcntl
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, col, select
from typer.testing import CliRunner

from castle_chef import orders, worker
from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.jobs import fail_job
from castle_chef.meals import MealInput
from castle_chef.models import GroceryOrder, Job, JobStatus, Meal

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
    assert job.error is not None and "Could not save the meals" in job.error
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
    orders.discard(session, order.household_id, order.id)

    result = runner.invoke(app, ["job", "run", "--job-id", str(job_id)])

    assert result.exit_code == 0, result.output
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
            orders.discard(member, household_id, order_id)
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
