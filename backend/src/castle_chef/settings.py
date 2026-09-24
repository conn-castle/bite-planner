import json
import os
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
    # Serve the failure handler's `/events` route (its own Lambda function, which
    # has no public URL; see failures.py). Never on the public API.
    failure_handler: bool = False

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
    # Run the agent inside Grok's own sandbox profile (see worker.py). Off in the
    # worker container, which is the sandbox there (Grok's needs kernel
    # privileges Fargate doesn't grant).
    agent_sandbox: bool = True
    # Run the agent as this uid/gid (the worker container runs as root and
    # starts the agent as an unprivileged user); None: as the worker's user.
    agent_uid: int | None = None
    # If set, parallel jobs run the agent one at a time, locking this file (next
    # to the provider's login on EFS): two agents refreshing one subscription
    # login at once can invalidate it.
    agent_lock_file: Path | None = None
    # Environment variables passed through to the agent (JSON list), e.g.
    # GROK_AUTH_PATH for a login kept outside Grok's home.
    agent_env: list[str] = []
    # The Agent Layer project `al` launches the agent from (it syncs its generated
    # files there on every launch, and Grok's home is its `.grok-config`).
    agent_project_dir: Path = REPO_ROOT
    # If set, copied to agent_project_dir, owned by the agent user, before each
    # run: the worker container's /app is read-only, so the agent gets a writable
    # copy of the project's .agent-layer.
    agent_project_template: Path | None = None
    # Neon project and branch for managing Neon Auth accounts (`castle-chef family`).
    neon_project_id: str | None = None
    neon_branch_id: str | None = None
    # Structured logs from the worker and CLI, plus logs sent from the app (client/).
    logs_dir: Path = Path.home() / ".local" / "state" / "castle-chef" / "logs"
    # Per-job working directories (prompt, agent output, logs).
    jobs_dir: Path = Path.home() / ".local" / "state" / "castle-chef" / "jobs"


def load_ssm_parameters() -> None:
    """Set environment variables from AWS SSM (the source of truth for secrets)
    where the platform can't inject them, i.e. on Lambda.

    CASTLE_CHEF_SSM_PARAMETERS maps variable names to parameter names, e.g.
    {"DATABASE_URL": "/castle-chef/production/database-url-pooled"}. Variables
    that are already set are left alone. Fails loudly if a parameter can't be read.
    """
    mapping = json.loads(os.environ.get("CASTLE_CHEF_SSM_PARAMETERS", "{}"))
    missing = {name: parameter for name, parameter in mapping.items() if name not in os.environ}
    if not missing:
        return
    import boto3  # only needed where parameters are loaded

    ssm = boto3.client("ssm")
    for name, parameter in missing.items():
        response = ssm.get_parameter(Name=parameter, WithDecryption=True)
        os.environ[name] = response["Parameter"]["Value"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
