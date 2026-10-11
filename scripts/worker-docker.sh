#!/usr/bin/env bash
# Runs the worker image locally the way Fargate runs it (infra/castle_chef_infra/runtime.py):
# linux/amd64, read-only root filesystem, an empty /tmp volume, the same dropped
# capabilities, 1 vCPU and 2 GB, and the Codex login on a volume
# (EFS in AWS), against Neon dev (backend/.env). Run through make:
#   make worker-image              build the image from the working tree
#   make worker-login              connect Instacart (ChatGPT device code and link, once)
#   make worker-job JOB=<job-id>   run one pending job, then copy out its files
#   scripts/worker-docker.sh instacart search|cart …   check the Instacart store
# A job's directory and the worker's log (worker.log) are
# copied to ~/.local/state/castle-chef/docker-jobs/<job-id>/ after it runs.
set -euo pipefail
cd "$(dirname "$0")/.."

readonly IMAGE=castle-chef-worker:local
readonly CODEX_VOLUME=castle-chef-codex-login
readonly CODEX_DIR=/mnt/agent-auth/codex  # CODEX_HOME_DIR in infra
readonly AGENT_UID=65532

fargate_args=(
  --platform linux/amd64 --read-only --init --cpus 1 --memory 2g
  --mount type=volume,dst=/tmp
  --volume "$CODEX_VOLUME:$CODEX_DIR"
  --env "CASTLE_CHEF_CODEX_HOME=$CODEX_DIR"
  --env "CASTLE_CHEF_CODEX_LOCK_FILE=$CODEX_DIR/codex.lock"
  --env CASTLE_CHEF_RELEASE=local
)
for capability in AUDIT_WRITE FSETID MKNOD NET_BIND_SERVICE NET_RAW SETFCAP SETPCAP SYS_CHROOT; do
  fargate_args+=(--cap-drop "$capability")
done

need_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || { echo "No $IMAGE, or Docker isn't running: run make worker-image" >&2; exit 1; }
}

# The same project-scoped key Fargate receives from SSM, passed to Docker through
# the environment (its value never appears in a command argument or log).
openai_key() {
  OPENAI_API_KEY=$(aws ssm get-parameter \
    --name /castle-chef/shared/openai-api-key --with-decryption \
    --query Parameter.Value --output text --profile conn --region us-east-1)
  export OPENAI_API_KEY
}

# The worker's settings need the database URL (the task gets it from SSM). It reaches
# the container through this environment, never an argument.
database_url() {
  [[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }
  DATABASE_URL=$(sed -n 's/^DATABASE_URL=//p' backend/.env)
  export DATABASE_URL
}

# Checkout reads the household's Instacart session from SSM (the task role does in
# AWS): short-lived credentials from the `conn` profile, passed by environment only.
aws_credentials() {
  local exported
  aws_args=(--env AWS_DEFAULT_REGION=us-east-1
    --env CASTLE_CHEF_INSTACART_SESSION_PARAMETER=/castle-chef/shared/instacart-session)
  if exported=$(aws configure export-credentials --profile conn --format env 2>/dev/null); then
    eval "$exported"
    aws_args+=(--env AWS_ACCESS_KEY_ID --env AWS_SECRET_ACCESS_KEY --env AWS_SESSION_TOKEN)
  else
    # The job then fails reading the session, saying so.
    echo "No AWS sign-in (aws sso login --profile conn --use-device-code): a job that opens Instacart's checkout will fail." >&2
  fi
}

# EFS's access points make each login directory the agent user's, mode 700.
prepare_login_volume() {
  docker run --rm --platform linux/amd64 --volume "$CODEX_VOLUME:$CODEX_DIR" \
    --entrypoint sh "$IMAGE" \
    -c "chown $AGENT_UID:$AGENT_UID $CODEX_DIR && chmod 700 $CODEX_DIR"
}

case "${1:-}" in
  build)
    docker build --platform linux/amd64 --file docker/Dockerfile --target worker \
      --build-arg "CASTLE_CHEF_COMMIT=$(git rev-parse HEAD)" --tag "$IMAGE" .
    ;;
  login)
    need_image
    database_url
    prepare_login_volume
    docker run --rm "${fargate_args[@]}" --env DATABASE_URL "$IMAGE" agent-login codex
    ;;
  instacart)
    need_image
    database_url
    prepare_login_volume
    docker run --rm "${fargate_args[@]}" --env DATABASE_URL "$IMAGE" instacart "${@:2}"
    ;;
  check)
    # What `run` needs, checked before a job is handed to it (the API's local dispatch).
    need_image
    database_url
    ;;
  run)
    job_id=${2:?usage: $0 run JOB_ID}
    need_image
    database_url
    prepare_login_volume
    aws_credentials
    openai_key
    container=castle-chef-job-$job_id
    status=0
    docker run --name "$container" "${fargate_args[@]}" "${aws_args[@]}" --env OPENAI_API_KEY \
      --env DATABASE_URL --env "CASTLE_CHEF_JOB_ID=$job_id" "$IMAGE" || status=$?
    out=${XDG_STATE_HOME:-$HOME/.local/state}/castle-chef/docker-jobs/$job_id
    mkdir -p "$out"
    docker cp "$container:/tmp/jobs/$job_id" "$out/job" >/dev/null 2>&1 || echo "The job left no directory." >&2
    docker logs "$container" >"$out/worker.log" 2>&1 || true
    docker rm --volumes "$container" >/dev/null
    echo "Worker exited with status $status; its files are in $out"
    exit "$status"
    ;;
  *)
    echo "usage: $0 build | login codex | instacart search|cart … | check | run JOB_ID" >&2
    exit 2
    ;;
esac
