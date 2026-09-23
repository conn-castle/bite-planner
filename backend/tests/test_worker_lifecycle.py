"""Worker start-up and shutdown: the schema guard, the release it logs, and SIGTERM."""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlmodel import Session, select

from castle_chef.db import get_engine
from castle_chef.jobs import GENERATE_MEALS, create_job
from castle_chef.models import Job, JobStatus
from tests.conftest import BACKEND_DIR, TEST_DATABASE_URL

FAKE_AGENT = Path(__file__).parent / "fake_agent.py"
CASTLE_CHEF = Path(sys.executable).parent / "castle-chef"
SCHEMA_BEHIND_EXIT_CODE = 78  # the worker unit's RestartPreventExitStatus


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    ).stdout.strip()


def log_events(logs_dir: Path) -> list[dict]:
    """The worker's own events (not those of libraries such as Alembic)."""
    log_file = logs_dir / "castle-chef.jsonl"
    if not log_file.exists():
        return []
    entries = [json.loads(line) for line in log_file.read_text().splitlines()]
    return [entry for entry in entries if entry["logger"].startswith("castle_chef")]


def start_worker(tmp_path: Path, agent_seconds: float = 0, poll_seconds: int = 3600):
    env = os.environ | {
        "DATABASE_URL": TEST_DATABASE_URL,
        "CASTLE_CHEF_JOBS_DIR": str(tmp_path / "jobs"),
        "CASTLE_CHEF_LOGS_DIR": str(tmp_path / "logs"),
        "CASTLE_CHEF_AGENT_COMMAND": f"{sys.executable} {FAKE_AGENT} 1 end_turn 0 {agent_seconds}",
        "CASTLE_CHEF_POLL_INTERVAL_SECONDS": str(poll_seconds),
    }
    return subprocess.Popen(
        [CASTLE_CHEF, "worker", "run"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for(condition: Callable[[], bool], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the worker")
        time.sleep(0.1)


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


def test_worker_refuses_to_start_when_the_database_is_behind(
    session: Session, database_revision: Callable[[str], None], tmp_path: Path
) -> None:
    # The revision before the newest migration: this code expects a migration that has not run.
    script = ScriptDirectory.from_config(Config(BACKEND_DIR / "alembic.ini"))
    [head] = script.get_heads()
    database_revision(script.get_revision(head).down_revision)
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    worker = start_worker(tmp_path)
    _, stderr = worker.communicate(timeout=30)

    assert worker.returncode == SCHEMA_BEHIND_EXIT_CODE
    assert "behind this code" in stderr
    assert job_status(session, job) == JobStatus.PENDING
    assert [e["event"] for e in log_events(tmp_path / "logs")] == ["schema.behind"]


def test_worker_runs_when_the_database_is_newer_than_the_code(
    session: Session, database_revision: Callable[[str], None], tmp_path: Path
) -> None:
    # A revision this code does not know: the worker was rolled back to an older release.
    database_revision("f00dfeedf00d")
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    worker = start_worker(tmp_path)
    try:
        wait_for(lambda: job_status(session, job) == JobStatus.SUCCEEDED)
    finally:
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=30)

    events = [e["event"] for e in log_events(tmp_path / "logs")]
    assert events[:2] == ["schema.ahead", "worker.started"]


def test_worker_logs_the_commit_it_runs_on_start(tmp_path: Path) -> None:
    worker = start_worker(tmp_path)
    try:
        wait_for(lambda: any(e["event"] == "queue.checked" for e in log_events(tmp_path / "logs")))
    finally:
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=30)

    [started] = [e for e in log_events(tmp_path / "logs") if e["event"] == "worker.started"]
    commit = git("rev-parse", "HEAD")
    assert started["commit"] == commit
    release_tags = [tag for tag in git("tag", "--points-at", "HEAD").split() if tag[1:].isdigit()]
    assert started["release"] in (release_tags or [f"dev-{commit[:7]}"])


def test_sigterm_lets_the_running_job_finish_and_claims_no_more(
    session: Session, tmp_path: Path
) -> None:
    first = create_job(session, GENERATE_MEALS, {"count": 1})
    second = create_job(session, GENERATE_MEALS, {"count": 1})
    worker = start_worker(tmp_path, agent_seconds=3)
    wait_for(lambda: job_status(session, first) == JobStatus.RUNNING)

    worker.send_signal(signal.SIGTERM)
    worker.wait(timeout=30)

    assert worker.returncode == 0
    assert job_status(session, first) == JobStatus.SUCCEEDED
    assert job_status(session, second) == JobStatus.PENDING
    assert log_events(tmp_path / "logs")[-1]["event"] == "worker.stopped"


def test_sigterm_stops_an_idle_worker_promptly(tmp_path: Path) -> None:
    worker = start_worker(tmp_path, poll_seconds=3600)
    wait_for(lambda: any(e["event"] == "queue.checked" for e in log_events(tmp_path / "logs")))

    stopped_at = time.monotonic()
    worker.send_signal(signal.SIGTERM)
    worker.wait(timeout=10)

    assert worker.returncode == 0
    assert time.monotonic() - stopped_at < 5
    assert log_events(tmp_path / "logs")[-1]["event"] == "worker.stopped"
