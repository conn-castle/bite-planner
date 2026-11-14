"""A job's record: what its agent was given and did, kept once the worker's task
is gone, for finding out what happened. It is the job's directory (the prompt,
the agent's responses and tool calls, and the files its tools wrote), as one gzipped tar in S3
(`jobs/<job-id>.tar.gz`; docs/operations/aws.md, "Diagnostics")."""

import io
import logging
import tarfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from bite_planner_agent.checkout import RENEWED_SESSION_FILE, SESSION_FILE

from bite_planner.settings import get_settings

log = logging.getLogger(__name__)

# The household's Instacart cookies, should a copy be in a job's directory: never kept.
SECRET_FILES = {SESSION_FILE, RENEWED_SESSION_FILE}
# While a job runs, its record is saved this often too, so a task that's killed
# (out of memory, the time cap) still leaves most of it.
SAVE_EVERY_SECONDS = 60.0


class RecordStore:
    """The job records bucket."""

    def __init__(self, bucket: str) -> None:
        import boto3  # the worker's group

        self.bucket = bucket
        self._s3 = boto3.client("s3")

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)


def record_store() -> RecordStore | None:
    """Where records go; none when running on this machine, where the job's files
    stay on disk (and `make worker-job` copies them out)."""
    bucket = get_settings().job_records_bucket
    return RecordStore(bucket) if bucket else None


def pack(job_dir: Path) -> tuple[bytes, list[str]]:
    """The job's directory as `job/`, if it exists."""

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if Path(info.name).name in SECRET_FILES else info

    parts: list[str] = []
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if job_dir.exists():
            archive.add(job_dir, arcname="job", filter=keep)
            parts.append("job")
    return buffer.getvalue(), parts


def save_record(job_id: UUID, *, final: bool) -> None:
    """Keeps the job's record as it is now (`final`: the job has ended). Failing
    changes nothing about the job; it's logged, as an error once the job has
    ended (a save while it runs can meet a file the agent is still writing)."""
    key = f"jobs/{job_id}.tar.gz"
    fields = {"job_id": str(job_id), "key": key, "final": final}
    try:
        store = record_store()
        if store is None:
            return
        data, parts = pack(get_settings().jobs_dir / str(job_id))
        store.put(key, data)
    except Exception:
        log.log(
            logging.ERROR if final else logging.WARNING,
            "job.record_failed",
            extra=fields,
            exc_info=True,
        )
        return
    log.info("job.record_saved", extra=fields | {"bytes": len(data), "parts": parts})


@contextmanager
def keeping_record(job_id: UUID) -> Iterator[None]:
    """Saves the job's record while it runs (every SAVE_EVERY_SECONDS) and once
    it ends, however it ends."""
    stop = threading.Event()

    def periodically() -> None:
        while not stop.wait(SAVE_EVERY_SECONDS):
            save_record(job_id, final=False)

    saver = threading.Thread(target=periodically, name=f"record-{job_id}", daemon=True)
    saver.start()
    try:
        yield
    finally:
        stop.set()
        saver.join()
        save_record(job_id, final=True)
