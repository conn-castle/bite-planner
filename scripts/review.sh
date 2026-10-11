#!/usr/bin/env bash
# Serves the app for a UI review from a phone or computer on the local network,
# with the request-html-comments overlay for element-linked comments. Runs the
# API (http://localhost:8000) and the app dev server on loopback
# (http://127.0.0.1:5175) against Neon dev, like `make dev`, behind the review
# server on this machine's LAN address over HTTPS (https://<LAN IP>:5174), so
# sign-in works as usual. Stops when the review is sent or cancelled (comments
# are written to .agent-layer/tmp/reviews/), on Ctrl+C, or if a server exits.
set -euo pipefail
cd "$(dirname "$0")/.."

readonly REVIEW_PORT=5174 APP_PORT=5175 API_PORT=8000
readonly REVIEW_TOOL=.agent-layer/skills-imported/request-html-comments/scripts/html_review.mjs

[[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }
[[ -f $REVIEW_TOOL ]] || { echo "Missing $REVIEW_TOOL: run al skills pull" >&2; exit 1; }
# The final review's total reads the Instacart session from SSM (scripts/worker-docker.sh).
aws sts get-caller-identity --profile conn >/dev/null 2>&1 \
  || { echo "Sign in to AWS first: aws sso login --profile conn --use-device-code" >&2; exit 1; }

# The address other devices reach this machine at (the default route's source).
lan_ip=$(ip -4 route get 1.1.1.1 | awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }')
[[ -n $lan_ip ]] || { echo "Could not find this machine's local network address" >&2; exit 1; }

# A self-signed certificate for that address, reused until it nears expiry.
tls_dir="$HOME/.local/state/castle-chef/review-tls"
cert="$tls_dir/$lan_ip.crt" key="$tls_dir/$lan_ip.key"
if ! openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null 2>&1; then
  mkdir -p "$tls_dir"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 \
    -subj "/CN=Castle Chef review" -addext "subjectAltName=IP:$lan_ip" \
    -keyout "$key" -out "$cert" 2>/dev/null
  chmod 600 "$key"
fi

output=".agent-layer/tmp/reviews/review-$(date +%Y%m%d-%H%M%S).json"
mkdir -p "$(dirname "$output")"

# Jobs run in the worker image, as on Fargate (scripts/worker-docker.sh): build it
# from the working tree first.
echo "Building the worker image…"
scripts/worker-docker.sh build >/dev/null

# Each server gets its own process group, so stopping it stops its children too.
set -m
(cd backend && AWS_PROFILE=conn AWS_REGION=us-east-1 exec uv run uvicorn castle_chef.api:app --reload) </dev/null &
api=$!
(cd apps/mobile && CASTLE_CHEF_REVIEW_PORT=$REVIEW_PORT exec pnpm exec vite --port "$APP_PORT" --strictPort) </dev/null &
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

echo "Open https://$lan_ip:$REVIEW_PORT/ on a device on this network and accept the certificate warning."
echo "Comments will be written to $output"
node "$REVIEW_TOOL" "http://127.0.0.1:$APP_PORT/" --no-open --host "$lan_ip" --port "$REVIEW_PORT" \
  --tls-cert "$cert" --tls-key "$key" --output "$output" </dev/null &
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
