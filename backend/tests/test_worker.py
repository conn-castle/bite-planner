import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, col, select
from typer.testing import CliRunner

from castle_chef.cli import app
from castle_chef.jobs import GENERATE_MEALS, create_job
from castle_chef.models import Job, JobMeal, JobStatus, Meal, utc_now

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def use_fake_agent(
    monkeypatch: pytest.MonkeyPatch, meals: int, stop_reason: str = "end_turn", exit_code: int = 0
) -> None:
    monkeypatch.setenv(
        "CASTLE_CHEF_AGENT_COMMAND",
        f"{sys.executable} {FAKE_AGENT} {meals} {stop_reason} {exit_code}",
    )


def run_worker_once() -> None:
    result = runner.invoke(app, ["worker", "run", "--once"])
    assert result.exit_code == 0, result.output


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

    run_worker_once()

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

    run_worker_once()

    env = json.loads((tmp_path / "jobs" / str(job.id) / "fake-agent-env.json").read_text())
    assert "DATABASE_URL" not in env
    assert reload(session, job).status == JobStatus.SUCCEEDED


def test_job_fails_when_agent_adds_too_few_meals(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 2})

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "Requested 2 meals, but the agent added 1."
    assert job_meals(session, job) == []


def test_job_fails_when_agent_exits_with_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=0, exit_code=3)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "exited with code 3" in job.error


def test_job_fails_when_agent_stops_abnormally(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1, stop_reason="max_turns")
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "'max_turns'" in job.error


def test_job_of_unknown_kind_fails_without_running_agent(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, "plan_shopping", {})

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error == "Unknown job kind 'plan_shopping'."
    assert not (tmp_path / "jobs" / str(job.id)).exists()


def test_worker_fails_jobs_left_running_past_the_timeout(session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 1})
    job.status = JobStatus.RUNNING
    job.started_at = utc_now() - timedelta(hours=2)
    session.add(job)
    session.commit()

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "worker likely stopped" in job.error


def test_job_fails_when_its_meals_cannot_be_saved(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    def fail_to_save(*_args: object) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr("castle_chef.worker.add_meals", fail_to_save)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    run_worker_once()

    job = reload(session, job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "Could not save the meals" in job.error
    assert job_meals(session, job) == []
