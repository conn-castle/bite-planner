# syntax=docker/dockerfile:1.7
# Castle Chef images (linux/amd64; build from the repository root):
#   api     the FastAPI app on AWS Lambda, through the Lambda Web Adapter
#   worker  one job per Fargate task: `castle-chef job run` and the agent
# Every download is pinned by version and digest or checksum.

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.12.13@sha256:27508d63700ae72281272e11ce1cec19ffacef96dd59dee05e29593e1f874f01 AS uv
FROM --platform=linux/amd64 public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0@sha256:9d6b29a45b14a4a70f88d53c68b6d5bab1b22d75baca733958fee59308c509b8 AS lambda-adapter

FROM --platform=linux/amd64 python:3.14-slim-trixie@sha256:7bf6c3111fe094f8ee1a1cbcdc63c4cfb345b0e3df42d5aa9a90b3b4b022ab6d AS base
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/backend/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app/backend
# Dependencies first, so code changes don't rebuild them.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
# Editable (uv's default), so castle_chef.settings.REPO_ROOT is /app.
RUN uv sync --frozen --no-dev
ARG CASTLE_CHEF_RELEASE=dev
ARG CASTLE_CHEF_COMMIT=unknown
ENV CASTLE_CHEF_RELEASE=${CASTLE_CHEF_RELEASE} CASTLE_CHEF_COMMIT=${CASTLE_CHEF_COMMIT}

FROM base AS api
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_PORT=8080 AWS_LWA_READINESS_CHECK_PATH=/health
CMD ["uvicorn", "castle_chef.api:app", "--host", "0.0.0.0", "--port", "8080"]

FROM base AS worker
# The agent runs as this unprivileged user; the worker (root) starts it.
RUN groupadd --gid 65532 agent \
 && useradd --uid 65532 --gid 65532 --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin agent
ADD --checksum=sha256:ea16cc701e4ce62eb079e1d0520a56eb05f1d406fa6a72adda1998f79ff656e0 --chmod=0555 \
    https://github.com/conn-castle/agent-layer/releases/download/v0.22.0/al-linux-amd64 \
    /usr/local/bin/al
ADD --checksum=sha256:9ce03ed23e16ea01072b4496263d6213a27899e1e3e107f008d36edf82e70407 --chmod=0555 \
    https://x.ai/cli/grok-1.0.41-linux-x86_64 \
    /usr/local/bin/grok
# The Agent Layer project the agent is launched from. `al` syncs its generated
# files (and Grok's home, .grok-config) into the project on every launch, and
# /app is read-only, so the worker copies this template to a writable
# /tmp/agent-project owned by the agent for each job.
COPY .agent-layer /opt/castle-chef/agent-project/.agent-layer
# The Grok login lives on EFS (GROK_AUTH_PATH, set by the task definition), never
# an API key; Grok doesn't update itself.
ENV GROK_DISABLE_API_KEY_AUTH=true \
    GROK_DISABLE_AUTOUPDATER=true \
    CASTLE_CHEF_AGENT_SANDBOX=false \
    CASTLE_CHEF_AGENT_UID=65532 \
    CASTLE_CHEF_AGENT_ENV='["GROK_AUTH_PATH","GROK_DISABLE_API_KEY_AUTH","GROK_DISABLE_AUTOUPDATER"]' \
    CASTLE_CHEF_AGENT_PROJECT_TEMPLATE=/opt/castle-chef/agent-project \
    CASTLE_CHEF_AGENT_PROJECT_DIR=/tmp/agent-project \
    CASTLE_CHEF_JOBS_DIR=/tmp/jobs \
    CASTLE_CHEF_LOGS_DIR=/tmp/logs
# A hard cap above the agent timeout (15 min) plus saving: a hung process can't
# keep the task (and its bill) running.
ENTRYPOINT ["timeout", "--kill-after=30s", "1200s", "castle-chef"]
# One job (the task gets CASTLE_CHEF_JOB_ID); `agent-login grok` signs the agent in.
CMD ["job", "run"]
