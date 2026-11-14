#!/usr/bin/env bash
# One canonical setup path: private consumer files generated from AWS SSM.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec uv run python -m bite_planner.configure local
