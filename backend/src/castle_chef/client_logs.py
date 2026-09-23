"""Saves logs sent from the app (`client_log_batch` rows) to disk."""

import json
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, col, select

from castle_chef.models import ClientLogBatch

BATCHES_PER_CHUNK = 20


def save_client_logs(session: Session, logs_dir: Path) -> int:
    """Write each batch to `<logs_dir>/client/` as JSON lines, then delete it.

    The first line describes the batch; each following line is one app log entry.
    Works in chunks; `SKIP LOCKED` lets the worker and `logs pull` run at once.
    Returns the number of batches saved.
    """
    directory = logs_dir / "client"
    saved = 0
    while True:
        batches = session.exec(
            select(ClientLogBatch)
            .order_by(col(ClientLogBatch.created_at))
            .limit(BATCHES_PER_CHUNK)
            .with_for_update(skip_locked=True)
        ).all()
        if not batches:
            return saved
        directory.mkdir(parents=True, exist_ok=True)
        for batch in batches:
            email = session.execute(
                text('SELECT email FROM neon_auth."user" WHERE id = :id'), {"id": batch.user_id}
            ).scalar_one_or_none()
            header = {
                "batch_id": str(batch.id),
                "sent_at": batch.created_at.isoformat(),
                "user_id": str(batch.user_id),
                "email": email,
                "device": batch.device,
            }
            lines = [json.dumps(header), *(json.dumps(entry) for entry in batch.entries)]
            path = directory / f"{batch.created_at:%Y%m%dT%H%M%S}-{batch.id}.jsonl"
            path.write_text("\n".join(lines) + "\n")
            session.delete(batch)
        # Rows are deleted only after their files are written; a retry rewrites the same files.
        session.commit()
        saved += len(batches)
