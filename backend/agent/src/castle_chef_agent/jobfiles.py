"""Files a job's worker and the agent's tools share in the job directory:
the worker's context for the tools' checks, and a lock so tool calls the
agent makes at once don't interleave."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

CONTEXT_FILE = "job.json"


def write_context(job_dir: Path, context: BaseModel) -> None:
    (job_dir / CONTEXT_FILE).write_text(context.model_dump_json(indent=2))


def replace_file(path: Path, text: str) -> None:
    """Write the file whole: a reader sees the old contents or the new, never part."""
    partial = path.with_name(f".{path.name}.tmp")
    partial.write_text(text)
    os.replace(partial, path)


def read_context[T: BaseModel](job_dir: Path, model: type[T]) -> T:
    return model.model_validate_json((job_dir / CONTEXT_FILE).read_text())


@contextmanager
def locked(job_dir: Path) -> Iterator[None]:
    """Hold the job directory's lock: one tool call reads and changes its files at a time."""
    with (job_dir / ".lock").open("a") as lock:  # closing it releases the lock
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
