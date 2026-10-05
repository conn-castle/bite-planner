# syntax=docker/dockerfile:1.7
# Castle Chef images (linux/amd64; build from the repository root):
#   api     the FastAPI app on AWS Lambda, through the Lambda Web Adapter
#   worker  one job per Fargate task: `castle-chef job run` and the agent
# Every download is pinned by version and digest or checksum.
# Releases push the worker with zstd layers, which Fargate starts faster than
# gzip (Lambda gets the api image as built).

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.12.13@sha256:27508d63700ae72281272e11ce1cec19ffacef96dd59dee05e29593e1f874f01 AS uv
FROM --platform=linux/amd64 public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0@sha256:9d6b29a45b14a4a70f88d53c68b6d5bab1b22d75baca733958fee59308c509b8 AS lambda-adapter

FROM --platform=linux/amd64 python:3.14-slim-trixie@sha256:7bf6c3111fe094f8ee1a1cbcdc63c4cfb345b0e3df42d5aa9a90b3b4b022ab6d AS python
ENV PYTHONUNBUFFERED=1 \
    PATH=/app/backend/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /app/backend

# Each image's virtual environment is built here; uv itself stays out of the images.
FROM python AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./

# The API adds the `api` dependency group; the worker, which starts once per
# job, installs only what it needs. Dependencies first, so code changes don't
# rebuild them; editable (uv's default), so castle_chef.settings.REPO_ROOT is /app.
FROM build AS api-build
COPY backend/agent ./agent
RUN uv sync --frozen --no-default-groups --group api --no-install-project
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
RUN uv sync --frozen --no-default-groups --group api

FROM build AS worker-build
COPY backend/agent ./agent
RUN uv sync --frozen --no-default-groups --group worker --no-install-project
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
RUN uv sync --frozen --no-default-groups --group worker

# The agent's own environment: only its tools (castle_chef_agent) and their
# dependencies, installed as a package (no source tree), with a `castle-chef` launcher.
FROM build AS agent-build
COPY backend/agent ./agent
RUN UV_PROJECT_ENVIRONMENT=/opt/castle-chef-agent/.venv \
    uv sync --frozen --no-default-groups --package castle-chef-agent --no-editable
COPY docker/castle-chef /opt/castle-chef-agent/bin/castle-chef

# Codex (OpenAI's release package, pinned): its app-server calls Instacart's tools
# in the ChatGPT apps (castle_chef_agent.instacart). The whole package: tool calls
# need its code-mode host, not just the codex binary.
FROM python AS codex
ADD --checksum=sha256:0e211868c9fd73cb49ad35ac675b5eafdf6b9f453df8a493df980c59a590fe5f \
    https://github.com/openai/codex/releases/download/rust-v0.157.1/codex-package-x86_64-unknown-linux-musl.tar.gz \
    /tmp/codex.tar.gz
RUN mkdir /opt/codex && tar -xzf /tmp/codex.tar.gz -C /opt/codex \
 && chmod -R a+rX /opt/codex && /opt/codex/bin/codex --version

# Chrome Headless Shell, for the agent's Instacart checkout tools (castle_chef_agent.checkout):
# the build Playwright expects (uv.lock's playwright 1.63.0: chromium-headless-shell
# revision 1243), where Playwright looks for it. Unzipped keeping each file's mode.
FROM python AS chromium
ADD --checksum=sha256:a9da028861a0cf789ff25c2fed45f5f1aaf969ed9247835b6a7821a4f7af9d1d \
    https://cdn.playwright.dev/builds/cft/153.0.8010.12/linux64/chrome-headless-shell-linux64.zip \
    /tmp/chromium.zip
RUN python - <<'PY'
import os, zipfile
target = "/opt/ms-playwright/chromium_headless_shell-1243"
with zipfile.ZipFile("/tmp/chromium.zip") as archive:
    for entry in archive.infolist():
        path = archive.extract(entry, target)
        if not entry.is_dir():
            os.chmod(path, (entry.external_attr >> 16 & 0o777) | 0o444)
for marker in ("INSTALLATION_COMPLETE", "DEPENDENCIES_VALIDATED"):
    open(os.path.join(target, marker), "w").close()
PY
RUN chmod -R a+rX /opt/ms-playwright

