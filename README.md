# Castle Chef

An agent-driven meal service for the family. There is no backend server: the iOS app and the agents meet in a shared Neon Postgres database, which also serves as the job queue.

```text
iOS app (Capacitor)  ── Neon Auth (sign-in) ──┐
        │                                      │ JWT
        └── Neon Data API (PostgREST + RLS) ◄──┘
                     │
               Neon Postgres  ◄── job queue + meals
                     │  (direct connection, owner role)
        worker on this machine (`castle-chef worker run`, latest release)
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
| `deploy/` | Worker deployment: systemd units and `castle-chef-update`, which keeps the worker on the latest release |
| `.github/workflows/` | CI, releases (`release.yml`: migrations, GitHub release, TestFlight), database rollback (`rollback.yml`) |

Every push to `main` is a release ([Releases](#releases)). Development happens against the Neon `dev` branch ([Development](#development)).

## Local demo

Runs the whole loop from this machine against the Neon `dev` branch: the app on the local network, and a worker that checks for requests every 15 seconds instead of every 30 minutes. Use two terminals.

Prerequisites (once): `cd backend && uv sync`, `cd apps/mobile && pnpm install`, `backend/.env` for `dev` (see [Development](#development)), and for phones, port 5173 open: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

**Start**

```sh
# Terminal 1: app dev server (HTTPS, local network)
cd apps/mobile && pnpm dev

# Terminal 2: demo worker, checking every 15 s (the default is 1800 s)
cd backend && CASTLE_CHEF_POLL_INTERVAL_SECONDS=15 uv run castle-chef worker run
```

**Try it:** open `https://<this machine's LAN IP>:5173` (e.g. `https://192.168.1.252:5173`) on a phone or computer and accept the certificate warning (Safari: Show Details → visit this website). Sign in with your email and the emailed code, then request meals. The worker picks the request up within 15 s; Grok takes 30–60 s (a few cents per request).

**Stop:** press `Ctrl+C` in each terminal. If one was started in the background or you lost the terminal:

```sh
pkill -f 'castle-chef worker run'   # every running worker, including the systemd service's
                                    # (restart that with: systemctl --user restart castle-chef-worker)
lsof -ti :5173 | xargs -r kill      # app dev server (whatever is listening on port 5173)
lsof -i :5173                       # prints nothing once it has stopped
```

