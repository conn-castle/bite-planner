"""Runs one job: the agent harness in a job directory, then its meals saved."""

import fcntl
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from string import Template
from uuid import UUID

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from pydantic import ValidationError

from castle_chef.db import get_engine, get_session
from castle_chef.jobs import GENERATE_MEALS, GenerateMealsParams, claim_job, finish_job
from castle_chef.meals import MealInput, add_meals, load_meals
from castle_chef.models import Job
from castle_chef.settings import REPO_ROOT, get_settings

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Grok flags that make the run bare: shell as the only tool and no subagents.
# `al grok` supplies the model, reasoning effort, and `--no-memory` from
# .agent-layer/config.toml. Grok's headless mode auto-approves every tool call,
# so the sandbox profile below (or, in the worker container, the container) is
# what limits the agent.
SANDBOX_PROFILE = "castle-chef-agent"
GROK_ARGS = [
    "--output-format", "json",
    "--tools", "run_terminal_cmd",
    "--disallowed-tools", "Agent,search_tool,use_tool",
    "--max-turns", "60",
]  # fmt: skip

# `strict`: read and write the job directory, read system paths, no network for
# commands. The agent may also read what `castle-chef meal add` needs to run,
# but nothing else in the repo (e.g. backend/.env) or the home directory.
SANDBOX_TOML = Template("""[profiles.$name]
extends = "strict"
read_only = [$read_only]
""")


# Exit status when the database is behind this code: the release's migration
# did not run (in AWS, the failure handler reports the stopped task).
SCHEMA_BEHIND_EXIT_CODE = 78


class AgentRunError(Exception):
    pass


class SchemaBehindError(Exception):
    pass


def run_job(job_id: UUID) -> None:
    """Run one job: claim it, run the agent, and save its meals (or its failure).

    Raises LookupError if the job doesn't exist and SchemaBehindError if the
    database is behind this code; a job someone else already claimed is left alone.
    """
    check_schema()
    fields = {"job_id": str(job_id)}
    log.info("worker.started", extra=current_release() | fields)
    with get_session() as session:
        job = claim_job(session, job_id)
    if job is None:
        log.warning("job.already_claimed", extra=fields)
        return
    log.info("job.claimed", extra=fields | {"kind": job.kind, "params": job.params})
    try:
        meals = process_job(job)
    except (AgentRunError, ValidationError) as error:
        log.error("job.failed", extra=fields | {"error": str(error)})
        _finish(job_id, error=str(error))
        return
    except Exception as error:
        log.exception("job.crashed", extra=fields)
        _finish(job_id, error=f"Unexpected worker error: {error!r}")
        return
    try:
        with get_session() as session:
            # The meals and the job's success are committed together, and only if
            # the job is still running (not failed as stale meanwhile).
            add_meals(session, job_id, meals)
            if not finish_job(session, job_id):
                log.error("job.no_longer_running", extra=fields | {"meal_count": len(meals)})
                return
    except Exception as error:
        # If the commit went through despite the error (a lost acknowledgement),
        # the job is no longer running and this changes nothing.
        log.exception("job.save_failed", extra=fields)
        _finish(job_id, error=f"Could not save the meals: {error!r}")
        return
    log.info("job.succeeded", extra=fields | {"meal_count": len(meals)})


def _finish(job_id: UUID, error: str) -> None:
    with get_session() as session:
        if not finish_job(session, job_id, error=error):
            log.error("job.no_longer_running", extra={"job_id": str(job_id)})


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
    """The release (`r<N>`) and commit this code is, from the image's environment
    (CASTLE_CHEF_RELEASE, CASTLE_CHEF_COMMIT, set at build) or else from git:
    the checkout's release tag, or `dev-<sha>` if untagged."""
    if "CASTLE_CHEF_COMMIT" in os.environ:
        return {
            "release": os.environ.get("CASTLE_CHEF_RELEASE", "unknown"),
            "commit": os.environ["CASTLE_CHEF_COMMIT"],
        }

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check
        )

    commit = git("rev-parse", "HEAD").stdout.strip()
    tag = git("describe", "--exact-match", "--tags", "--match", "r[0-9]*", "HEAD", check=False)
    release = tag.stdout.strip() if tag.returncode == 0 else f"dev-{commit[:7]}"
    return {"release": release, "commit": commit}


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


def prepare_agent_project() -> Path:
    """The Agent Layer project to launch the agent from: the repository, or (in
    the worker container) a fresh writable copy of the image's template owned
    by the agent user."""
    settings = get_settings()
    project = settings.agent_project_dir
    if settings.agent_uid is not None:
        # Fargate's empty task volumes hide the image's /tmp permissions; the
        # agent's tools need a shared, sticky /tmp.
        os.chmod("/tmp", 0o1777)
    if settings.agent_project_template is not None:
        shutil.copytree(settings.agent_project_template, project, dirs_exist_ok=True)
        if settings.agent_uid is not None:
            for path in [project, *project.rglob("*")]:
                os.chown(path, settings.agent_uid, settings.agent_uid)
    return project


