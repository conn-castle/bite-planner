import json
import subprocess
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


def test_uvicorns_own_lines_are_json_too(tmp_path: Path) -> None:
    # As the image runs the API (`uvicorn castle_chef.api:app`): uvicorn sets up
    # its logging, then imports the app; a crash it reports keeps its traceback.
    script = """
import logging, uvicorn
uvicorn.Config("castle_chef.api:app").load()
try:
    raise RuntimeError("a defect")
except RuntimeError:
    logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
logging.getLogger("uvicorn.access").info("127.0.0.1 - GET /health 200")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    lines = [json.loads(line) for line in result.stderr.splitlines()]
    [crash] = [line for line in lines if line["event"] == "Exception in ASGI application"]
    assert crash["level"] == "error" and "RuntimeError: a defect" in crash["exception"]
    assert not [line for line in lines if line["logger"] == "uvicorn.access"]
