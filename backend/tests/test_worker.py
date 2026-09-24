import fcntl
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlmodel import Session, col, select
from typer.testing import CliRunner

from castle_chef import worker
from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.jobs import GENERATE_MEALS, create_job, fail_job
from castle_chef.models import Job, JobMeal, JobStatus, Meal

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def use_fake_agent(
    monkeypatch: pytest.MonkeyPatch, meals: int, stop_reason: str = "end_turn", exit_code: int = 0
) -> None:
    monkeypatch.setenv(
        "CASTLE_CHEF_AGENT_COMMAND",
        f"{sys.executable} {FAKE_AGENT} {meals} {stop_reason} {exit_code} 0",
    )


def run_job(job: Job, expected_exit: int = 0) -> str:
    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])
    assert result.exit_code == expected_exit, result.output
    return result.output


def reload(session: Session, job: Job) -> Job:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one()


def job_meals(session: Session, job: Job) -> list[Meal]:
    return list(
        session.exec(
            select(Meal)
            .join(JobMeal, col(JobMeal.meal_id) == Meal.id)
            .where(JobMeal.job_id == job.id)
            .order_by(col(Meal.name))
        ).all()
    )


def test_job_succeeds_when_agent_adds_requested_meals(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=3)
    job = create_job(session, GENERATE_MEALS, {"count": 3})

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


def test_agent_environment_has_no_database_credentials(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_job(job)

    env = json.loads((tmp_path / "jobs" / str(job.id) / "fake-agent-env.json").read_text())
    assert "DATABASE_URL" not in env
    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_job_fails_when_agent_adds_too_few_meals(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 2})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "Requested 2 meals, but the agent added 1."
    assert job_meals(session, job) == []


def test_job_fails_when_agent_exits_with_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=0, exit_code=3)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "exited with code 3" in job.error


def test_job_fails_when_agent_stops_abnormally(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1, stop_reason="max_turns")
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "'max_turns'" in job.error


def test_job_of_unknown_kind_fails_without_running_agent(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, "plan_shopping", {})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "Unknown job kind 'plan_shopping'."
    assert not (tmp_path / "jobs" / str(job.id)).exists()


def test_job_fails_when_its_meals_cannot_be_saved(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    def fail_to_save(*_args: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr("castle_chef.worker.add_meals", fail_to_save)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "Could not save the meals" in job.error
    assert job_meals(session, job) == []


def test_job_already_claimed_is_left_alone(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A duplicate delivery: another task already claimed this job.
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    job.status = JobStatus.RUNNING
    session.add(job)
    session.commit()

    run_job(job)

    assert reload(session, job).status == JobStatus.RUNNING
    assert not (tmp_path / "jobs" / str(job.id)).exists()


def test_running_a_job_that_does_not_exist_fails_loudly(session: Session) -> None:
    missing = Job(kind=GENERATE_MEALS, params={"count": 1})

    output = run_job(missing, expected_exit=1)

    assert "does not exist" in output


def test_meals_are_discarded_if_the_job_was_failed_meanwhile(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The job was marked failed as stale while its agent was still running.
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    real_process_job = worker.process_job

    def process_then_go_stale(claimed: Job):
        meals = real_process_job(claimed)
        with Session(get_engine()) as other:
            fail_job(other, claimed.id, "stale")
        return meals

    monkeypatch.setattr(worker, "process_job", process_then_go_stale)

    run_job(job)

    job = reload(session, job)
    assert (job.status, job.error) == (JobStatus.FAILED, "stale")
    assert job_meals(session, job) == []


def test_agent_timeout_stops_everything_the_agent_started(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawner = Path(__file__).parent / "fake_agent_spawner.py"
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {spawner}")
    monkeypatch.setenv("CASTLE_CHEF_AGENT_TIMEOUT_SECONDS", "2")
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_job(job)

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "timed out" in job.error
    child_pid = int((tmp_path / "jobs" / str(job.id) / "child.pid").read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # the agent's own child is gone too


def test_parallel_jobs_wait_for_the_agent_lock(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "agent.lock"
    monkeypatch.setenv("CASTLE_CHEF_AGENT_LOCK_FILE", str(lock_path))
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})
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