def login_agent(provider: str) -> int:
    """Sign the agent in to its subscription with a device code (the link and
    code are printed; the owner approves in a browser). Runs as the agent user,
    so the login (on EFS in AWS, via GROK_AUTH_PATH) is owned by it. Returns
    the login command's exit status."""
    if provider != "grok":
        raise ValueError(f"Unsupported agent provider {provider!r}; only grok is set up.")
    settings = get_settings()
    project = prepare_agent_project()
    env = {
        # Agent Layer writes some clients' settings under HOME: somewhere the
        # agent user can write.
        "HOME": str(project) if settings.agent_uid is not None else os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", os.defpath),
    }
    for name in ["LANG", *settings.agent_env]:
        if name in os.environ:
            env[name] = os.environ[name]
    command = [*shlex.split(settings.agent_command), "login", "--device-auth"]
    with agent_lock(provider=provider):
        log.info("agent.login_started", extra={"provider": provider})
        completed = subprocess.run(
            command,
            cwd=project,
            env=env,
            stdin=subprocess.DEVNULL,
            user=settings.agent_uid,
            group=settings.agent_uid,
            extra_groups=[] if settings.agent_uid is not None else None,
            check=False,
        )
    event = "agent.login_finished" if completed.returncode == 0 else "agent.login_failed"
    (log.info if completed.returncode == 0 else log.error)(
        event, extra={"provider": provider, "returncode": completed.returncode}
    )
    return completed.returncode


@contextmanager
def agent_lock(**fields: str) -> Iterator[None]:
    """Hold the agent provider's lock (settings.agent_lock_file) while the agent
    runs or signs in, so parallel jobs take turns with the one subscription login."""
    path = get_settings().agent_lock_file
    if path is None:
        yield
        return
    started = time.monotonic()
    with path.open("a") as lock:  # closing it releases the lock
        fcntl.flock(lock, fcntl.LOCK_EX)
        log.info(
            "agent.lock_acquired",
            extra=fields | {"waited_seconds": round(time.monotonic() - started, 1)},
        )
        yield


def run_agent(job_dir: Path, prompt: str) -> None:
    settings = get_settings()
    job_dir.mkdir(parents=True)
    prompt_path = job_dir / "prompt.md"
    prompt_path.write_text(prompt)
    venv_bin = Path(sys.executable).parent
    sandbox_args: list[str] = []
    if settings.agent_sandbox:
        read_only = [Path(sys.prefix), Path(__file__).resolve().parents[1]]
        (job_dir / ".grok").mkdir()
        (job_dir / ".grok" / "sandbox.toml").write_text(
            SANDBOX_TOML.substitute(
                name=SANDBOX_PROFILE, read_only=", ".join(json.dumps(str(p)) for p in read_only)
            )
        )
        sandbox_args = ["--sandbox", SANDBOX_PROFILE]
    project = prepare_agent_project()
    if settings.agent_uid is not None:
        # The agent writes its meals into the job directory.
        for path in [job_dir, *job_dir.rglob("*")]:
            os.chown(path, settings.agent_uid, settings.agent_uid)

    command = [
        *shlex.split(settings.agent_command),
        "--cwd", str(job_dir),
        "--prompt-file", str(prompt_path),
        *GROK_ARGS,
        *sandbox_args,
    ]  # fmt: skip
    # A minimal environment: no database credentials or other secrets reach the agent.
    env = {
        "HOME": str(job_dir)
        if settings.agent_uid is not None
        else os.environ.get("HOME", str(job_dir)),
        # Puts this environment's `castle-chef` executable first on the agent's PATH.
        "PATH": f"{venv_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        "CASTLE_CHEF_JOB_DIR": str(job_dir),
    }
    for name in ["LANG", *settings.agent_env]:
        if name in os.environ:
            env[name] = os.environ[name]
    stdout_path = job_dir / "agent-output.json"
    stderr_path = job_dir / "agent-stderr.log"
    # The job directory is named after the job, which ties these events to it.
    with (
        agent_lock(job_id=job_dir.name),
        stdout_path.open("w") as stdout,
        stderr_path.open("w") as stderr,
    ):
        log.info("agent.started", extra={"job_id": job_dir.name, "job_dir": str(job_dir)})
        started = time.monotonic()
        # Its own process group, so a timeout kills everything the agent started.
        agent = subprocess.Popen(
            command,
            cwd=project,  # `al` resolves the Agent Layer project from here.
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            # Unprivileged, and no other open files: it can't read the worker's
            # environment (the database URL) or anything the worker holds.
            user=settings.agent_uid,
            group=settings.agent_uid,
            extra_groups=[] if settings.agent_uid is not None else None,
            close_fds=True,
        )
        try:
            returncode = agent.wait(timeout=settings.agent_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            os.killpg(agent.pid, signal.SIGKILL)
            agent.wait()
            raise AgentRunError(
                f"Agent timed out after {settings.agent_timeout_seconds}s; see {job_dir}."
            ) from error

    duration = round(time.monotonic() - started, 1)
    if returncode != 0:
        log.error(
            "agent.exited",
            extra={
                "job_id": job_dir.name,
                "job_dir": str(job_dir),
                "returncode": returncode,
                "seconds": duration,
            },
        )
        raise AgentRunError(f"Agent exited with code {returncode}; see {stderr_path}.")
    try:
        output = json.loads(stdout_path.read_text())
    except json.JSONDecodeError as error:
        raise AgentRunError(f"Agent output is not JSON; see {stdout_path}.") from error
    stop_reason = output.get("stopReason")
    log.info(
        "agent.finished",
        extra={
            "job_id": job_dir.name,
            "job_dir": str(job_dir),
            "seconds": duration,
            "stop_reason": stop_reason,
            "turns": output.get("num_turns"),
            "cost_usd": output.get("total_cost_usd"),
        },
    )
    if stop_reason != "end_turn":
        raise AgentRunError(f"Agent stopped with reason {stop_reason!r}; see {stdout_path}.")
