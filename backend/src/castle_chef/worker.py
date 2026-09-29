"""Runs one job: the agent harness in a job directory, then its result (meals, or
a shopping list) saved."""

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
from dataclasses import dataclass
from pathlib import Path
from string import Template
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.meals import MealInput, MealsJob, load_meals, meal_problems
from castle_chef_agent.products import ItemChoice
from castle_chef_agent.server import Tools
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from castle_chef import instacart, products, push, ratings, shopping
from castle_chef.db import get_engine, get_session
from castle_chef.jobs import SUPERSEDED_ERROR, claim_job, create_shop, finish_job
from castle_chef.meals import add_meals
from castle_chef.models import GroceryOrder, Job, JobKind, ListSource
from castle_chef.notifications import ideas_ready, list_failed, list_ready
from castle_chef.products import InvalidProducts
from castle_chef.settings import REPO_ROOT, get_settings
from castle_chef.shopping import InvalidList, ListEntry

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Grok flags that make the run bare: the job's MCP tools are its only tools (no
# shell, no files, no subagents). Grok reaches MCP tools through its search_tool
# and use_tool, and starts the job directory's MCP server only in a trusted
# folder. `al grok` supplies the model, reasoning effort, and `--no-memory` from
# its project's .agent-layer/config.toml. Grok's headless mode auto-approves every
# tool call; the worker container limits the MCP server.
GROK_ARGS = [
    "--output-format", "json",
    "--tools", "search_tool,use_tool",
    "--trust",
    "--max-turns", "60",
]  # fmt: skip

# The job's tools, as an MCP server Grok starts from the job directory's project
# config: the agent's only tools (it gets no shell).
MCP_TOML = Template("""[mcp_servers.castle-chef]
command = "castle-chef"
args = $args
""")


# Exit status when the database is behind this code: the release's migration
# did not run (in AWS, the failure handler reports the stopped task).
SCHEMA_BEHIND_EXIT_CODE = 78


class AgentRunError(Exception):
    pass


class SchemaBehindError(Exception):
    pass


class JobDiscarded(Exception):
    """The job's draft was discarded (with the job) after it was claimed."""


@dataclass(frozen=True)
class Meals:
    """A generate_meals job's result."""

    meals: list[MealInput]


@dataclass(frozen=True)
class ListItems:
    """A build_list job's result: the checked items and staples, and the picks they're for."""

    items: list[ListEntry]
    source: ListSource


@dataclass(frozen=True)
class Products:
    """A shop job's result: each numbered item's product and alternatives, for a build's list."""

    build_id: UUID
    items: dict[int, UUID]
    choices: dict[int, ItemChoice]


type JobResult = Meals | ListItems | Products


def run_job(job_id: UUID) -> None:
    """Run one job: claim it, run the agent, and save its meals (or its failure).

    Raises SchemaBehindError if the database is behind this code. A job someone
    else already claimed is left alone, and a job that no longer exists was
    discarded with its draft, which is not an error.
    """
    check_schema()
    fields = {"job_id": str(job_id)}
    log.info("worker.started", extra=current_release() | fields)
    try:
        with get_session() as session:
            job = claim_job(session, job_id)
    except LookupError:
        log.info("job.discarded", extra=fields)
        return
    if job is None:
        log.warning("job.already_claimed", extra=fields)
        return
    log.info("job.claimed", extra=fields | {"kind": job.kind})
    try:
        result = process_job(job)
    except JobDiscarded:
        log.info("job.discarded", extra=fields)
        return
    except (AgentRunError, ValidationError, InvalidList, InvalidProducts) as error:
        log.error("job.failed", extra=fields | {"error": str(error)})
        _finish(job_id, error=str(error))
        _notify_failed(job)
        return
    except Exception as error:
        log.exception("job.crashed", extra=fields)
        _finish(job_id, error=f"Unexpected worker error: {error!r}")
        _notify_failed(job)
        return
    counts = (
        {"meal_count": len(result.meals)}
        if isinstance(result, Meals)
        else {"item_count": len(result.items)}
    )
    shop_id: UUID | None = None
    try:
        with get_session() as session:
            # Locking the job keeps its draft from being discarded mid-save; a job
            # already gone was discarded with its draft, which is not an error.
            if session.exec(select(Job).where(Job.id == job_id).with_for_update()).first() is None:
                log.info("job.discarded", extra=fields | counts)
                return
            # The result and the job's success are committed together, and only
            # if the job is still running (not failed as stale, or as superseded
            # by a newer list, meanwhile).
            if isinstance(result, Meals):
                add_meals(session, job_id, result.meals)
            elif isinstance(result, Products):
                newest = shopping.newest_build(session, job.grocery_order_id)
                if (
                    newest is None
                    or newest.id != result.build_id
                    or not shopping.is_current(session, job.grocery_order_id, newest)
                ):
                    # Its list was replaced (the picks changed) while it shopped.
                    finish_job(session, job_id, error=SUPERSEDED_ERROR)
                    log.info("job.superseded", extra=fields | counts)
                    return
                products.save_choices(session, result.items, result.choices)
            else:
                shopping.add_items(session, job_id, result.items, result.source)
                # The list is shopped next, in this task (one wait for the member).
                shop_id = create_shop(session, job, uuid4(), job.requested_by).id
            if not finish_job(session, job_id):
                job = session.get(Job, job_id)
                if job is not None and job.error == SUPERSEDED_ERROR:
                    # A newer list for other picks replaced it: expected.
                    log.info("job.superseded", extra=fields | counts)
                else:
                    log.error("job.no_longer_running", extra=fields | counts)
                return
    except Exception as error:
        # If the commit went through despite the error (a lost acknowledgement),
        # the job is no longer running and this changes nothing.
        log.exception("job.save_failed", extra=fields)
        _finish(job_id, error=f"Could not save the result: {error!r}")
        return
    log.info("job.succeeded", extra=fields | counts)
    if shop_id is not None:
        run_job(shop_id)
        return
    _notify_requester(job_id)


