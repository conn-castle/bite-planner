"""Fargate identity is read before claiming, and local workers cannot buy."""

import json
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from sqlmodel import Session

from castle_chef.models import Job, JobStatus
from castle_chef.settings import get_settings
from castle_chef.worker import run_job
from castle_chef.worker_tasks import current_task
from tests.test_failures import CLUSTER, TASK


@pytest.fixture
def metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    task = tmp_path / "task"
    task.write_text(json.dumps({"TaskARN": TASK, "Cluster": CLUSTER.rsplit("/", 1)[-1]}))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CASTLE_CHEF_WORKER_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", f"http://127.0.0.1:{server.server_port}")
    get_settings.cache_clear()
    try:
        yield task
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_reads_the_task_identity_from_fargates_metadata(metadata: Path) -> None:
    task = current_task()
    assert task is not None and task.task_arn == TASK


@pytest.mark.parametrize(
    "body",
    [
        {"TaskARN": TASK, "Cluster": "wrong"},
        {"TaskARN": TASK.replace("/abc", "-other/abc"), "Cluster": CLUSTER},
    ],
)
def test_wrong_metadata_prevents_claiming(
    metadata: Path,
    session: Session,
    job: Job,
    body: dict[str, str],
) -> None:
    metadata.write_text(json.dumps(body))
    with pytest.raises(RuntimeError, match="cluster"):
        run_job(job.id)
    session.refresh(job)
    assert job.status == JobStatus.PENDING and job.worker_task_arn is None


def test_missing_metadata_prevents_claiming(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    job: Job,
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_WORKER_CLUSTER_ARN", CLUSTER)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="metadata endpoint is missing"):
        run_job(job.id)
    session.refresh(job)
    assert job.status == JobStatus.PENDING


def test_local_dry_runs_have_no_task_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASTLE_CHEF_WORKER_CLUSTER_ARN", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("AWS_EXECUTION_ENV", raising=False)
    get_settings.cache_clear()
    assert current_task() is None
