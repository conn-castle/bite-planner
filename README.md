# Castle Chef

An agent-driven meal service for the family. There is no backend server: the iOS app and the agents meet in a shared Neon Postgres database, which also serves as the job queue.

```text
iOS app (Capacitor)  ── Neon Auth (sign-in) ──┐
        │                                      │ JWT
        └── Neon Data API (PostgREST + RLS) ◄──┘
                     │
               Neon Postgres  ◄── job queue + meals
                     │  (direct connection, owner role)
        worker on this machine (`castle-chef worker run`)
                     │  per job
        `al grok` (bare, sandboxed) ── calls `castle-chef meal add`
```

1. The app inserts a `job` row (`kind = generate_meals`, `params = {"count": N}`).
2. The worker polls the queue every 30 minutes, claims the job (`FOR UPDATE SKIP LOCKED`), and runs Grok with a prompt asking for N low-effort meals.
3. Grok calls `castle-chef meal add` once per meal. The CLI validates the meal and saves it as JSON in the job directory; the agent never touches the database.
4. After Grok exits, the worker checks that exactly N meals were saved, then stores them (linked through `job_meal`) and marks the job `succeeded` in one transaction. Otherwise the job is `failed` with an error and nothing is stored.
5. The app shows the job's meals and lets you rescale servings.

## Layout

| Path | What |
| --- | --- |
| `backend/` | Python (uv): SQLModel models, Alembic migrations, `castle-chef` CLI, job worker, tests |
| `backend/src/castle_chef/prompts/` | Agent prompts |
| `apps/mobile/` | Capacitor + React + Vite app (`ios/` is the Xcode project) |
| `deploy/castle-chef-worker.service` | systemd user unit for the worker |
| `.github/workflows/` | CI (lint, tests, build) and manual TestFlight upload |

## Backend

```sh
cd backend
uv sync
```

`backend/.env` (gitignored) holds `DATABASE_URL`, the direct owner-role connection string for the Neon `main` branch. Other settings (`CASTLE_CHEF_POLL_INTERVAL_SECONDS`, `CASTLE_CHEF_AGENT_TIMEOUT_SECONDS`, `CASTLE_CHEF_AGENT_COMMAND`, `CASTLE_CHEF_JOBS_DIR`) are in `src/castle_chef/settings.py`.

### Tests

Tests run against a local Postgres, never Neon:

```sh
docker compose up -d --wait
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run pytest
uv run ruff check . && uv run ruff format --check .
```

`tests/neon_stubs.sql` creates stand-ins for the roles and `auth.uid()` that Neon provides, so the RLS policies are tested too.

### Migrations

Edit `src/castle_chef/models.py`, then:

```sh
DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run alembic upgrade head
DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run alembic revision --autogenerate -m "..."
```

Any new table the app reads must also get grants, RLS, and policies (see the `expose tables` migration). Apply to Neon and refresh the Data API's schema cache:

```sh
uv run alembic upgrade head
neonctl data-api refresh-schema --project-id example-project --branch main --database neondb
```

### Worker

```sh
uv run castle-chef worker run            # poll forever (every 30 min)
uv run castle-chef worker run --once     # manual trigger: process pending jobs now, then exit
uv run castle-chef job create --count 3  # queue a job without the app
```

Each job gets a directory under `~/.local/state/castle-chef/jobs/<job-id>/` with the prompt, Grok's JSON output, and its stderr.

The worker runs `al -q grok` from the repo root, so Grok uses this repo's login and model settings (`.agent-layer/config.toml`). Grok's headless mode auto-approves every tool call, so the run is contained by construction:

