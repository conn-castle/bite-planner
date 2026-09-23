import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from castle_chef.cli import app
from castle_chef.jobs import GENERATE_MEALS, create_job
from castle_chef.models import ClientLogBatch

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def log_events(logs_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (logs_dir / "castle-chef.jsonl").read_text().splitlines()]


def test_worker_writes_structured_job_events_to_the_log_file(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {FAKE_AGENT} 1 end_turn 0 0")
    job = create_job(session, GENERATE_MEALS, {"count": 1})

    assert runner.invoke(app, ["worker", "run", "--once"]).exit_code == 0

    events = {entry["event"]: entry for entry in log_events(tmp_path / "logs")}
    assert events["job.claimed"]["job_id"] == str(job.id)
    assert events["job.succeeded"] == events["job.succeeded"] | {
        "level": "info",
        "job_id": str(job.id),
        "meal_count": 1,
    }
    assert events["queue.checked"]["jobs_processed"] == 1


def test_logs_sent_from_the_app_are_saved_to_disk_and_removed(
    session: Session, tmp_path: Path
) -> None:
    user_id = session.execute(
        text("""INSERT INTO neon_auth."user" (email, name) VALUES ('mom@example.com', 'Mom')
                RETURNING id""")
    ).scalar_one()
    session.add(
        ClientLogBatch(
            user_id=user_id,
            device={"platform": "ios"},
            entries=[{"event": "app.start"}, {"event": "jobs.load_failed", "error": "boom"}],
        )
    )
    session.commit()

    result = runner.invoke(app, ["logs", "pull"])

    assert result.exit_code == 0, result.output
    [saved] = (tmp_path / "logs" / "client").glob("*.jsonl")
    header, *entries = [json.loads(line) for line in saved.read_text().splitlines()]
    assert (header["email"], header["device"]) == ("mom@example.com", {"platform": "ios"})
    assert [entry["event"] for entry in entries] == ["app.start", "jobs.load_failed"]
    assert session.exec(select(ClientLogBatch)).all() == []
