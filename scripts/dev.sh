#!/usr/bin/env bash
# Runs the API (http://localhost:8000) and the app dev server (https://localhost:5173,
# proxying /api to the API) against the Neon dev branch. Ctrl+C, or either one
# exiting, stops both. Jobs already started keep running to the end, each in its
# own worker container.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }

# Jobs run in the worker image, as on Fargate (scripts/worker-docker.sh): build it
# from the working tree first.
echo "Building the worker image…"
scripts/worker-docker.sh build >/dev/null

# Each server gets its own process group, so stopping it stops its children too
# (uvicorn's reloader, vite). No stdin: a background process group reading the
# terminal would be stopped.
set -m
(cd backend && exec uv run uvicorn castle_chef.api:app --reload) </dev/null &
api=$!
(cd apps/mobile && exec pnpm dev) </dev/null &
web=$!
trap 'kill -- -"$api" -"$web" 2>/dev/null || true; wait' EXIT
trap 'exit 130' INT TERM
wait -n
