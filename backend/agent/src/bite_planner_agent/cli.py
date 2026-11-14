"""`bite-planner mcp`: serves a job's tools to the agent. The backend's `bite-planner`
mounts the same command; in the worker image the agent's `bite-planner` is this app alone."""

from pathlib import Path
from typing import Annotated

import typer

from bite_planner_agent.runner import main as run_agent
from bite_planner_agent.server import InstacartSettings, Tools, serve

app = typer.Typer(no_args_is_help=True)


# A callback keeps the app a group with one command: without it, Typer makes the app that
# command, and the worker's `bite-planner mcp <tools>` would take `mcp` for the tools.
@app.callback()
def main() -> None:
    """A Bite Planner job's tools for its agent."""


@app.command("mcp")
def mcp_serve(
    tools: Annotated[Tools, typer.Argument(help="Which job's tools to offer.")],
    job_dir: Annotated[
        Path, typer.Option(exists=True, file_okay=False, help="The job's directory.")
    ],
    codex_home: Annotated[
        Path | None, typer.Option(help="The Codex login (products tools).")
    ] = None,
    codex_lock_file: Annotated[Path | None, typer.Option(help="Its lock.")] = None,
    codex_command: Annotated[str, typer.Option(help="How to start Codex.")] = "codex",
    store: Annotated[str, typer.Option(help="The Instacart store.")] = "Wegmans",
) -> None:
    """Serve the job's tools to the agent's harness over MCP (stdio)."""
    instacart = (
        InstacartSettings(
            codex_home=codex_home, lock_file=codex_lock_file, command=codex_command, store=store
        )
        if codex_home is not None
        else None
    )
    serve(tools, job_dir, instacart)


@app.command("run-agent")
def run_openai_agent(
    cwd: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    prompt_file: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Run a job with GPT-6.1 Sol and only that job's MCP tools."""
    raise typer.Exit(run_agent(cwd, prompt_file))
