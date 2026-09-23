"""Polls the job queue and runs the agent harness for each job."""

import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from string import Template

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from pydantic import ValidationError

from castle_chef.client_logs import save_client_logs
from castle_chef.db import get_engine, get_session
from castle_chef.jobs import (
    GENERATE_MEALS,
    GenerateMealsParams,
    claim_next_job,
    fail_stale_jobs,
    finish_job,
)
from castle_chef.meals import MealInput, add_meals, load_meals
from castle_chef.models import Job, JobStatus
from castle_chef.settings import REPO_ROOT, get_settings

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Grok flags that make the run bare: shell as the only tool and no subagents.
# `al grok` supplies the model, reasoning effort, and `--no-memory` from
# .agent-layer/config.toml. Grok's headless mode auto-approves every tool call,
# so the sandbox profile below is what limits the agent.
SANDBOX_PROFILE = "castle-chef-agent"
GROK_ARGS = [
    "--output-format", "json",
    "--tools", "run_terminal_cmd",
    "--disallowed-tools", "Agent,search_tool,use_tool",
    "--sandbox", SANDBOX_PROFILE,
    "--max-turns", "60",
]  # fmt: skip

# `strict`: read and write the job directory, read system paths, no network for
# commands. The agent may also read what `castle-chef meal add` needs to run,
# but nothing else in the repo (e.g. backend/.env) or the home directory.
SANDBOX_TOML = Template("""[profiles.$name]
extends = "strict"
read_only = [$read_only]
""")


# Exit status when the database is behind this code. The systemd unit does not
# restart on it (RestartPreventExitStatus), so the refusal is logged once.
SCHEMA_BEHIND_EXIT_CODE = 78


class AgentRunError(Exception):
    pass


class SchemaBehindError(Exception):
    pass


_stop_requested = False


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def run(once: bool) -> None:
    """Poll the queue until done (`once`) or SIGTERM, which lets a running job finish."""
    global _stop_requested
    settings = get_settings()
    check_schema()
    log.info("worker.started", extra=current_release())
    _stop_requested = False
    previous_handler = signal.signal(signal.SIGTERM, _request_stop)
    try:
        while not _stop_requested:
            with get_session() as session:
                # Grace period beyond the agent timeout before a run counts as lost.
                stale = fail_stale_jobs(
                    session, timedelta(seconds=settings.agent_timeout_seconds + 300)
                )
                if stale:
                    log.error("jobs.stale_failed", extra={"count": stale})
            processed = 0
            while not _stop_requested and process_next_job():
                processed += 1
            with get_session() as session:
                saved = save_client_logs(session, settings.logs_dir)
            log.info(
                "queue.checked", extra={"jobs_processed": processed, "client_logs_saved": saved}
            )
            if once:
                return
            _sleep_unless_stopped(settings.poll_interval_seconds)
        log.info("worker.stopped")
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _sleep_unless_stopped(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not _stop_requested and (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(remaining, 1))


def check_schema() -> None:
    """Refuse to run against a database whose migrations are older than this code.

    A database with revisions this code does not know is newer (the worker was
    rolled back to an older release), which expand/contract migrations allow.
    """
    script = ScriptDirectory.from_config(Config(REPO_ROOT / "backend" / "alembic.ini"))
    with get_engine().connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    heads = set(script.get_heads())
    if current == heads:
        return
    fields = {"database": sorted(current), "code": sorted(heads)}
    if any(not _is_known_revision(script, revision) for revision in current):
        log.warning("schema.ahead", extra=fields)
        return
    raise SchemaBehindError(
        f"The database is at migration {sorted(current) or 'none'}, behind this code's "
        f"{sorted(heads)}. Its migration has not run: releases migrate Neon `main` "
        "before publishing; on a dev branch, run `alembic upgrade head`."
    )


def _is_known_revision(script: ScriptDirectory, revision: str) -> bool:
    try:
        script.get_revision(revision)
    except CommandError:
        return False
    return True


def current_release() -> dict[str, str]:
    """The checked-out commit and its release tag (`r<N>`), or `dev-<sha>` if untagged."""

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check
        )

    commit = git("rev-parse", "HEAD").stdout.strip()
    tag = git("describe", "--exact-match", "--tags", "--match", "r[0-9]*", "HEAD", check=False)
    release = tag.stdout.strip() if tag.returncode == 0 else f"dev-{commit[:7]}"
    return {"release": release, "commit": commit}