def _notify_requester(job_id: UUID) -> None:
    """ "Your meal ideas are ready", or "Your shopping list is ready" while it's
    still the list for the order's picks, to whoever started the job. The job's
    success stands whatever happens here; a failure is logged as an error."""
    try:
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None:  # discarded with its draft meanwhile
                return
            if job.kind == JobKind.GENERATE_MEALS:
                notification = ideas_ready(session, job)
            else:
                # A list is ready once it's shopped, while it's still for the picks.
                build = session.get(Job, job.build_id) if job.kind == JobKind.SHOP else None
                newest = shopping.newest_build(session, job.grocery_order_id)
                if (
                    build is None
                    or newest is None
                    or newest.id != build.id
                    or not shopping.is_current(session, job.grocery_order_id, newest)
                ):
                    log.info("job.notify_skipped", extra={"job_id": str(job_id)})
                    return
                notification = list_ready(session, build)
            requester = job.requested_by
        push.notify(requester, notification)
    except Exception:
        log.exception("job.notify_failed", extra={"job_id": str(job_id)})


def _notify_failed(job: Job) -> None:
    """ "Couldn't shop your list", to whoever is waiting for it, while it's still the
    list for the order's picks (a failed batch or build shows in the app). A
    failure here is logged as an error."""
    if job.kind != JobKind.SHOP or job.build_id is None:
        return
    try:
        with get_session() as session:
            build = session.get(Job, job.build_id)
            newest = shopping.newest_build(session, job.grocery_order_id)
            if build is None or newest is None or newest.id != build.id:
                return
            if not shopping.is_current(session, job.grocery_order_id, newest):
                return
            notification = list_failed(session, build)
        push.notify(job.requested_by, notification)
    except Exception:
        log.exception("job.notify_failed", extra={"job_id": str(job.id)})


def _finish(job_id: UUID, error: str) -> None:
    with get_session() as session:
        if finish_job(session, job_id, error=error):
            return
        if session.get(Job, job_id) is None:
            log.info("job.discarded", extra={"job_id": str(job_id)})
        else:
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
    """The release (`YYYY.M.D.N`) and commit this code is, from the environment
    (CASTLE_CHEF_COMMIT set in the image, CASTLE_CHEF_RELEASE by the deploy) or
    else from git: the checkout's release tag, or `dev-<sha>` if untagged."""
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
    # Release tags: YYYY.M.D.N, and r<N> before 2026-09-24.
    tag = git(
        "describe", "--exact-match", "--tags", "--match", "20[0-9][0-9].*", "--match", "r[0-9]*",
        "HEAD", check=False,
    )  # fmt: skip
    release = tag.stdout.strip() if tag.returncode == 0 else f"dev-{commit[:7]}"
    return {"release": release, "commit": commit}


