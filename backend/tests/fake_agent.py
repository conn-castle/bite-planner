"""Stands in for `al grok` in worker tests.

Usage (as CASTLE_CHEF_AGENT_COMMAND): fake_agent.py MEALS STOP_REASON EXIT_CODE SECONDS
followed by the Grok arguments the worker appends. Adds MEALS meals through the
real `castle-chef meal add` command, takes SECONDS, and prints Grok-style JSON. Settings are
arguments because the worker gives the agent a minimal environment.
"""

import json
import os
import subprocess
import sys
import time

MEAL = {
    "name": "Sheet Pan Sausage and Peppers",
    "servings": 4,
    "ingredients": [
        {"name": "Italian sausage", "quantity": 1.5, "unit": "lb"},
        {"name": "bell peppers", "quantity": 3, "preparation": "sliced"},
    ],
    "steps": ["Roast everything at 425F for 25 minutes."],
}

meal_count, stop_reason, exit_code, seconds, *args = sys.argv[1:]
cwd = args[args.index("--cwd") + 1]
with open(args[args.index("--prompt-file") + 1]) as file:
    prompt = file.read()
with open(os.path.join(cwd, "fake-agent-prompt.md"), "w") as file:
    file.write(prompt)
with open(os.path.join(cwd, "fake-agent-env.json"), "w") as file:
    json.dump(sorted(os.environ), file)

for index in range(int(meal_count)):
    meal = MEAL | {"name": f"{MEAL['name']} {index}"}
    subprocess.run(
        ["castle-chef", "meal", "add"],
        input=json.dumps(meal),
        text=True,
        stdout=subprocess.DEVNULL,  # Grok captures tool output; keep stdout for the final JSON.
        check=True,
        cwd=cwd,
    )

time.sleep(float(seconds))
print(json.dumps({"text": "done", "stopReason": stop_reason}))
sys.exit(int(exit_code))
