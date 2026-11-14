<img src="apps/mobile/public/brand-mark.png" alt="Bite Planner logo" width="96">

# Bite Planner

Plan meals, choose recipes, and turn them into a grocery order. Bite Planner combines an iOS app with an agent-powered backend for household meal planning and Instacart shopping.

**Status:** pre-release, self-hosted software. This repository contains the application and deployment code; it does not provide a public hosted service or an open sign-up flow. Running a live instance requires your own service accounts and configuration.

## What it does

- Suggests meals and records household ratings to guide later suggestions.
- Lets you review recipes, adjust servings, and approve proposed recipe edits.
- Builds a shopping list and finds products through an operator-connected Instacart account.
- Shows a delivery quote for approval and books an approved order when its delivery window becomes available.
- Keeps household orders, members, invitations, cooking steps, and timers together.

The client uses Ionic React and Capacitor. A FastAPI backend on AWS Lambda stores data in Neon Postgres; queued jobs run in isolated Fargate containers using the OpenAI Responses API. Scheduled jobs handle booking and reminders. See [Architecture](docs/architecture.md) for the data flow and boundaries.

## Run the checks

You can build and test the project without access to the maintainer's deployment or credentials.

Install Python 3.14, [uv](https://docs.astral.sh/uv/), Node.js 22.13 or newer, pnpm at the version pinned in [`package.json`](apps/mobile/package.json), and Docker with Compose. Then:

```sh
git clone https://github.com/conn-castle/bite-planner.git
cd bite-planner
make check
```

This installs dependencies, starts a local test database, runs lint, type checks and tests, and builds the web client with synthetic configuration. It downloads Playwright's browser; a new Linux machine may also need its [system dependencies](CONTRIBUTING.md#checks). The test database is separate from a live deployment.

## Run a live instance

The application depends on AWS, Neon Postgres with Neon Auth, OpenAI, and an operator-configured Instacart connection. Native iOS distribution also requires Apple developer access and signing configuration.

Start with the [deployment guide](docs/operations/aws.md), then follow [local development](CONTRIBUTING.md#live-development). Deployment settings and credentials come from your AWS SSM Parameter Store. The tracked examples are synthetic; `make env` connects to an already configured account, not a shared public backend.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Contributing](CONTRIBUTING.md) | Development setup, checks, migrations, and reporting issues |
| [Architecture](docs/architecture.md) | Client, API, agents, data, and purchase authorization |
| [Deployment and operations](docs/operations/aws.md) | Provisioning, configuration, signing, releases, and recovery |
| [Product requirements](spec/requirements.md) | Implemented behavior and deferred scope |
| [Interface specification](spec/README.md) | Shared UI patterns and accepted mockup overrides |

Report bugs and feature requests in [GitHub Issues](https://github.com/conn-castle/bite-planner/issues). Keep credentials and personal data out of public issues and logs.

## License

Bite Planner is **source-available** under the [PolyForm Perimeter License 1.0.1](LICENSE), the same license used by [Agent Outbox](https://github.com/conn-castle/agent-outbox). See [Third-party notices](THIRD_PARTY_NOTICES.md) for included icon artwork. Dependencies retain their own licenses.
