"""Worker start-up: the schema guard and the release it logs."""

import json
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from castle_chef.cli import app
from castle_chef.db import get_engine
from castle_chef.jobs import GENERATE_MEALS, create_job
from castle_chef.models import Job, JobStatus
from tests.conftest import BACKEND_DIR
from tests.test_worker import use_fake_agent

runner = CliRunner()
SCHEMA_BEHIND_EXIT_CODE = 78


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    ).stdout.strip()


def log_events(logs_dir: Path) -> list[dict]:
    """The worker's own events (not those of libraries such as Alembic)."""
    entries = [
        json.loads(line) for line in (logs_dir / "castle-chef.jsonl").read_text().splitlines()
    ]
    return [entry for entry in entries if entry["logger"].startswith("castle_chef")]


def job_status(session: Session, job: Job) -> JobStatus:
    session.expire_all()
    return session.exec(select(Job).where(Job.id == job.id)).one().status


@pytest.fixture
def database_revision() -> Iterator[Callable[[str], None]]:
    """Set the database's recorded migration revision; restored afterwards."""

    def set_revision(revision: str) -> None:
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = :r"), {"r": revision})

    with get_engine().connect() as connection:
        original = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    yield set_revision
    set_revision(original)


def test_worker_refuses_to_run_when_the_database_is_behind(
    session: Session, database_revision: Callable[[str], None], tmp_path: Path
) -> None:
    # The revision before the newest migration: this code expects a migration that has not run.
    script = ScriptDirectory.from_config(Config(BACKEND_DIR / "alembic.ini"))
    [head] = script.get_heads()
    database_revision(script.get_revision(head).down_revision)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    assert result.exit_code == SCHEMA_BEHIND_EXIT_CODE
    assert "behind this code" in result.output
    assert job_status(session, job) == JobStatus.PENDING
    assert [e["event"] for e in log_events(tmp_path / "logs")] == ["schema.behind"]


def test_worker_runs_when_the_database_is_newer_than_the_code(
    session: Session,
    database_revision: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A revision this code does not know: an older release is running.
    database_revision("f00dfeedf00d")
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    result = runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    assert result.exit_code == 0, result.output
    assert job_status(session, job) == JobStatus.SUCCEEDED
    assert [e["event"] for e in log_events(tmp_path / "logs")][:2] == [
        "schema.ahead",
        "worker.started",
    ]


def test_worker_logs_the_checkout_it_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

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
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Images have no git checkout; the build sets these.
    monkeypatch.setenv("CASTLE_CHEF_RELEASE", "r42")
    monkeypatch.setenv("CASTLE_CHEF_COMMIT", "abc123")
    use_fake_agent(monkeypatch, meals=1)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    runner.invoke(app, ["job", "run", "--job-id", str(job.id)])

    [started] = [e for e in log_events(tmp_path / "logs") if e["event"] == "worker.started"]
    assert (started["release"], started["commit"]) == ("r42", "abc123")
