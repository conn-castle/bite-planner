import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from castle_chef.cli import app
from castle_chef.models import Job

runner = CliRunner()
FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


def log_events(logs_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (logs_dir / "castle-chef.jsonl").read_text().splitlines()]


def test_worker_writes_structured_job_events_to_the_log_file(
    job: Job, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_AGENT_COMMAND", f"{sys.executable} {FAKE_AGENT} 1 end_turn 0 0")

    assert runner.invoke(app, ["job", "run", "--job-id", str(job.id)]).exit_code == 0

    events = {entry["event"]: entry for entry in log_events(tmp_path / "logs")}
    assert events["job.claimed"]["job_id"] == str(job.id)
    assert events["job.succeeded"] == events["job.succeeded"] | {
        "level": "info",
        "job_id": str(job.id),
        "meal_count": 1,
    }
    assert events["worker.started"]["job_id"] == str(job.id)
