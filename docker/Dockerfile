# syntax=docker/dockerfile:1.7
# Bite Planner images (linux/amd64; build from the repository root):
#   api     the FastAPI app on AWS Lambda, through the Lambda Web Adapter
#   worker  one job per Fargate task: `bite-planner job run` and the agent
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
# rebuild them; editable (uv's default), so bite_planner.settings.REPO_ROOT is /app.
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

# The agent's own environment: only its tools (bite_planner_agent) and their
# dependencies, installed as a package (no source tree), with a `bite-planner` launcher.
FROM build AS agent-build
COPY backend/agent ./agent
RUN UV_PROJECT_ENVIRONMENT=/opt/bite-planner-agent/.venv \
    uv sync --frozen --no-default-groups --package bite-planner-agent --no-editable
COPY docker/bite-planner /opt/bite-planner-agent/bin/bite-planner

# Codex (OpenAI's release package, pinned): its app-server calls Instacart's tools
# in the ChatGPT apps (bite_planner_agent.instacart). The whole package: tool calls
# need its code-mode host, not just the codex binary.
FROM python AS codex
ADD --checksum=sha256:0e211868c9fd73cb49ad35ac675b5eafdf6b9f453df8a493df980c59a590fe5f \
    https://github.com/openai/codex/releases/download/rust-v0.157.1/codex-package-x86_64-unknown-linux-musl.tar.gz \
    /tmp/codex.tar.gz
RUN mkdir /opt/codex && tar -xzf /tmp/codex.tar.gz -C /opt/codex \
 && chmod -R a+rX /opt/codex && /opt/codex/bin/codex --version

# Chrome Headless Shell, for the agent's Instacart checkout tools (bite_planner_agent.checkout):
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
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
ENV AWS_LWA_PORT=8080 AWS_LWA_READINESS_CHECK_PATH=/health
# The commit is the image's; the release version is set by the deploy (infra/).
ARG BITE_PLANNER_COMMIT=unknown
ENV BITE_PLANNER_COMMIT=${BITE_PLANNER_COMMIT}
CMD ["uvicorn", "bite_planner.api:app", "--host", "0.0.0.0", "--port", "8080"]

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
COPY --from=agent-build /opt/bite-planner-agent /opt/bite-planner-agent
COPY --from=codex /opt/codex /opt/codex
COPY --from=worker-build /app/backend /app/backend
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
# The backend is the worker's (root) alone: the agent's user can't list or read it.
RUN chmod 700 /app
# The OpenAI key comes from SSM through the worker task's ECS secret. Only the
# isolated runner gets it; its MCP tool subprocess does not.
ENV BITE_PLANNER_AGENT_COMMAND="bite-planner run-agent" \
    BITE_PLANNER_AGENT_UID=65532 \
    BITE_PLANNER_AGENT_PATH=/opt/bite-planner-agent/bin:/usr/local/bin:/usr/bin:/bin \
    BITE_PLANNER_AGENT_ENV='["OPENAI_API_KEY","PLAYWRIGHT_BROWSERS_PATH"]' \
    BITE_PLANNER_CODEX_COMMAND=/opt/codex/bin/codex \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    BITE_PLANNER_JOBS_DIR=/tmp/jobs \
    BITE_PLANNER_LOGS_DIR=/tmp/logs
# The commit is the image's; the release version is set by the deploy (infra/).
ARG BITE_PLANNER_COMMIT=unknown
ENV BITE_PLANNER_COMMIT=${BITE_PLANNER_COMMIT}
# A hard cap above two agent runs (15 min each: a list, then its shopping) plus
# saving: a hung process can't keep the task (and its bill) running.
# bite_planner.jobs.WORKER_LIMIT is this limit: a booking isn't counted lost before it.
ENTRYPOINT ["timeout", "--kill-after=30s", "2400s", "bite-planner"]
# One job (the task gets BITE_PLANNER_JOB_ID); `agent-login codex` connects
# Instacart (CODEX_HOME on EFS, set by the task).
CMD ["job", "run"]