FROM python AS api
COPY --from=lambda-adapter /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=api-build /app/backend /app/backend
ENV AWS_LWA_PORT=8080 AWS_LWA_READINESS_CHECK_PATH=/health
# The commit is the image's; the release version is set by the deploy (infra/).
ARG CASTLE_CHEF_COMMIT=unknown
ENV CASTLE_CHEF_COMMIT=${CASTLE_CHEF_COMMIT}
CMD ["uvicorn", "castle_chef.api:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python AS worker
# The headless shell's libraries (Playwright's list for Debian 13), from the Debian
# snapshot the base image was built from, so the versions are fixed.
RUN sed -i \
      -e 's|^URIs: http://deb.debian.org/debian$|URIs: http://snapshot.debian.org/archive/debian/20260918T000000Z|' \
      -e 's|^URIs: http://deb.debian.org/debian-security$|URIs: http://snapshot.debian.org/archive/debian-security/20260918T000000Z|' \
      /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends \
      fonts-liberation libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
      libcairo2 libcups2t64 libdbus-1-3 libdrm2 libfontconfig1 libgbm1 libglib2.0-0t64 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
      libxfixes3 libxkbcommon0 libxrandr2 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=chromium /opt/ms-playwright /opt/ms-playwright
# The agent runs as this unprivileged user; the worker (root) starts it.
RUN groupadd --gid 65532 agent \
 && useradd --uid 65532 --gid 65532 --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin agent
ADD --checksum=sha256:ea16cc701e4ce62eb079e1d0520a56eb05f1d406fa6a72adda1998f79ff656e0 --chmod=0555 \
    https://github.com/conn-castle/agent-layer/releases/download/v0.22.0/al-linux-amd64 \
    /usr/local/bin/al
ADD --checksum=sha256:9ce03ed23e16ea01072b4496263d6213a27899e1e3e107f008d36edf82e70407 --chmod=0555 \
    https://x.ai/cli/grok-1.0.41-linux-x86_64 \
    /usr/local/bin/grok
# The Agent Layer project the agent is launched from: only the worker's Grok
# settings (docker/agent-project). `al` syncs its generated files (and Grok's
# home, .grok-config) into the project on every launch, and the root filesystem
# is read-only, so the worker copies this template to a writable
# /tmp/agent-project owned by the agent for each job.
COPY docker/agent-project /opt/castle-chef/agent-project
COPY --from=agent-build /opt/castle-chef-agent /opt/castle-chef-agent
COPY --from=codex /opt/codex /opt/codex
COPY --from=worker-build /app/backend /app/backend
# The backend is the worker's (root) alone: the agent's user can't list or read it.
RUN chmod 700 /app
# The Grok login lives on EFS (GROK_AUTH_PATH, set by the task definition), never
# an API key; Grok doesn't update itself.
ENV GROK_DISABLE_API_KEY_AUTH=true \
    GROK_DISABLE_AUTOUPDATER=true \
    CASTLE_CHEF_AGENT_COMMAND="al -q grok" \
    CASTLE_CHEF_AGENT_UID=65532 \
    CASTLE_CHEF_AGENT_PATH=/opt/castle-chef-agent/bin:/usr/local/bin:/usr/bin:/bin \
    CASTLE_CHEF_AGENT_ENV='["GROK_AUTH_PATH","GROK_DISABLE_API_KEY_AUTH","GROK_DISABLE_AUTOUPDATER","PLAYWRIGHT_BROWSERS_PATH"]' \
    CASTLE_CHEF_AGENT_PROJECT_TEMPLATE=/opt/castle-chef/agent-project \
    CASTLE_CHEF_AGENT_PROJECT_DIR=/tmp/agent-project \
    CASTLE_CHEF_CODEX_COMMAND=/opt/codex/bin/codex \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    CASTLE_CHEF_JOBS_DIR=/tmp/jobs \
    CASTLE_CHEF_LOGS_DIR=/tmp/logs
# The commit is the image's; the release version is set by the deploy (infra/).
ARG CASTLE_CHEF_COMMIT=unknown
ENV CASTLE_CHEF_COMMIT=${CASTLE_CHEF_COMMIT}
# A hard cap above two agent runs (15 min each: a list, then its shopping) plus
# saving: a hung process can't keep the task (and its bill) running.
# castle_chef.jobs.WORKER_LIMIT is this limit: a booking isn't counted lost before it.
ENTRYPOINT ["timeout", "--kill-after=30s", "2400s", "castle-chef"]
# One job (the task gets CASTLE_CHEF_JOB_ID); `agent-login grok` signs the agent in,
# and `agent-login codex` connects Instacart (CODEX_HOME on EFS, set by the task).
CMD ["job", "run"]
