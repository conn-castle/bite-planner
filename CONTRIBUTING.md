# Contributing to Bite Planner

Use [GitHub Issues](https://github.com/conn-castle/bite-planner/issues) for bugs and independently deliverable feature ideas. Check existing open and closed issues first. For bugs, describe the observed and expected behavior, reproduction steps, and relevant device/release information. For features, describe the requested behavior and unresolved choices. Do not include credentials, private logs, or unrelated personal data.

## Prerequisites

- Python 3.14 and uv. Python dependencies are locked in the two `uv.lock` files.
- Node.js 22.13 or newer and the pnpm version in `apps/mobile/package.json`.
- Docker with Compose for the local Postgres test database and worker image.
- macOS with Xcode for native iOS work; Ruby/Bundler for the fastlane lanes.

Application code lives in `apps/mobile/`, `backend/`, and `infra/`. The Python workspace includes the backend and the isolated agent-tools package in `backend/agent/`.

## Checks

```sh
make check
```

This runs dependency installation, Python lint/format/type checks, frontend lint, backend/mobile/infrastructure tests, and a production web build using synthetic API/auth settings. No live AWS, Neon, Apple, OpenAI, or Instacart access is needed for the checks.

On Linux, install Playwright's system dependencies if they are missing:

```sh
uv run --project backend playwright install --with-deps --only-shell chromium
```

Useful narrower commands:

```sh
make lint
make test
make format
```

`make test` starts Postgres 17 on local port 54329. Tests use `TEST_DATABASE_URL`, create isolated worker databases, and refuse a second simultaneous backend run on the same server. They never use the live `DATABASE_URL`. `make db-stop` stops the local database without deleting data.

## Live development

First provision your account and populate the canonical SSM parameters in the [deployment guide](docs/operations/aws.md#configuration). A complete live setup uses AWS, Neon/Auth and the agent's external services; it is not an offline substitute for `make check`.

Choose your AWS profile and region explicitly:

```sh
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1
aws sso login --profile "$AWS_PROFILE" --use-device-code
make setup
make dev
```

`make setup` installs dependencies and runs `make env`'s SSM-backed configuration generator. It writes private mode-600 environment files for the development backend, development client, and deployment metadata. Generated files are ignored by Git; edit SSM and regenerate rather than hand-maintaining copies.

The API runs on port 8000 and the Vite app on port 5173. The browser uses HTTPS with a local development certificate. Live jobs run in the local worker Docker image; they still need SSM credentials and the configured external connections. `make dev` builds that image before starting its servers.

For production client configuration after the API exists:

```sh
uv run --project backend python -m bite_planner.configure production
make build
```

On macOS, `make ios` builds the production-configured client, synchronizes Capacitor, and opens Xcode. It connects to the configured production API, not the development database. Opening Xcode without synchronizing runs the last copied web build.

## Database changes

```sh
make migration m="describe the schema change"
make migrate
```

The first command generates an Alembic revision against local Postgres. Inspect its upgrade and downgrade before committing. The second applies migrations to your configured development Neon database. Keep runtime models and migrations consistent; the checks exercise migration reversibility and model agreement.

`make dev-reset` replaces the configured Neon development branch with a copy of production after asking for confirmation. It discards development data and auth state; use it only when that reset is intended.

## Interface changes

Use shared components in `apps/mobile/src/ui/`, with the tokens in `src/theme/tokens.css`. The [interface specification](spec/README.md) records accepted behavior that overrides the original mockup.

The optional `make review` command starts the live development app behind a private Tailscale annotation session. It requires Tailscale and an installed [request-html-comments tool](https://github.com/nicholasjconn/skills); set `BITE_PLANNER_REVIEW_TOOL` to its `scripts/html_review.mjs` path. Personal Agent Layer configuration is not part of this public repository. The command writes review results under `.agent-layer/tmp/reviews/`.

Keep functional changes and documentation aligned. Add tests for meaningful observable behavior, and avoid tests that merely repeat the implementation.
