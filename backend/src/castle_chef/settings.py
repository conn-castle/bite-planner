from functools import lru_cache
from pathlib import Path

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

    # Direct (owner role) connection string for the Neon branch.
    database_url: str = Field(validation_alias="DATABASE_URL")

    # How often the worker checks the queue. Each check wakes Neon compute.
    poll_interval_seconds: int = 1800
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
