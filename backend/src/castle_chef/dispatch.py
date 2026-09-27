"""Starting a job's worker: a message on the jobs queue in AWS (EventBridge Pipes
then runs one Fargate task per message), or the worker image in Docker on this
machine in development."""

import logging
import subprocess
from functools import lru_cache
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from castle_chef.settings import REPO_ROOT, get_settings

log = logging.getLogger(__name__)

# The worker reads its job id from this variable (the Pipe sets it per task).
JOB_ID_ENV = "CASTLE_CHEF_JOB_ID"


class JobMessage(BaseModel):
    """The jobs-queue message. Bump `version` for incompatible changes."""

    version: Literal[1] = 1
    job_id: UUID


class DispatchError(Exception):
    pass


class JobDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> None: ...


class SqsDispatcher:
    def __init__(self, queue_url: str) -> None:
        # Only the API sends to the queue; the worker image doesn't install boto3.
        import boto3
        from botocore.config import Config

        self.queue_url = queue_url
        # Bounded, so a stuck call fails within the API's time limit and the job
        # is marked failed instead of the request timing out.
        self._sqs = boto3.client(
            "sqs",
            config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
        )

    def dispatch(self, job_id: UUID) -> None:
        try:
            self._sqs.send_message(
                QueueUrl=self.queue_url, MessageBody=JobMessage(job_id=job_id).model_dump_json()
            )
        except Exception as error:
            raise DispatchError(f"Could not queue job {job_id}: {error}") from error


class LocalDispatcher:
    """Runs the job in the worker image on this machine, as Fargate does
    (scripts/worker-docker.sh), detached (development)."""

    def dispatch(self, job_id: UUID) -> None:
        script = REPO_ROOT / "scripts" / "worker-docker.sh"
        try:
            # What the run needs (the image, Docker, backend/.env), first: once it's
            # detached, a failure would leave the job pending until it goes stale.
            check = subprocess.run(
                [script, "check"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            )
            if check.returncode != 0:
                raise DispatchError(
                    f"Can't run job {job_id} locally: {check.stderr.strip() or check.stdout}"
                )
            subprocess.Popen(
                [script, "run", str(job_id)],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                start_new_session=True,  # outlives the request and the API's reloader
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DispatchError(f"Could not start a local worker for {job_id}: {error}") from error
        log.info("job.dispatched_locally", extra={"job_id": str(job_id)})


@lru_cache
def get_dispatcher() -> JobDispatcher:
    """The dispatcher CASTLE_CHEF_JOB_DISPATCH selects."""
    settings = get_settings()
    if settings.job_dispatch == "local":
        return LocalDispatcher()
    if not settings.jobs_queue_url:
        raise RuntimeError("Set CASTLE_CHEF_JOBS_QUEUE_URL.")
    return SqsDispatcher(settings.jobs_queue_url)
