"""Worker start-up: the schema guard and the release it logs."""

import json
import re
import subprocess
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from castle_chef import booking, worker
from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.models import BookState, GroceryOrder, Job, JobStatus, utc_now
from tests.conftest import BACKEND_DIR
from tests.test_booking import approved
from tests.test_worker import use_fake_agent

runner = CliRunner()
SCHEMA_BEHIND_EXIT_CODE = 78


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    ).stdout.strip()


def log_events(logs_dir: Path) -> list[dict[str, Any]]:
    """The worker's own events (not those of libraries such as Alembic)."""
    entries = [
        json.loads(line) for line in (logs_dir / "castle-chef.jsonl").read_text().splitlines()
    ]
    return [entry for entry in entries if entry["logger"].startswith("castle_chef")]


def job_status(session: Session, job: Job) -> JobStatus:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one().status


@pytest.fixture
def database_revision() -> Iterator[Callable[[str | None], None]]:
    """Set the database's recorded migration revision (None: no migration has
    run); restored afterwards."""

    def set_revision(revision: str | None) -> None:
        with get_engine().begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            if revision is not None:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision}
                )

    with get_engine().connect() as connection:
        original = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    yield set_revision
    set_revision(original)


def test_worker_refuses_to_run_when_the_database_is_behind(
    session: Session, job: Job, database_revision: Callable[[str | None], None], tmp_path: Path
) -> None:
    # No migration recorded: this code expects migrations that have not run.
    database_revision(None)

    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    assert result.exit_code == SCHEMA_BEHIND_EXIT_CODE
    assert "behind this code" in result.output
    assert job_status(session, job) == JobStatus.PENDING
    assert [e["event"] for e in log_events(tmp_path / "logs")] == ["schema.behind"]


def test_worker_runs_when_the_database_is_newer_than_the_code(
    session: Session,
    job: Job,
    database_revision: Callable[[str | None], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A revision this code does not know: an older release is running.
    database_revision("f00dfeedf00d")
    use_fake_agent(monkeypatch, meals=1)

    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    assert result.exit_code == 0, result.output
    assert job_status(session, job) == JobStatus.SUCCEEDED
    assert [e["event"] for e in log_events(tmp_path / "logs")][:2] == [
        "schema.ahead",
        "worker.started",
    ]


def test_worker_logs_the_checkout_it_runs(
    job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)

    runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    [started] = [e for e in log_events(tmp_path / "logs") if e["event"] == "worker.started"]
    commit = git("rev-parse", "HEAD")
    assert started["commit"] == commit
    # Release tags: YYYY.M.D.N, and r<N> before 2026-09-24.
    release_tags = [
        tag
        for tag in git("tag", "--points-at", "HEAD").split()
        if re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}\.\d+|r\d+", tag)
    ]
    assert started["release"] in (release_tags or [f"dev-{commit[:7]}"])


def test_worker_in_an_image_logs_the_release_it_was_built_from(
    job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Images have no git checkout; the build sets these.
    monkeypatch.setenv("CASTLE_CHEF_RELEASE", "r42")
    monkeypatch.setenv("CASTLE_CHEF_COMMIT", "abc123")
    use_fake_agent(monkeypatch, meals=1)

    runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    [started] = [e for e in log_events(tmp_path / "logs") if e["event"] == "worker.started"]
    assert (started["release"], started["commit"]) == ("r42", "abc123")


def test_overdue_queued_booking_settles_once_without_running_agent(
    session: Session,
    household_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = approved(session, household_id, utc_now().date() + timedelta(days=4))
    job = booking.start(session, order)
    session.commit()
    job_id, order_id = job.id, order.id
    session.execute(
        text("UPDATE job SET created_at=created_at - interval '3 hours' WHERE id=:id"),
        {"id": job_id},
    )
    session.commit()
    notified: list[UUID] = []
    processed: list[UUID] = []
    monkeypatch.setattr(worker, "notify_booking", notified.append)

    def process(claimed: Job) -> worker.JobResult:
        processed.append(claimed.id)
        raise AssertionError("An overdue job must not run")

    monkeypatch.setattr(worker, "process_job", process)
    worker.run_job(job_id)
    worker.run_job(job_id)  # duplicate delivery cannot notify or settle twice
    session.expire_all()
    settled = session.get_one(GroceryOrder, order_id)
    assert settled.book_state == BookState.NOT_BOOKED
    assert settled.book_job_id is None
    assert processed == [] and notified == [order_id]
