"""`castle-chef mcp`: serves a job's tools to the agent. The backend's `castle-chef`
mounts the same command; in the worker image the agent's `castle-chef` is this app alone."""

from pathlib import Path
from typing import Annotated

import typer

from castle_chef_agent.server import InstacartSettings, Tools, serve

app = typer.Typer(no_args_is_help=True)


# A callback keeps the app a group with one command: without it, Typer makes the app that
# command, and the worker's `castle-chef mcp <tools>` would take `mcp` for the tools.
@app.callback()
def main() -> None:
    """A Castle Chef job's tools for its agent."""


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