def process_job(job: Job) -> JobResult:
    settings = get_settings()
    job_dir = settings.jobs_dir / str(job.id)
    if job.kind == JobKind.SHOP:
        if settings.codex_home is None:
            raise AgentRunError(
                "No Codex login here (CASTLE_CHEF_CODEX_HOME): can't reach Instacart."
            )
        with get_session() as session:
            build = session.get(Job, job.build_id) if job.build_id else None
            if build is None:
                raise JobDiscarded
            context = products.shop_context(session, build, settings.instacart_store)
        lock = settings.codex_lock_file
        instacart_args = [
            "--codex-home", str(settings.codex_home),
            *(["--codex-lock-file", str(lock)] if lock else []),
            "--codex-command", settings.codex_command,
            "--store", settings.instacart_store,
        ]  # fmt: skip
        run_agent(job_dir, context.prompt, Tools.PRODUCTS, context.job, instacart_args)
        # Checked against the items as the worker numbered them.
        return Products(
            build_id=build.id,
            items=context.items,
            choices=products.finished_choices(job_dir, context.job),
        )
    if job.kind == JobKind.BUILD_LIST:
        if job.list_source is None:
            raise AgentRunError(f"List job {job.id} has no picks to build from.")
        with get_session() as session:
            scaled = shopping.scaled_meals(session, job.list_source)
        lines = shopping.list_job(scaled)
        run_agent(job_dir, shopping.build_list_prompt(scaled), Tools.SHOPPING_LIST, lines)
        # Checked against the lines as the worker made them: the agent can write job.json.
        return ListItems(items=shopping.finished_list(job_dir, lines), source=job.list_source)

    count = get_settings().meals_per_batch
    with get_session() as session:
        prompt = generate_meals_prompt(session, job, count)
    run_agent(job_dir, prompt, Tools.MEALS, MealsJob(count=count))
    if problems := meal_problems(job_dir, count):
        raise AgentRunError(f"The agent didn't finish: {' '.join(problems)}")
    return Meals(meals=load_meals(job_dir))


def generate_meals_prompt(session: Session, job: Job, count: int) -> str:
    """The agent's instructions for a batch of `count` meals for the job's
    household, which avoid meals like the ones it marked "don't show meals like
    this". Raises JobDiscarded if the job's draft is gone."""
    household_id = session.exec(
        select(GroceryOrder.household_id).where(GroceryOrder.id == job.grocery_order_id)
    ).first()
    if household_id is None:
        raise JobDiscarded(f"Job {job.id}'s draft was discarded.")
    avoid = ""
    if not_for_us := ratings.not_for_us_meals(session, household_id):
        listed = "\n".join(
            f"- {meal.name}: {meal.description}" if meal.description else f"- {meal.name}"
            for meal in not_for_us
        )
        avoid = (
            'The family marked these meals "don\'t show meals like this". '
            f"Don't suggest meals like them:\n\n{listed}\n\n"
        )
    return Template(PROMPTS_DIR.joinpath("generate_meals.md").read_text()).substitute(
        count=count,
        avoid=avoid,
    )


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
    if provider == "codex":
        # Codex reaches Instacart for the worker and the agent's tools; it isn't
        # the job's agent.
        return instacart.login()
    if provider != "grok":
        raise ValueError(f"Unsupported agent provider {provider!r}: grok or codex.")
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
    command = [*shlex.split(required_agent_command()), "login", "--device-auth"]
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


def required_agent_command() -> str:
    """The agent harness's command; real agents run only in the worker image."""
    command = get_settings().agent_command
    if command is None:
        raise AgentRunError(
            "No agent command here: a real agent runs only in the worker image "
            "(make worker-job JOB=<id> runs one locally)."
        )
    return command


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


def run_agent(
    job_dir: Path, prompt: str, tools: Tools, context: BaseModel, tool_args: list[str] | None = None
) -> None:
    """Run the agent on the prompt in a new job directory, with the job's tools (and
    their server's extra arguments) and the context they check against."""
    settings = get_settings()
    agent_command = required_agent_command()
    job_dir.mkdir(parents=True)
    prompt_path = job_dir / "prompt.md"
    prompt_path.write_text(prompt)
    write_context(job_dir, context)
    (job_dir / ".grok").mkdir()
    (job_dir / ".grok" / "config.toml").write_text(
        MCP_TOML.substitute(
            args=json.dumps(["mcp", tools.value, "--job-dir", str(job_dir), *(tool_args or [])])
        )
    )
    venv_bin = Path(sys.executable).parent
    project = prepare_agent_project()
    if settings.agent_uid is not None:
        # The agent writes its meals into the job directory.
        for path in [job_dir, *job_dir.rglob("*")]:
            os.chown(path, settings.agent_uid, settings.agent_uid)

    command = [
        *shlex.split(agent_command),
        "--cwd", str(job_dir),
        "--prompt-file", str(prompt_path),
        *GROK_ARGS,
    ]  # fmt: skip
    # A minimal environment: no database credentials or other secrets reach the agent.
    env = {
        "HOME": str(job_dir)
        if settings.agent_uid is not None
        else os.environ.get("HOME", str(job_dir)),
        "PATH": settings.agent_path
        or f"{venv_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
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