- The working directory is the job directory (no project instructions); the only tool is the shell; subagents are disabled.
- A per-job sandbox profile (`castle-chef-agent`, extending Grok's `strict`) lets commands read only the job directory, system paths, `backend/.venv`, `backend/src`, and Grok's own home (`.grok-config`, which Grok must read to run); write only the job directory and `/tmp`; and blocks network access for commands. If the profile is missing, Grok refuses to start.
- Known gap: because Grok's home is readable, a misbehaving agent could read Grok's login (`.grok-config/auth.json`) and put it into a meal. Running the worker as a separate Linux user with its own Grok login would close this.
- The agent gets a minimal environment (`HOME`, `PATH`, `CASTLE_CHEF_JOB_DIR`): no database credentials.
- The prompt contains only the validated meal count, so there is no prompt-injection channel. Keep it that way: a free-text job parameter would become input to an auto-approving shell agent.

### Logs

Everything logs structured JSON, one object per line: `{"ts", "level", "logger", "event", ...fields}`.

- Worker and CLI: stderr (journald for the service) and `~/.local/state/castle-chef/logs/castle-chef.jsonl` (rotated at 10 MB, 5 files). Events include `job.claimed`, `agent.finished` (seconds, turns, cost), `job.succeeded`, `job.failed`, and `queue.checked`. `meal add` runs inside the agent's sandbox, so it logs to stderr only.
- App: each entry is `{"ts", "level", "event", "launch", ...fields}`, kept in a 1,000-entry buffer on the device. Account → **Send logs** uploads it to `client_log_batch`; the worker saves each batch to `~/.local/state/castle-chef/logs/client/<time>-<id>.jsonl` (first line: sender, device, app version) on its next cycle and deletes the row. `uv run castle-chef logs pull` saves them immediately.

```sh
tail -f ~/.local/state/castle-chef/logs/castle-chef.jsonl | jq -c '{ts, event, job_id}'
```

### Run the worker as a service

`deploy/castle-chef-worker.service` assumes the repo is at `~/git-repos/nicholasjconn/castle-chef`; edit `WorkingDirectory` if it lives elsewhere.

```sh
mkdir -p ~/.config/systemd/user
cp deploy/castle-chef-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now castle-chef-worker
loginctl enable-linger "$USER"   # keep running without a login session
journalctl --user -u castle-chef-worker -f
```

## App

```sh
cd apps/mobile
pnpm install
pnpm dev          # https://localhost:5173 and https://<this machine's LAN IP>:5173
pnpm test && pnpm lint && pnpm build
```

`apps/mobile/.env` holds the public Neon Auth and Data API URLs for the `main` branch.

The dev server listens on the local network over HTTPS with a self-signed certificate (browsers warn once; accept it). It also proxies Neon Auth at `/neon-auth` so the session cookie is first-party in any browser, including Safari on a phone, and sends the trusted origin Neon Auth requires (`vite.config.ts`). If other devices can't connect, allow the port through the firewall: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

iOS (on a Mac): `pnpm build && pnpm exec cap sync ios && pnpm exec cap open ios`.

Data handling (`src/queryClient.ts`, `src/queries.ts`):

- Server data lives in TanStack Query: shown from cache immediately, refreshed in the background (stale-while-revalidate).
- The cache is persisted on the device (Capacitor Preferences) for 7 days, so a relaunch shows the last data instantly. Sign-out deletes it.
- Refreshes when the app returns to the foreground (`@capacitor/app`) and pauses while offline (`@capacitor/network`), with an offline banner. Requests made offline are sent when the connection returns.
- The request list refreshes every 10 s while any request is pending or running.
- Requesting meals is optimistic: the request appears at once and reconciles with the server.
- Meals of recent finished requests are prefetched, so opening one is instant.
- Hash routing (`/`, `/requests/:id`, `/account`) gives real back navigation in the WebView. Render errors show a recovery screen and are logged.

On iOS, Neon Auth requests go through Capacitor's native HTTP with the trusted origin `https://app.example.test`, because the WebView's `capacitor://localhost` origin can't be trusted and its cookies would be third-party. See `src/neon.ts`.

### TestFlight

`.github/workflows/testflight.yml` (run manually) builds on a macOS runner and uploads with fastlane (`apps/mobile/fastlane/`). One-time setup:

1. In App Store Connect, create the app with bundle id `com.conncastle.castlechef`.
2. On a Mac, create the App Store profile in the match repo: `cd apps/mobile && bundle install && bundle exec fastlane match appstore`.
3. Add repository secrets: `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_BASE64` (base64 of the `.p8`), `MATCH_PASSWORD`, `MATCH_GIT_BASIC_AUTHORIZATION` (base64 of `user:token` with read access to `conn-castle-apple-certs`).

## Neon

Project `castle-chef` (`example-project`), branch `main`, database `neondb`. Neon Auth and the Data API are enabled on `main`; only the `public` schema is exposed.

Security model:

- The app signs in with Neon Auth using an emailed one-time code (no passwords, no social providers) and gets a short-lived JWT; the Data API runs its queries as the `authenticated` role.
- Invite-only: only accounts listed in `household_member` can read anything or queue jobs. An account that exists in Neon Auth but was never invited sees nothing. Sign-up is disabled in Neon Auth.
- RLS: household members can read all meals and jobs, and can only insert `generate_meals` jobs as themselves, with a valid count (1 to 14), and at most 3 pending or running at a time. Everything else is denied. Anonymous requests are rejected. Policies use two `SECURITY DEFINER` helpers in the `app_private` schema, which the Data API does not expose.
- The worker uses the owner role over a direct connection (bypasses RLS). The agent has no database access.
- Trusted auth origin: `https://app.example.test`, presented by the iOS app and the dev server's auth proxy. Keep this domain under your control. Localhost is not trusted.

### Family members (invite-only)

```sh
cd backend
uv run castle-chef family invite mom@example.com --name "Mom"   # create the account and add it
uv run castle-chef family list                                  # invited emails and names
uv run castle-chef family remove mom@example.com                # remove and delete the account
```

`invite` needs `CASTLE_CHEF_NEON_PROJECT_ID` and `CASTLE_CHEF_NEON_BRANCH_ID` in `backend/.env` and a logged-in `neonctl`. Sign-in is passwordless: the person enters their email in the app, then the 6-digit code Neon Auth emails them (from `auth@mail.myneon.app`; check spam).

Known Neon issue: the Data API caches Neon Auth's signing keys for up to an hour and does not refresh on an unknown key ([neondatabase/neon#12935](https://github.com/neondatabase/neon/issues/12935)). After a key change (or on a brand-new branch before its first sign-up), API calls can fail with `jwk not found` for up to an hour.
