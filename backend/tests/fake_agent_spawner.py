"""Stands in for an agent whose MCP server starts a new process group and hangs.

Usage (as CASTLE_CHEF_AGENT_COMMAND): fake_agent_spawner.py, followed by the
runner arguments the worker appends. Writes the child's pid to `child.pid` in the
job directory, then waits (the worker's timeout must end it).
"""

import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
job_dir = Path(args[args.index("--cwd") + 1])
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
)
(job_dir / "child.pid").write_text(str(child.pid))
time.sleep(120)