def process_next_job() -> bool:
    """Process one pending job. Returns False when the queue is empty."""
    with get_session() as session:
        job = claim_next_job(session)
    if job is None:
        return False

    fields = {"job_id": str(job.id)}
    log.info("job.claimed", extra=fields | {"kind": job.kind, "params": job.params})
    try:
        meals = process_job(job)
    except (AgentRunError, ValidationError) as error:
        log.error("job.failed", extra=fields | {"error": str(error)})
        with get_session() as session:
            finish_job(session, job.id, error=str(error))
        return True
    except Exception as error:
        log.exception("job.crashed", extra=fields)
        with get_session() as session:
            finish_job(session, job.id, error=f"Unexpected worker error: {error!r}")
        return True

    try:
        with get_session() as session:
            # The meals and the job's success are committed together.
            add_meals(session, job.id, meals)
            finish_job(session, job.id)
    except Exception as error:
        # The commit may have succeeded even though it raised (e.g. a lost
        # acknowledgement), so fail the job only if it is not already done.
        log.exception("job.save_failed", extra=fields)
        with get_session() as session:
            saved = session.get(Job, job.id)
            if saved is not None and saved.status == JobStatus.RUNNING:
                finish_job(session, job.id, error=f"Could not save the meals: {error!r}")
        return True
    log.info("job.succeeded", extra=fields | {"meal_count": len(meals)})
    return True


def process_job(job: Job) -> list[MealInput]:
    if job.kind != GENERATE_MEALS:
        raise AgentRunError(f"Unknown job kind {job.kind!r}.")
    params = GenerateMealsParams.model_validate(job.params)

    prompt = Template(PROMPTS_DIR.joinpath("generate_meals.md").read_text()).substitute(
        count=params.count,
        meal_schema=json.dumps(MealInput.model_json_schema(), indent=2),
    )
    job_dir = get_settings().jobs_dir / str(job.id)
    run_agent(job_dir, prompt)

    meals = load_meals(job_dir)
    if len(meals) != params.count:
        raise AgentRunError(f"Requested {params.count} meals, but the agent added {len(meals)}.")
    return meals


def run_agent(job_dir: Path, prompt: str) -> None:
    settings = get_settings()
    job_dir.mkdir(parents=True)
    prompt_path = job_dir / "prompt.md"
    prompt_path.write_text(prompt)
    venv_bin = Path(sys.executable).parent
    read_only = [Path(sys.prefix), Path(__file__).resolve().parents[1]]
    (job_dir / ".grok").mkdir()
    (job_dir / ".grok" / "sandbox.toml").write_text(
        SANDBOX_TOML.substitute(
            name=SANDBOX_PROFILE, read_only=", ".join(json.dumps(str(p)) for p in read_only)
        )
    )

    command = [
        *shlex.split(settings.agent_command),
        "--cwd", str(job_dir),
        "--prompt-file", str(prompt_path),
        *GROK_ARGS,
    ]  # fmt: skip
    # A minimal environment: no database credentials or other secrets reach the agent.
    env = {
        "HOME": os.environ["HOME"],
        # Puts this environment's `castle-chef` executable first on the agent's PATH.
        "PATH": f"{venv_bin}{os.pathsep}{os.environ['PATH']}",
        "CASTLE_CHEF_JOB_DIR": str(job_dir),
    }
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    stdout_path = job_dir / "agent-output.json"
    stderr_path = job_dir / "agent-stderr.log"
    log.info("agent.started", extra={"job_dir": str(job_dir)})
    started = time.monotonic()
    try:
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,  # `al` resolves the Agent Layer project from here.
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=settings.agent_timeout_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise AgentRunError(
            f"Agent timed out after {settings.agent_timeout_seconds}s; see {job_dir}."
        ) from error

    duration = round(time.monotonic() - started, 1)
    if completed.returncode != 0:
        log.error(
            "agent.exited",
            extra={
                "job_dir": str(job_dir),
                "returncode": completed.returncode,
                "seconds": duration,
            },
        )
        raise AgentRunError(f"Agent exited with code {completed.returncode}; see {stderr_path}.")
    try:
        output = json.loads(stdout_path.read_text())
    except json.JSONDecodeError as error:
        raise AgentRunError(f"Agent output is not JSON; see {stdout_path}.") from error
    stop_reason = output.get("stopReason")
    log.info(
        "agent.finished",
        extra={
            "job_dir": str(job_dir),
            "seconds": duration,
            "stop_reason": stop_reason,
            "turns": output.get("num_turns"),
            "cost_usd": output.get("total_cost_usd"),
        },
    )
    if stop_reason != "end_turn":
        raise AgentRunError(f"Agent stopped with reason {stop_reason!r}; see {stdout_path}.")