`Ctrl+C` stops the worker mid-job: the job stays `running` and the next worker run marks it failed after the agent timeout (15 min) plus 5 min. (`SIGTERM`, which systemd sends, lets the running job finish instead.) The production worker ([Worker deployment](#worker-deployment)) uses Neon `main`, so it never sees requests made here.

## Backend

```sh
cd backend
uv sync
```

`backend/.env` (gitignored) holds `DATABASE_URL`, the direct owner-role connection string for the Neon `dev` branch, and the Neon ids for `family` commands (see [Development](#development)). It is generated from SSM ([Secrets](#secrets)); credentials for `main` are never in your checkout. Other settings (`CASTLE_CHEF_POLL_INTERVAL_SECONDS`, `CASTLE_CHEF_AGENT_TIMEOUT_SECONDS`, `CASTLE_CHEF_AGENT_COMMAND`, `CASTLE_CHEF_JOBS_DIR`) are in `src/castle_chef/settings.py`.

### Tests

Tests run against a local Postgres, never Neon:

```sh
docker compose up -d --wait        # local Postgres on port 54329
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run pytest
uv run ruff check . && uv run ruff format --check .
docker compose stop                # stop it when done (data is kept)
```

`tests/neon_stubs.sql` creates stand-ins for the roles and `auth.uid()` that Neon provides, so the RLS policies are tested too.

### Migrations

Neon `main` is migrated only by the release workflow ([Releases](#releases)). Migrations must be **backward-compatible** (expand/contract): the previous release's worker, and app builds already on phones, keep running against the migrated database.

- Adding tables, nullable (or defaulted) columns, and policies is fine; so is replacing a policy, constraint, or index.
- To remove or rename a table or column, stop using it in one release and drop it in a later one. `tests/test_migration_guard.py` fails on a drop or rename in `upgrade()` unless the statement has `# contract: <why the previous release no longer uses it>` on the line above.
- Downgrades are still written (CI tests them) but never run on `main`.

Edit `src/castle_chef/models.py`, then generate the migration against local Postgres:

```sh
DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run alembic upgrade head
DATABASE_URL=postgresql://postgres:postgres@localhost:54329/castle_chef uv run alembic revision --autogenerate -m "..."
```

Any new table the app reads must also get grants, RLS, and policies (see the `expose tables` migration). Try it with the app on the `dev` branch (`backend/.env`), then refresh `dev`'s Data API schema cache:

```sh
uv run alembic upgrade head
neonctl data-api refresh-schema --project-id example-project --branch dev --database neondb
```

### Worker

```sh
uv run castle-chef worker run            # poll forever (every 30 min)
uv run castle-chef worker run --once     # manual trigger: process pending jobs now, then exit
uv run castle-chef job create --count 3  # queue a job without the app
```

Each job gets a directory under `~/.local/state/castle-chef/jobs/<job-id>/` with the prompt, Grok's JSON output, and its stderr.

On start, the worker logs `worker.started` with its release (`r<N>`, or `dev-<sha>` for an untagged checkout) and commit. It refuses to start (`schema.behind`, exit status 78) if the database's migrations are older than the code: on `dev`, run `uv run alembic upgrade head`; on `main`, the release's migration did not run. A database that is *newer* than the code (after a worker rollback) is fine. On `SIGTERM` (systemd stop, updates) it finishes the running job, claims nothing new, and exits. A reboot still interrupts a job, since this machine's user managers stop within 5 seconds; the job is marked failed 20 minutes later.

The worker runs `al -q grok` from the repo root, so Grok uses this repo's login and model settings (`.agent-layer/config.toml`). Grok's headless mode auto-approves every tool call, so the run is contained by construction:

- The working directory is the job directory (no project instructions); the only tool is the shell; subagents are disabled.
- A per-job sandbox profile (`castle-chef-agent`, extending Grok's `strict`) lets commands read only the job directory, system paths, `backend/.venv`, `backend/src`, and Grok's own home (`.grok-config`, which Grok must read to run); write only the job directory and `/tmp`; and blocks network access for commands. If the profile is missing, Grok refuses to start.
- Because Grok's home is readable, a misbehaving agent could read Grok's login (`.grok-config/auth.json`) and put it into a meal. The production worker therefore runs as its own Linux user with its own Grok login ([Worker deployment](#worker-deployment)); a worker run from your checkout uses yours.
- The agent gets a minimal environment (`HOME`, `PATH`, `CASTLE_CHEF_JOB_DIR`): no database credentials.
- The prompt contains only the validated meal count, so there is no prompt-injection channel. Keep it that way: a free-text job parameter would become input to an auto-approving shell agent.

### Logs

Everything logs structured JSON, one object per line: `{"ts", "level", "logger", "event", ...fields}`.

- Worker and CLI: stderr (journald for the service) and `~/.local/state/castle-chef/logs/castle-chef.jsonl` (rotated at 10 MB, 5 files). Events include `worker.started` (release, commit), `job.claimed`, `agent.finished` (seconds, turns, cost), `job.succeeded`, `job.failed`, `queue.checked`, `schema.behind`, and `worker.stopped`. `meal add` runs inside the agent's sandbox, so it logs to stderr only.
- Updater (production worker only): the same file, logger `castle_chef.update`: `update.installed`, `update.started`, `update.finished`, `update.worker_restarted`, and at error level `update.rolled_back`, `update.deploy_failed`, `update.fetch_failed`, `update.auth_failed`, `update.untrusted_release`, `update.invalid_release`, `update.rollback_failed`.
- App: each entry is `{"ts", "level", "event", "launch", ...fields}`, kept in a 1,000-entry buffer on the device. Account → **Send logs** uploads it to `client_log_batch`; the worker saves each batch to `~/.local/state/castle-chef/logs/client/<time>-<id>.jsonl` (first line: sender, device, app version) on its next cycle and deletes the row. `uv run castle-chef logs pull` saves them immediately.

```sh
tail -f ~/.local/state/castle-chef/logs/castle-chef.jsonl | jq -c '{ts, event, job_id}'
```

### Worker deployment

The production worker runs as the Linux user `castle-chef` from its own clone, `~castle-chef/app`, which `deploy/castle-chef-update` keeps on the latest release. Every 5 minutes (`castle-chef-update.timer`) the updater:

1. Reads the latest GitHub release. It only follows releases created by the release workflow (author `github-actions[bot]`), which migrates Neon before publishing, and checks out the release's commit, not its tag.
2. Fetches it (a network failure changes nothing), stops the worker (waiting for a running job to finish), checks out the commit, runs `uv sync` and `al sync`, and installs changed units from `deploy/`.
3. Starts the worker and waits for `worker.started` with the new commit. If it doesn't come within 2 minutes, it goes back to the previous commit, logs `update.rolled_back`, and never retries that release (`~/.local/state/castle-chef/bad-releases`). If preparing the commit failed instead (e.g. a package download), it goes back and retries on the next run.
4. Otherwise, if the worker is stopped (but hasn't failed on its own), starts it. To pause the worker, stop the timer too: `systemctl --user stop castle-chef-update.timer castle-chef-worker`.

Nothing notifies you when the updater or worker fails; check the log file or `journalctl --user -u castle-chef-worker -u castle-chef-update`.

One-time setup, after the first release exists:

```sh
# As your admin account:
sudo useradd --create-home --shell /bin/bash castle-chef
sudo loginctl enable-linger castle-chef          # its services run without a login session
sudo -iu castle-chef
```

Then, in the `castle-chef` shell, `export XDG_RUNTIME_DIR=/run/user/$(id -u)` so `systemctl --user` reaches its services.

As `castle-chef`:

1. Install `uv`, `al`, `grok`, and `neonctl` into `~/.local/bin` (as for your own account). The updater also needs `git`, `curl`, `jq`, and `openssl` (system packages).
2. Give it the Castle Chef Bot GitHub App's credentials from SSM ([Secrets](#secrets)). From **your** account, which has the AWS profile:
   ```sh
   ssm() { aws ssm get-parameter --profile conn --region us-east-1 --with-decryption --name "$1" --query Parameter.Value --output text; }
   sudo -H -u castle-chef bash -c 'umask 077; mkdir -p ~/.config/castle-chef'
   ssm /castle-chef/shared/github-app-private-key |
     sudo -H -u castle-chef bash -c 'umask 077; cat > ~/.config/castle-chef/github-app.pem'
   printf 'GITHUB_APP_CLIENT_ID=%s\n' "$(ssm /castle-chef/shared/github-app-client-id)" |
     sudo -H -u castle-chef bash -c 'umask 077; cat > ~/.config/castle-chef/github-app.env'
   ```
3. Clone `~/app` at the latest release, using the app (from your checkout of this repository):
   ```sh
   sudo -H -u castle-chef bash -c 'cd; set -a; . ~/.config/castle-chef/github-app.env; set +a; bash -s install' \
     < deploy/castle-chef-update
   ```
4. Write `~/app/backend/.env` for production, again from your account:
   ```sh
   { printf 'DATABASE_URL=%s\n' "$(aws ssm get-parameter --profile conn --region us-east-1 --with-decryption \
       --name /castle-chef/production/database-url --query Parameter.Value --output text)"
     echo CASTLE_CHEF_NEON_PROJECT_ID=example-project
     echo CASTLE_CHEF_NEON_BRANCH_ID=br-example-00000000; } |
     sudo -H -u castle-chef bash -c 'umask 077; cat > ~/app/backend/.env'
   ```
5. `cd ~/app/backend && uv sync --frozen --no-dev`, then `cd ~/app && al sync` and log in to Grok (`al grok`, then its login; it is stored in `~/app/.grok-config`). Log in to `neonctl` (`neonctl auth`) for `family` commands.
6. Install and start the units:
   ```sh
   mkdir -p ~/.config/systemd/user
   cp ~/app/deploy/castle-chef-{worker.service,update.service,update.timer} ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now castle-chef-worker castle-chef-update.timer
   tail -f ~/.local/state/castle-chef/logs/castle-chef.jsonl | jq -c '{ts, event, release, job_id}'
   ```

If the old worker service is installed under your own account, remove it: `systemctl --user disable --now castle-chef-worker && rm ~/.config/systemd/user/castle-chef-worker.service`.

## App

```sh
cd apps/mobile
pnpm install
pnpm dev          # https://localhost:5173 and https://<this machine's LAN IP>:5173
pnpm test && pnpm lint && pnpm build
```

Public Neon URLs (not secrets): `.env.development` (Neon `dev`) is used by `pnpm dev`; `.env.production` (Neon `main`) by `pnpm build`, so by TestFlight **and by any local iOS build**; `.env` holds what both share. The Account page shows the version and release (`r<N>`, or `dev` for local builds). `__APP_VERSION__` (`package.json`) also invalidates the persisted cache; bump it when the cached data's shape changes.

The dev server listens on the local network over HTTPS with a self-signed certificate (browsers warn once; accept it). It also proxies Neon Auth at `/neon-auth` so the session cookie is first-party in any browser, including Safari on a phone, and sends the trusted origin Neon Auth requires (`vite.config.ts`). If other devices can't connect, allow the port through the firewall: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

iOS (on a Mac): `pnpm build && pnpm exec cap sync ios && pnpm exec cap open ios`. This build talks to **production** (`main`).

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

The release workflow builds on a macOS runner and uploads with fastlane (`apps/mobile/fastlane/`) when `apps/mobile/` changed since the last upload ([Releases](#releases)). One-time setup:

1. In App Store Connect, create the app with bundle id `com.conncastle.castlechef`.
2. On a Mac, create the App Store profile in the match repo: `cd apps/mobile && bundle install && bundle exec fastlane match appstore`.
3. Repository secrets `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_BASE64` (base64 of the `.p8`), `MATCH_PASSWORD`, `CASTLE_CHEF_APP_CLIENT_ID`, and `CASTLE_CHEF_APP_PRIVATE_KEY`, copied from SSM ([Secrets](#secrets)). The release reads `conn-castle-apple-certs` with a one-hour token from the Castle Chef Bot app.

## Releases

Every push to `main` runs `.github/workflows/release.yml`:

1. **CI** (`ci.yml`): lint, tests (including migration up/down and the migration guard), app build, `shellcheck` of the updater.
2. **Migrate**: a Neon snapshot of `main` named `pre-r<N>` (the restore point, kept 14 days), `alembic upgrade head` on `main`, Data API schema refresh, then checks that `main` is at this release's migrations and matches the models.
3. **Publish**: GitHub release `r<N>` for the commit. The worker follows it within about 5 minutes ([Worker deployment](#worker-deployment)).
4. **iOS**, only if `apps/mobile/` changed since the last upload (or when run manually with `force_ios`): build and upload on macOS, then wait for Apple's processing on Linux (cheaper). Tag `ios-r<N>` marks a processed upload; without it, the next release builds again.

If CI, the migration, or the checks fail, nothing is published: the worker and app stay on the previous release, which works with the migrated database (backward-compatible migrations), and GitHub emails you. A failed migration leaves `main` unchanged (it runs in one transaction). Nothing is restored automatically.

Re-run a failed release from the Actions tab, or release the current `main` with `gh workflow run release.yml` (add `-f force_ios=true` to upload the app again). A run never publishes a commit older than the latest release, and only runs from `main`.

Repository secrets: `NEON_API_KEY`, `MIGRATION_DATABASE_URL` (direct owner connection to `main`), plus the [TestFlight](#testflight) secrets, all copied from SSM ([Secrets](#secrets)).

### Rollback

| What went wrong | What to do |
| --- | --- |
| The release failed | Nothing to undo. Fix and push. |
| The release is bad, the data is fine | Make the previous release latest and mark the bad one prerelease: `gh release edit r<previous> --latest && gh release edit r<N> --prerelease`. The worker moves back within about 5 minutes (after any running job). The schema stays. Then fix forward, or `git revert` and push. |
| Data was damaged | 1. Make the previous release latest (as above). On the home machine, as `castle-chef`, stop the updater and the worker so no new code runs on the older schema: `systemctl --user stop castle-chef-update.timer castle-chef-worker` (waits for a running job). 2. Run **Rollback database** (`rollback.yml`) with the release and the confirmation text, and don't push until it finishes (a new release run would cancel it while it waits). It snapshots `main` as it is (`before-rollback-r<N>-…`), restores `main` from `pre-r<N>`, refreshes the Data API cache, and fails if the Data API URL changed. Everything written since the snapshot is lost, including sign-ins (Neon Auth lives in the same database), so everyone signs in again. Restore points expire after 14 days. 3. Follow its summary: if `main`'s branch id changed, update `CASTLE_CHEF_NEON_BRANCH_ID` in the worker's `backend/.env`; restart the updater (`systemctl --user start castle-chef-update.timer`), which moves the worker to the previous release and starts it; sign in on the app; and `git revert` the release's migration and push (or the next release applies it again). Neon keeps the pre-restore `main` as another branch; delete it when done, and reset `dev` if it was a child of it. |
| A bad iOS build | Expire it in App Store Connect; testers can reinstall the previous build from TestFlight → Previous Builds. Then fix forward. |

The database restore has not been exercised yet (Neon doesn't allow a test root branch in this project), which is why it checks the Data API URL afterwards and lists what to verify.

## Secrets

**AWS SSM Parameter Store is the source of truth for every secret** (profile `conn`, region `us-east-1`, `SecureString`). GitHub secrets and `.env` files are copies generated from it. The agent manages all of them: creating, storing, copying, rotating, and revoking. Add or rotate a secret in SSM first, then update its copies and this table.

| SSM parameter | Copied to | Status |
| --- | --- | --- |
| `/castle-chef/production/database-url` | GitHub `MIGRATION_DATABASE_URL`; worker `~castle-chef/app/backend/.env` `DATABASE_URL` | set |
| `/castle-chef/development/database-url` | your checkout's `backend/.env` `DATABASE_URL` | set |
| `/castle-chef/shared/neon-api-key` (Neon key `castle-chef-release`, limited to this project) | GitHub `NEON_API_KEY` | set |
| `/castle-chef/shared/github-app-private-key` (Castle Chef Bot GitHub App) | worker `~castle-chef/.config/castle-chef/github-app.pem`; GitHub `CASTLE_CHEF_APP_PRIVATE_KEY` | set |
| `/castle-chef/shared/github-app-client-id` (not secret) | worker `~castle-chef/.config/castle-chef/github-app.env` `GITHUB_APP_CLIENT_ID`; GitHub `CASTLE_CHEF_APP_CLIENT_ID` | set |
| `/castle-chef/shared/app-store-connect-key-id` (`EXAMP12345`) | GitHub `APP_STORE_CONNECT_KEY_ID` | in SSM |
| `/castle-chef/shared/app-store-connect-issuer-id` | GitHub `APP_STORE_CONNECT_ISSUER_ID` | in SSM |
| `/castle-chef/shared/app-store-connect-api-key-base64` (the `.p8`, base64) | GitHub `APP_STORE_CONNECT_API_KEY_BASE64` | in SSM |
| `/castle-chef/shared/match-password` (encrypts `conn-castle-apple-certs`) | GitHub `MATCH_PASSWORD` | in SSM |
**Castle Chef Bot** is this repository's own GitHub App (one app per repository): private, read-only access to repository contents, installed on `castle-chef` and `conn-castle-apple-certs`, settings at https://github.com/settings/apps. Nothing stores a GitHub token: the updater and the release workflow mint one-hour tokens from its key (the updater's can read only `castle-chef`; the release's only `conn-castle-apple-certs`, for fastlane match).

The release builds iOS only once the Apple secrets and the app's are all in GitHub; the Apple ones are copied there when App Store signing is ready ([TestFlight](#testflight)).

Copy a secret to GitHub (the value never appears in the terminal):

```sh
aws ssm get-parameter --profile conn --region us-east-1 --with-decryption \
  --name /castle-chef/shared/neon-api-key --query Parameter.Value --output text |
  gh secret set NEON_API_KEY -R nicholasjconn/castle-chef
```

Store a value you have (typed without echo, never pasted into chat or a command line):

```sh
read -rs VALUE && printf %s "$VALUE" | aws ssm put-parameter --profile conn --region us-east-1 \
  --type SecureString --name /castle-chef/... --value file:///dev/stdin && unset VALUE
```

For a file such as the App Store Connect `.p8`: `base64 -i AuthKey_EXAMP12345.p8 | tr -d '\n' | aws ssm put-parameter ... --value file:///dev/stdin`.

## Development

Development uses the Neon branch `dev` (`example-development-branch`), a copy of `main` with its own Neon Auth (users and keys copied when the branch was made, then independent) and Data API. Test in the browser with `pnpm dev` and the [local demo](#local-demo) worker; iOS-only behavior (native sign-in in `src/neon.ts`, the persisted cache across relaunches) still needs a TestFlight build.

`backend/.env` for `dev`: `DATABASE_URL` from SSM `/castle-chef/development/database-url` ([Secrets](#secrets)), `CASTLE_CHEF_NEON_PROJECT_ID=example-project`, and `CASTLE_CHEF_NEON_BRANCH_ID=example-development-branch`. Both must point at `dev`, or `family invite` creates the account on one branch and the membership on the other.

Refresh `dev` from production with `neonctl branches reset dev --parent --project-id example-project`. This discards `dev`'s changes (including migrations not yet released: re-run `alembic upgrade head`). After creating or resetting `dev`, the Data API can fail with `jwk not found` for up to an hour (see [Neon](#neon)).

## Neon

Project `castle-chef` (`example-project`), database `neondb`, branches `main` (production) and `dev` ([Development](#development)). Neon Auth and the Data API are enabled on both; only the `public` schema is exposed.

Security model:

- The app signs in with Neon Auth using an emailed one-time code (no passwords, no social providers) and gets a short-lived JWT; the Data API runs its queries as the `authenticated` role.
- Invite-only: only accounts listed in `household_member` can read anything or queue jobs. An account that exists in Neon Auth but was never invited sees nothing. Sign-up is disabled in Neon Auth.
- RLS: household members can read all meals and jobs, and can only insert `generate_meals` jobs as themselves, with a valid count (1 to 14), and at most 3 pending or running at a time. Everything else is denied. Anonymous requests are rejected. Policies use two `SECURITY DEFINER` helpers in the `app_private` schema, which the Data API does not expose.
- The worker uses the owner role over a direct connection (bypasses RLS). The agent has no database access.
- Trusted auth origin: `https://app.example.test`, presented by the iOS app and the dev server's auth proxy. Keep this domain under your control. Localhost is not trusted.

### Family members (invite-only)

For production, run these as the worker user, whose `backend/.env` points at `main`: `sudo -iu castle-chef`, then `cd ~/app/backend` and use `.venv/bin/castle-chef` in place of `uv run castle-chef`. From your checkout they act on `dev`.

```sh
cd backend
uv run castle-chef family invite mom@example.com --name "Mom"   # create the account and add it
uv run castle-chef family list                                  # invited emails and names
uv run castle-chef family remove mom@example.com                # remove and delete the account
```

`invite` and `remove` need `CASTLE_CHEF_NEON_PROJECT_ID` and `CASTLE_CHEF_NEON_BRANCH_ID` in `backend/.env` and a logged-in `neonctl`. Sign-in is passwordless: the person enters their email in the app, then the 6-digit code Neon Auth emails them (from `auth@mail.myneon.app`; check spam).

Known Neon issue: the Data API caches Neon Auth's signing keys for up to an hour and does not refresh on an unknown key ([neondatabase/neon#12935](https://github.com/neondatabase/neon/issues/12935)). After a key change (or on a brand-new branch before its first sign-up), API calls can fail with `jwk not found` for up to an hour.

## Status and next steps

Working end to end in a browser (local demo above), reviewed, and CI green. Not yet done:

1. Run the iOS app on a Mac (simulator or device): sign in with a code, fully quit and relaunch (should stay signed in and show data instantly), and send logs from Account. The native auth path (`src/neon.ts`) has only been verified at the HTTP level.
2. Releases: push the first release (Neon secrets are set), then set up the worker user ([Worker deployment](#worker-deployment)); its GitHub token is still needed ([Secrets](#secrets)).
3. TestFlight: one-time setup under [TestFlight](#testflight); the next release with app changes (or `force_ios`) uploads. Then invite the family.
4. Product: give the agent meal history so it avoids repeats.
