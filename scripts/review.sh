#!/usr/bin/env bash
# Serves the app for a private UI review from a phone or computer on Tailscale,
# with the request-html-comments overlay for element-linked comments. Runs the
# API (http://localhost:8000) and the app dev server on loopback
# (http://127.0.0.1:5175) against Neon dev, like `make dev`, behind the review
# server over Tailscale HTTPS, so sign-in works as usual. Stops when the review
# is sent or cancelled (comments are written to .agent-layer/tmp/reviews/), on
# Ctrl+C, or if a server exits.
set -euo pipefail
cd "$(dirname "$0")/.."

# The public Tailscale HTTPS port, also used by Vite's HMR client.
readonly REVIEW_PORT=${BITE_PLANNER_REVIEW_PORT:-5174}
readonly APP_PORT=${BITE_PLANNER_APP_PORT:-5175} API_PORT=${BITE_PLANNER_API_PORT:-8000}
readonly REVIEW_TOOL=${BITE_PLANNER_REVIEW_TOOL:?Set BITE_PLANNER_REVIEW_TOOL to the installed html_review.mjs path}

[[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }
[[ -f $REVIEW_TOOL ]] || { echo "Missing $REVIEW_TOOL: set BITE_PLANNER_REVIEW_TOOL to an installed annotation tool" >&2; exit 1; }
# The final review's total reads the Instacart session from SSM (scripts/worker-docker.sh).
aws sts get-caller-identity >/dev/null 2>&1 \
  || { echo "Sign in to AWS first: aws sso login --profile ${AWS_PROFILE:?Set AWS_PROFILE} --use-device-code" >&2; exit 1; }

# Prepare the API's database with the same command as `make migrate` before any
# server starts: `/health` alone doesn't detect a schema behind this checkout.
(cd backend
  if [[ -n ${BITE_PLANNER_REVIEW_DATABASE_URL:-} ]]; then
    export DATABASE_URL=$BITE_PLANNER_REVIEW_DATABASE_URL
  fi
  uv run alembic upgrade head)

output=".agent-layer/tmp/reviews/review-$(date +%Y%m%d-%H%M%S).json"
mkdir -p "$(dirname "$output")"

# Jobs run in the worker image, as on Fargate (scripts/worker-docker.sh): build it
# from the working tree first.
echo "Building the worker image…"
scripts/worker-docker.sh build >/dev/null

# Each server gets its own process group, so stopping it stops its children too.
set -m
(cd backend
  if [[ -n ${BITE_PLANNER_REVIEW_DATABASE_URL:-} ]]; then
    export DATABASE_URL=$BITE_PLANNER_REVIEW_DATABASE_URL
  fi
  exec uv run uvicorn bite_planner.api:app --reload --port "$API_PORT") </dev/null &
api=$!
(cd apps/mobile && BITE_PLANNER_REVIEW_PORT=$REVIEW_PORT BITE_PLANNER_API_DEV_URL="http://127.0.0.1:$API_PORT" \
  exec pnpm exec vite --port "$APP_PORT" --strictPort) </dev/null &
web=$!
# Waits on the servers by pid, never a bare `wait`: a background child that a stop
# signal reaches between its fork and exec runs these traps with this shell's jobs,
# and a bare `wait` there retries a job that isn't its child forever (bash 5.3).
trap 'kill -- -"$api" -"$web" 2>/dev/null || true; wait "$api" "$web" 2>/dev/null || true' EXIT
trap 'exit 130' INT TERM

# Both servers answer before the review starts; one that exited stops it here (e.g.
# a port in use), before anything else is started.
ready() { curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/health" && curl -sf -o /dev/null "http://127.0.0.1:$APP_PORT/"; }
for _ in $(seq 60); do
  ready && break
  kill -0 "$api" 2>/dev/null || { echo "The API exited" >&2; exit 1; }
  kill -0 "$web" 2>/dev/null || { echo "The app dev server exited" >&2; exit 1; }
  sleep 0.5
done
ready || { echo "The API or the app dev server did not start" >&2; exit 1; }

echo "Connect the reviewing device to Tailscale; the review tool will print its verified private HTTPS URL."
echo "Comments will be written to $output"
node "$REVIEW_TOOL" "http://127.0.0.1:$APP_PORT/" --no-open --tailscale \
  --tailscale-port "$REVIEW_PORT" --output "$output" </dev/null &
review=$!
trap 'kill -- -"$api" -"$web" -"$review" 2>/dev/null || true; wait "$api" "$web" "$review" 2>/dev/null || true' EXIT

# The review tool exits 0 when the review is sent, 2 when cancelled.
status=0
wait -n -p finished "$api" "$web" "$review" || status=$?
if [[ $finished != "$review" ]]; then
  echo "A server exited (status $status); stopping the review" >&2
  exit 1
fi
exit "$status"
