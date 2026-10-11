"""Run the OpenAI agent with one job's MCP tools and no database credentials."""

import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TypedDict

from castle_chef_agent.jobfiles import write_context
from castle_chef_agent.runner import AgentSummary, ToolServerConfig
from castle_chef_agent.server import Tools
from pydantic import BaseModel, ValidationError

from castle_chef import instacart
from castle_chef.settings import Settings, get_settings

log = logging.getLogger(__name__)
TIMEOUT_GRACE_SECONDS = 8


class AgentRunError(Exception):
    pass


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
    from the worker's (settings.agent_env). The project-scoped OpenAI key is
    included; database credentials and other secrets are not."""
    names = ["LANG", *settings.agent_env]
    return variables | {name: os.environ[name] for name in names if name in os.environ}


def _process_groups(pid: int) -> set[int]:
    """The runner's groups, including MCP servers that start a new session."""
    pending = [pid]
    visited: set[int] = set()
    groups: set[int] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            groups.add(os.getpgid(current))
            children = Path(f"/proc/{current}/task/{current}/children").read_text()
        except OSError, ValueError:  # a process exited while we inspected it
            continue
        pending.extend(int(child) for child in children.split())
    groups.discard(os.getpgrp())
    return groups


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_timed_out_agent(agent: subprocess.Popen[bytes]) -> None:
    groups = _process_groups(agent.pid)
    with suppress(ProcessLookupError):
        # asyncio.run cancels its task on SIGINT, letting MCP close the tool
        # server and its own process group. The fallback covers a stuck runner.
        os.killpg(agent.pid, signal.SIGINT)
    deadline = time.monotonic() + TIMEOUT_GRACE_SECONDS
    while time.monotonic() < deadline:
        if agent.poll() is not None and not any(_group_alive(group) for group in groups):
            break
        time.sleep(0.05)
    groups.update(_process_groups(agent.pid))
    for group in groups:
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
    agent.wait()


def login_agent(provider: str) -> int:
    """Sign Codex in to the household's ChatGPT and Instacart connection."""
    if provider == "codex":
        return instacart.login()
    raise ValueError(f"Unsupported agent provider {provider!r}: codex.")


def required_agent_command() -> str:
    """The Responses runner's command; real agents run only in the worker image."""
    command = get_settings().agent_command
    if command is None:
        raise AgentRunError(
            "No OpenAI agent command here: a real agent runs only in the worker image "
            "(make worker-job JOB=<id> runs one locally)."
        )
    return command


def run_agent(
    job_dir: Path,
    prompt: str,
    tools: Tools,
    context: BaseModel,
    tool_args: list[str] | None = None,
) -> None:
    """Run the agent with the job's tools and checked context."""
    settings = get_settings()
    agent_command = required_agent_command()
    job_dir.mkdir(mode=0o700, parents=True)
    prompt_path = job_dir / "prompt.md"
    prompt_path.write_text(prompt)
    write_context(job_dir, context)
    (job_dir / "tools.json").write_text(
        ToolServerConfig(
            command="castle-chef",
            args=["mcp", tools.value, "--job-dir", str(job_dir), *(tool_args or [])],
        ).model_dump_json()
    )
    venv_bin = Path(sys.executable).parent
    if settings.agent_uid is not None:
        # The agent and its tools write results into the job directory.
        os.chmod("/tmp", 0o1777)
        for path in [job_dir, *job_dir.rglob("*")]:
            os.chown(path, settings.agent_uid, settings.agent_uid)

    command = [
        *shlex.split(agent_command),
        "--cwd", str(job_dir),
        "--prompt-file", str(prompt_path),
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
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        log.info("agent.started", extra={"job_id": job_dir.name, "job_dir": str(job_dir)})
        started = time.monotonic()
        # Its own process group; the MCP server makes another, collected on timeout.
        agent = subprocess.Popen(
            command,
            cwd=job_dir,
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
            _stop_timed_out_agent(agent)
            raise AgentRunError(
                f"Agent timed out after {settings.agent_timeout_seconds}s; see {job_dir}."
            ) from error

    duration = round(time.monotonic() - started, 1)
    if returncode != 0:
        lines = stderr_path.read_text().strip().splitlines()
        detail = lines[-1] if lines else None
        log.error(
            "agent.exited",
            extra={
                "job_id": job_dir.name,
                "job_dir": str(job_dir),
                "returncode": returncode,
                "seconds": duration,
                "detail": detail,
            },
        )
        message = f"Agent exited with code {returncode}"
        if detail:
            message += f": {detail}"
        raise AgentRunError(f"{message}; see {stderr_path}.")
    try:
        output = AgentSummary.model_validate_json(stdout_path.read_text())
    except ValidationError as error:
        raise AgentRunError(f"Agent output is not a valid summary; see {stdout_path}.") from error
    stop_reason = output.stop_reason
    log.info(
        "agent.finished",
        extra={
            "job_id": job_dir.name,
            "job_dir": str(job_dir),
            "seconds": duration,
            "stop_reason": stop_reason,
            "turns": output.turns,
            "model": output.model,
            "usage": output.usage.model_dump() if output.usage else None,
            "response_id": output.response_id,
        },
    )
    if stop_reason != "end_turn":
        raise AgentRunError(f"Agent stopped with reason {stop_reason!r}; see {stdout_path}.")
