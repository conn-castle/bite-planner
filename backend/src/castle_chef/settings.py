from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/castle_chef/settings.py -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and `backend/.env`.

    Variables use the `CASTLE_CHEF_` prefix, except `DATABASE_URL`.
    """

    model_config = SettingsConfigDict(
        env_prefix="CASTLE_CHEF_",
        env_file=REPO_ROOT / "backend" / ".env",
        extra="ignore",
    )

    # Owner-role connection string for the Neon branch (the pooled one on Lambda).
    database_url: str = Field(validation_alias="DATABASE_URL")

    # Neon Auth URL of the branch (e.g. https://ep-….neonauth…/neondb/auth). The API
    # verifies the app's JWTs against its keys.
    neon_auth_url: str | None = None
    # Expected JWT issuer and audience; default: the Neon Auth URL's origin.
    neon_auth_issuer: str | None = None
    neon_auth_audience: str | None = None
    # Browser origins allowed to call the API (JSON list).
    cors_origins: list[str] = []

    # How the API starts a job: `sqs` (the queue on AWS) or `local` (a
    # `castle-chef job run` subprocess, for development).
    job_dispatch: Literal["sqs", "local"] = "local"
    jobs_queue_url: str | None = None
    # At most this many pending or running requests per person.
    max_active_jobs_per_member: int = 3
    # A pending job whose task never started is failed after this long.
    pending_timeout_seconds: int = 600

    # Hard limit for one agent run.
    agent_timeout_seconds: int = 900
    # Command that starts the agent harness; Grok flags are appended to it.
    agent_command: str = "al -q grok"
    # Neon project and branch for managing Neon Auth accounts (`castle-chef family`).
    neon_project_id: str | None = None
    neon_branch_id: str | None = None
    # Structured logs from the worker and CLI, plus logs sent from the app (client/).
    logs_dir: Path = Path.home() / ".local" / "state" / "castle-chef" / "logs"
    # Per-job working directories (prompt, agent output, logs).
    jobs_dir: Path = Path.home() / ".local" / "state" / "castle-chef" / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
