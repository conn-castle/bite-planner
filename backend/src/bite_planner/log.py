"""Structured logging: one JSON object per line.

Log with an event name and fields, e.g.
`log.info("job.claimed", extra={"job_id": str(job.id)})`.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Attributes every LogRecord has; anything else came from `extra=` and is a field.
_RECORD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry |= {key: value for key, value in vars(record).items() if key not in _RECORD_ATTRS}
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure(log_file: Path | None) -> None:
    """Log JSON lines to stderr and, when given, to a rotating file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        # Private: the agent (another user in the worker image) must not read them.
        log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    # Uvicorn's own lines (starting, stopping, and "Exception in ASGI application"
    # with its traceback) as JSON too, through the same handlers: its own plain
    # text would land in CloudWatch a line at a time, with no level. Its access
    # lines repeat `http.request`.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
