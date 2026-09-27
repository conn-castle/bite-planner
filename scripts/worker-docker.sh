#!/usr/bin/env bash
# Runs the worker image locally the way Fargate runs it (infra/castle_chef_infra/runtime.py):
# linux/amd64, read-only root filesystem, an empty /tmp volume, the same dropped
# capabilities, 1 vCPU and 2 GB, and the Grok login on its own volume (EFS in AWS),
# against Neon dev (backend/.env). Run through make:
#   make worker-image              build the image from the working tree
#   make worker-login              sign the local worker's agent in (device code, once)
#   make worker-job JOB=<job-id>   run one pending job, then copy out its files
# A job's directory, Grok's session files, and the worker's log (worker.log) are
# copied to ~/.local/state/castle-chef/docker-jobs/<job-id>/ after it runs.
set -euo pipefail
cd "$(dirname "$0")/.."

readonly IMAGE=castle-chef-worker:local
readonly LOGIN_VOLUME=castle-chef-grok-login
readonly LOGIN_DIR=/mnt/agent-auth/grok  # GROK_AUTH_DIR in infra
readonly AGENT_UID=65532

fargate_args=(
  --platform linux/amd64 --read-only --init --cpus 1 --memory 2g
  --mount type=volume,dst=/tmp
  --volume "$LOGIN_VOLUME:$LOGIN_DIR"
  --env "GROK_AUTH_PATH=$LOGIN_DIR/auth.json"
  --env "CASTLE_CHEF_AGENT_LOCK_FILE=$LOGIN_DIR/agent.lock"
  --env CASTLE_CHEF_RELEASE=local
)
for capability in AUDIT_WRITE FSETID MKNOD NET_BIND_SERVICE NET_RAW SETFCAP SETPCAP SYS_CHROOT; do
  fargate_args+=(--cap-drop "$capability")
done

need_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || { echo "No $IMAGE, or Docker isn't running: run make worker-image" >&2; exit 1; }
}

# The worker's settings need the database URL (the task gets it from SSM). It reaches
# the container through this environment, never an argument.
database_url() {
  [[ -f backend/.env ]] || { echo "Missing backend/.env: run make env" >&2; exit 1; }
  DATABASE_URL=$(sed -n 's/^DATABASE_URL=//p' backend/.env)
  export DATABASE_URL
}

# EFS's access point makes the login directory the agent user's, mode 700.
prepare_login_volume() {
  docker run --rm --platform linux/amd64 --volume "$LOGIN_VOLUME:$LOGIN_DIR" --entrypoint sh "$IMAGE" \
    -c "chown $AGENT_UID:$AGENT_UID $LOGIN_DIR && chmod 700 $LOGIN_DIR"
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
    docker run --rm "${fargate_args[@]}" --env DATABASE_URL "$IMAGE" agent-login grok
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
    container=castle-chef-job-$job_id
    status=0
    docker run --name "$container" "${fargate_args[@]}" \
      --env DATABASE_URL --env "CASTLE_CHEF_JOB_ID=$job_id" "$IMAGE" || status=$?
    out=${XDG_STATE_HOME:-$HOME/.local/state}/castle-chef/docker-jobs/$job_id
    mkdir -p "$out"
    docker cp "$container:/tmp/jobs/$job_id" "$out/job" >/dev/null 2>&1 || echo "The job left no directory." >&2
    docker cp "$container:/tmp/agent-project/.grok-config/sessions" "$out/grok-sessions" >/dev/null 2>&1 || true
    docker logs "$container" >"$out/worker.log" 2>&1 || true
    docker rm --volumes "$container" >/dev/null
    echo "Worker exited with status $status; its files are in $out"
    exit "$status"
    ;;
  *)
    echo "usage: $0 build | login | check | run JOB_ID" >&2
    exit 2
    ;;
esac
