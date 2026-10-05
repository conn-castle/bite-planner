"""The agent harness: the agent (Grok, through Agent Layer) run on a prompt in a
job directory, with the job's MCP tools as its only tools, one agent at a time;
and its sign-in to its subscription. It knows nothing of jobs or the database."""

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
from contextlib import contextmanager, nullcontext
from pathlib import Path
from string import Template
from typing import TypedDict

from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.server import Tools
from pydantic import BaseModel

from castle_chef import instacart
from castle_chef.settings import Settings, get_settings

log = logging.getLogger(__name__)

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


class AgentRunError(Exception):
    pass


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


class _AgentUser(TypedDict):
    user: int | None
    group: int | None
    extra_groups: list[int] | None


def _agent_user(settings: Settings) -> _AgentUser:
    """Who the agent runs as: the agent user (settings.agent_uid), unprivileged
    and in no other group, so it can't read the worker's environment (the
    database URL); unset, the worker's own user."""
    uid = settings.agent_uid
    return {"user": uid, "group": uid, "extra_groups": [] if uid is not None else None}


def _agent_env(settings: Settings, **variables: str) -> dict[str, str]:
    """The agent's environment: the variables given, and those passed through
    from the worker's (settings.agent_env). Nothing else: no database
    credentials or other secrets reach the agent."""
    names = ["LANG", *settings.agent_env]
    return variables | {name: os.environ[name] for name in names if name in os.environ}


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
    env = _agent_env(
        settings,
        # Agent Layer writes some clients' settings under HOME: somewhere the
        # agent user can write.
        HOME=str(project) if settings.agent_uid is not None else os.environ.get("HOME", "/tmp"),
        PATH=os.environ.get("PATH", os.defpath),
    )
    command = [*shlex.split(required_agent_command()), "login", "--device-auth"]
    with agent_lock(provider=provider):
        log.info("agent.login_started", extra={"provider": provider})
        completed = subprocess.run(
            command,
            cwd=project,
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
            **_agent_user(settings),
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
    job_dir: Path,
    prompt: str,
    tools: Tools,
    context: BaseModel,
    tool_args: list[str] | None = None,
    agent_locked: bool = False,
) -> None:
    """Run the agent on the prompt in a new job directory, with the job's tools (and
    their server's extra arguments) and the context they check against. It holds
    the agent's lock while it runs, unless the caller already does (`agent_locked`)."""
    settings = get_settings()
    agent_command = required_agent_command()
    job_dir.mkdir(mode=0o700, parents=True)
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
    env = _agent_env(
        settings,
        HOME=str(job_dir)
        if settings.agent_uid is not None
        else os.environ.get("HOME", str(job_dir)),
        PATH=settings.agent_path or f"{venv_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
    )
    stdout_path = job_dir / "agent-output.json"
    stderr_path = job_dir / "agent-stderr.log"
    # The job directory is named after the job, which ties these events to it.
    with (
        nullcontext() if agent_locked else agent_lock(job_id=job_dir.name),
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
            # No other open files: it can't read anything the worker holds.
            close_fds=True,
            **_agent_user(settings),
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
