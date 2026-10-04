import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from castle_chef_agent.checkout import SETTLE_CHECK_MS, SETTLE_MAX_MS
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
    # Serve the reminders' `/events` route instead (the Reminders Lambda function,
    # started by each member's EventBridge Scheduler schedule; see reminders.py).
    reminders_handler: bool = False

    # How reminder schedules are kept: `aws` (EventBridge Scheduler) or `local`
    # (development: logged, nothing scheduled).
    reminder_scheduler: Literal["aws", "local"] = "local"
    # The schedule group, the Reminders function it invokes, and the role it
    # invokes it as (aws only).
    reminder_schedule_group: str | None = None
    reminder_function_arn: str | None = None
    reminder_role_arn: str | None = None

    # How notifications are sent: `apns` (Apple's push service) or `local`
    # (development: logged, nothing sent).
    push: Literal["apns", "local"] = "local"
    # The APNs auth key (.p8, base64; SSM /castle-chef/shared/apns-key-base64) and its id.
    apns_key: str | None = None
    apns_key_id: str | None = None

    # How the API starts a job: `sqs` (the queue on AWS) or `local` (the worker
    # image in Docker on this machine, as Fargate runs it: scripts/worker-docker.sh).
    job_dispatch: Literal["sqs", "local"] = "local"
    jobs_queue_url: str | None = None
    # Suggestions per batch (a `generate_meals` job).
    meals_per_batch: int = 20
    # A pending job whose task never started is failed after this long.
    pending_timeout_seconds: int = 600

    # Hard limit for one agent run.
    agent_timeout_seconds: int = 900
    # Command that starts the agent harness; Grok flags are appended to it. Set in
    # the worker image (`al -q grok`), which is where a real agent runs; unset, a
    # job fails rather than run an agent on this machine (tests set a fake one).
    agent_command: str | None = None
    # The agent's PATH, exactly (the worker image: the agent's own `castle-chef`
    # and system commands, nothing of the backend). None: this environment's
    # `castle-chef` first, then the worker's PATH.
    agent_path: str | None = None
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
    # Instacart, through its tools in the ChatGPT apps (castle_chef_agent.instacart):
    # the Codex login (CODEX_HOME: the household's ChatGPT sign-in with Instacart
    # connected; EFS in AWS), its lock (next to it, like the agent's), how to start
    # Codex, and the store. No login: nothing reaches Instacart.
    codex_home: Path | None = None
    codex_lock_file: Path | None = None
    codex_command: str = "codex"
    instacart_store: str = "Wegmans"
    # The household's Instacart website session (browser cookies, for checkout:
    # castle_chef.checkout): the SSM SecureString parameter it's kept in, read and
    # written back by the worker. None: checkout can't be reached.
    instacart_session_parameter: str | None = None
    # Instacart's website (tests serve stand-in pages at another address).
    instacart_site: str = "https://www.instacart.com"
    # How often checkout's tools check a page for having stopped changing after
    # an action (castle_chef_agent.checkout); tests' stand-in pages settle at once.
    checkout_settle_check_ms: int = Field(default=SETTLE_CHECK_MS, gt=0, le=SETTLE_MAX_MS // 2)
    # Whether booking presses Place order (castle_chef.booking): `live` orders;
    # `dry_run` (development) stops just before and shows the order as booked,
    # "Dry run"; `off` (production until the owner turns it on, from SSM) stops
    # just before and says booking isn't turned on yet.
    instacart_booking: Literal["live", "dry_run", "off"] = "dry_run"
    # The store's time zone: delivery times, and the day an order books (at
    # BOOK_HOUR there, four days before delivery).
    store_time_zone: str = "America/New_York"
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
