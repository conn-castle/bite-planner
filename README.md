# Castle Chef

An agent-driven meal service for the family. A household makes grocery orders: the agent suggests meals, a member picks some and the delivery dates, and places the order. The iOS app talks to a small API on AWS; agent work goes through a queue to a one-off container that runs an AI agent (Grok, through Agent Layer) and saves its meals in Neon Postgres. Nothing runs while nobody is using it. What ordering must do is in [`spec/`](spec/).

```text
iOS app (Capacitor) ── Neon Auth (sign-in, emailed code) ── JWT
        │
        ▼
API: FastAPI on AWS Lambda (Function URL) ──► Neon Postgres (orders, jobs, meals, members)
        │ POST /v1/grocery-orders/{id}/jobs
        ▼
SQS jobs queue ──► EventBridge Pipe ──► one Fargate task per job
                                          `castle-chef job run`
                                          └─ `al grok` (unprivileged user) ── MCP tools: `castle-chef mcp`
Failures (dead-letter queue, task stopped) ──► failure-handler Lambda ──► job failed
Error log lines, Lambda and Pipe failures ──► CloudWatch alarms ──► email
```

1. A member opens the create-order flow: the API gets or creates the household's draft `grocery_order` (one per household) and gives that opening of the flow its order lock. Only the session holding it (one opening of the flow, on one phone, sent as the `Order-Session` header) can change the draft; the app renews it every 30 s and it lapses 2 minutes after the last renewal, so everyone else sees who is ordering and can take over once they stop.
2. The app asks for a batch of suggestions (`POST /v1/grocery-orders/{id}/jobs` with a job id it generates, so retries are safe). The API stores a `pending` `generate_meals` job for the draft (one batch at a time per draft) and sends its id to the queue.
3. The Pipe starts one Fargate task per message. The worker claims the job and runs Grok with a prompt asking for 20 low-effort meals (`meals_per_batch`).
4. Grok's only tools are the job's MCP tools, from a server it starts for the job (`castle-chef mcp meals`): it calls `add_meal` once per meal, then `done`, which says whether all of them are added. The tool validates each meal (refusing a repeat, or one too many) and saves it as JSON in the job directory; the agent never touches the database.
5. After Grok exits, the worker checks that exactly that many meals were saved, then stores them (each with its `job_id`) and marks the job `succeeded` in one transaction. Otherwise the job is `failed` with an error and nothing is stored. The task then exits.
6. The app polls the draft and shows the suggestions. The member picks meals (more batches if they like), chooses each meal's servings, the delivery date, and the next delivery date, reviews the order, and places it, which calls the grocery-service stub (it does nothing yet) and marks the order `placed`.

A batch takes one to two minutes: about 30 s to start the task (mostly Fargate provisioning), the rest is Grok.

## Layout

| Path | What |
| --- | --- |
| `spec/` | The ordering spec: requirements checklist and the UI mockup |
| `backend/` | Python (uv): the API, SQLModel models, Alembic migrations, `castle-chef` CLI, job worker, failure handler, tests |
| `backend/agent/` | `castle_chef_agent`: a job's agent's tools, as an MCP server (`castle-chef mcp meals|shopping-list`) and as the same commands (`castle-chef meal …`, `castle-chef shopping-list …`, for development), with their input models and checks; the backend imports and mounts them |
| `docker/agent-project/` | The worker's Agent Layer project: only the Grok settings `al grok` needs |
| `backend/src/castle_chef/prompts/` | Agent prompts |
| `apps/mobile/` | Capacitor + React + Vite app (`ios/` is the Xcode project) |
| `docker/Dockerfile` | The `api` (Lambda) and `worker` (Fargate) images |
| `infra/` | AWS CDK (Python): image repositories, deploy role, and the runtime stack |
| `Makefile`, `scripts/` | Developer commands: run `make` to list them |
| `.github/workflows/` | CI, releases (`release.yml`: images, migrations, deploy, GitHub release, TestFlight), `rollback.yml` |

Every push to `main` is a release ([Releases](#releases)). Development happens against the Neon `dev` branch ([Development](#development)).

## Local development

Runs the whole loop on this machine against the Neon `dev` branch: the API with `uvicorn`, jobs as local subprocesses (no AWS), and the app on the local network. `make` is the entry point for every development command; run `make` to list them.

```sh
make setup       # once: install dependencies, write backend/.env for dev from SSM
make dev-reset   # optional: make dev a fresh copy of production (discards dev's changes)
make dev         # the API on http://localhost:8000 and the app on https://localhost:5173
make review      # the same, for a UI review with comments from another device (below)
```

`make dev` runs the API (each request starts `castle-chef job run`) and the app's dev server, which proxies `/api` to it, until you press `Ctrl+C` or either one exits. For phones, open port 5173 once: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

**Try it:** open `https://<this machine's LAN IP>:5173` (e.g. `https://192.168.1.252:5173`) on a phone or computer and accept the certificate warning (Safari: Show Details → visit this website). Sign in with your email and the emailed code, then start an order. A batch of suggestions takes Grok a minute or two. Local jobs use your own Grok login (`al grok`) and Grok's sandbox ([Worker](#worker)).

**Stop:** `Ctrl+C`. A job already started keeps running to the end.

**UI review:** `make review` (not together with `make dev`: both use port 8000) runs the same API and app, behind the `request-html-comments` review server on `https://<LAN IP>:5174` (open the port once: `sudo ufw allow from 192.168.1.0/24 to any port 5174 proto tcp`). Open it on a phone or computer, accept the certificate warning, sign in, and comment on any element; **Send** writes the comments to `.agent-layer/tmp/reviews/` and stops everything. The review server has no login: anyone on the network who reaches the port can use the app as the signed-in account while it runs. Its self-signed certificate for this machine's address is kept in `~/.local/state/castle-chef/review-tls/`.

To try the app against a deployed API instead of the local one: `cd apps/mobile && CASTLE_CHEF_API_DEV_URL=<its URL> pnpm dev` (the deployed stack must use the same Neon branch as `.env.development`).

## Backend

`backend/.env` (gitignored, written by `make env`; `make` ignores a `DATABASE_URL` in your shell) holds `DATABASE_URL`, the direct owner-role connection string for the Neon `dev` branch, the Neon ids for `household` commands, `CASTLE_CHEF_NEON_AUTH_URL` (`dev`'s Neon Auth), and `CASTLE_CHEF_JOB_DISPATCH=local` (see [Development](#development)). It is generated from SSM ([Secrets](docs/operations/aws.md#secrets)); credentials for `main` are never in your checkout. Every setting is in `src/castle_chef/settings.py`.

The worker's packages are the project's dependencies; the API's extra packages (FastAPI, boto3, PyJWT, uvicorn) are the `api` dependency group, installed by default locally and left out of the worker image.

### Tests

Tests run against a local Postgres (Docker, port 54329), never Neon, and the checkout tools' tests drive a headless Chromium on stand-ins for Instacart's pages (`make deps` installs it):

```sh
make test      # starts the local Postgres, then backend, app, and infra tests
make lint      # lint and check formatting
make format    # fix Python formatting
make check     # lint, test, and build: what CI checks
make db-stop   # stop the local Postgres when done (data is kept)
```

The API is tested through FastAPI's test client with a test token verifier (real JWT verification is tested separately against a local JWKS server), the worker with a fake agent, and the failure handler with recorded AWS event shapes. `tests/neon_stubs.sql` creates stand-ins for the roles and `auth.uid()` that Neon provides, which older migrations use.

### Migrations

**Pre-release:** there are no real users yet, so these rules don't apply: the schema can change freely, and on 2026-09-24 it started over with one new migration. They apply from the first real user on.

Neon `main` is migrated only by the release workflow ([Releases](#releases)), before the new code is deployed. Migrations must be **backward-compatible** (expand/contract): the previous release keeps running against the migrated database until the deploy finishes, and a code rollback runs against it too.

- Adding tables, nullable (or defaulted) columns, and indexes is fine; so is replacing a constraint or index.
- To remove or rename a table or column, stop using it in one release and drop it in a later one. `tests/test_migration_guard.py` fails on a drop or rename in `upgrade()` unless the statement has `# contract: <why the previous release no longer uses it>` on the line above. `rollback.yml` refuses to go back past a contract migration without restoring the database.
- Downgrades are still written (CI tests them) but never run on `main`.

Edit `backend/src/castle_chef/models.py`, then generate the migration against local Postgres, and apply it to `dev` to try it:

```sh
make migration m="what it does"   # autogenerate in backend/migrations/versions/
make migrate                      # apply to dev
```

### Worker

```sh
make worker-job JOB=<id>   # run one pending job in the worker image, as each Fargate task does
```

A real agent runs only in the worker image: locally, `make dev` and `make review` build it and start each job in it (`CASTLE_CHEF_JOB_DISPATCH=local`), and `castle-chef job run` outside it fails the job (`CASTLE_CHEF_AGENT_COMMAND` is set only in the image; the tests set a fake agent, an MCP client of the real server).

Three kinds of job: `generate_meals` (a batch of meal ideas, saved with `castle-chef meal add`), `build_list` (a draft's shopping list: the worker scales each picked meal's ingredients to its servings and gives each line an id, e.g. `2c`; the agent combines the lines into items with the `add_item` tool and records the staples they use with `add_staple`, each naming the lines it covers, and calls `done` until every line is covered exactly once). The agent's tools check each change as it's made and say why they refuse one; they check against `job.json`, which the worker writes into the job directory (a batch's meal count, a list's lines). After the agent exits, the worker runs the same final check before saving. A list build is for the picks and servings at the moment it started (`job.list_source`); starting one for other picks fails the one still running, whose result is then dropped. And `shop` (the list's products at the Instacart store, which the worker starts right after a build succeeds, in the same task, so the member waits once; `job.build_id` names the build): the agent's tools are `search` (Instacart's search, through Codex's app-server as the agent's user, with the Codex login and its lock passed as the MCP server's arguments), `choose` (an item's product and how many, checked against its searches and, when the units compare, the item's amount, with up to eight alternatives or why there are none), `not_found` (with alternatives and/or why the meals work without it), and `done`. The worker saves each item's product and alternatives on it; the member then changes how many, switches to an alternative, or skips it. If shopping fails, `POST …/list/shop` starts it again.

**Instacart** is reached through its own tools in the ChatGPT apps: Codex's app-server (`/opt/codex` in the image, OpenAI's release package, pinned) calls them directly, with no model, signed in to the household's ChatGPT account with the Instacart app connected (`CASTLE_CHEF_CODEX_HOME`; `castle_chef_agent.instacart`). A search gives each product's size, price (per pound and per package for weighed items), picture, and whether the household has bought it before (Instacart's "Buy it again"), split into matches and related products (no matches: the store doesn't carry it); the cart can be read and its quantities set (0 takes a product out; weighed items count packages). The replies' widget data is all that's read: the rest holds the widget's own Instacart token and is never kept or logged. One app-server at a time uses the login (a lock file next to it), since two refreshing it at once can invalidate it. To check the store from the worker image: `scripts/worker-docker.sh instacart search "yellow onions"` or `… instacart cart` (read-only).

**Checkout** is Instacart's website, which the agent works through in a headless Chromium in the worker image (Playwright's Chrome Headless Shell, pinned by checksum, with its libraries from Debian's dated snapshot), signed in with the household's Instacart website session (SSM, see [Secrets](docs/operations/aws.md#secrets)). A `quote` job, for the final review: the worker sets the household's own cart items aside and fills the cart with the order's products through Instacart's cart tool (`castle_chef.cart`; a run that stops midway is undone by the next, from the household's cart ledger), then Grok reads checkout with the checkout tools (`castle_chef_agent.checkout`, prompt `prompts/checkout_quote.md`): it sees the page as its accessibility tree (just the dialog, when one is open), finds elements by their text, clicks, and types, then reports by pointing at the elements that show each amount, which the tools read and check themselves (it must add up, the tip must follow the household's rule, and the delivery can't be Priority). No page's layout is in the code; the tools refuse Place order and anything off Instacart's site. The session goes to the job directory for the tools and comes back with the report; the worker saves the renewed one to SSM and removes both copies. When Instacart signs the session out, the review says so: `make instacart-login` opens a browser on this machine (its own profile, in `~/.local/state/castle-chef/instacart-browser`), the owner signs in to the household's account, and the session goes to SSM.

Each job gets a directory (in the container `/tmp/jobs/<job-id>/`) with the prompt, `job.json`, the MCP config (`.grok/config.toml`), what the agent's tools saved, Grok's JSON output, and its stderr.

To run a job the way Fargate does (the worker image, read-only root filesystem, an empty `/tmp`, the task's capabilities, CPU and memory, Grok's version, and the agent as uid 65532), use Docker: `make worker-image` builds the image from the working tree, `make worker-login` signs its agent in to Grok once (a device code; the login stays in the Docker volume `castle-chef-grok-login`, separate from this machine's), `make worker-login AGENT=codex` connects Instacart once (the volume `castle-chef-codex-login`; see [AWS](#aws)), and `make worker-job JOB=<job-id>` runs one pending job against Neon dev, then copies its directory and Grok's session files to `~/.local/state/castle-chef/docker-jobs/<job-id>/`.

`job run` logs `worker.started` with its release (`YYYY.M.D.N`, or `dev-<sha>` for an untagged checkout) and commit. It refuses to run (`schema.behind`, exit status 78) if the database's migrations are older than the code. A database that is *newer* than the code (after a rollback) is fine. A job someone else already claimed is left alone, and a job that no longer exists (discarded with its draft) ends quietly (both exit 0).

The worker runs `al -q grok`, so Grok uses the worker's Agent Layer settings (`docker/agent-project/.agent-layer/config.toml`: model and reasoning effort). Grok's headless mode auto-approves every tool call, so the run is contained by construction:

- The working directory is the job directory (no project instructions). The agent's only tools are the job's MCP tools: Grok gets `--tools search_tool,use_tool` (how it reaches MCP tools) and `--trust` (so it starts the job directory's MCP server); it has no shell, file tools, or subagents. An agent with a shell went looking through the application's code before doing the job, even when there was nothing to find but its own commands.
- The agent gets a minimal environment (`HOME`, `PATH`, `CASTLE_CHEF_JOB_DIR`, and the Grok settings it needs): no database credentials.
- The prompts contain only fixed text and what agents made earlier (a batch's prompt: the meal count and the names of meals marked "not for us"; a shopping list's: the picked meals' names and ingredients, scaled to their servings), never free text from a member, so there is no prompt-injection channel. Keep it that way: a free-text job parameter would become input to an auto-approving agent.
- **The container** is the sandbox: the worker runs as root and starts the agent as the unprivileged user `agent` (uid 65532) with no other groups and no inherited files, so it can't read the worker's environment (`DATABASE_URL`). The MCP server Grok starts runs as that user too, and reaches only what its job needs: its `PATH` (`CASTLE_CHEF_AGENT_PATH`) is its own `castle-chef` (`/opt/castle-chef-agent`: the `castle_chef_agent` package alone, installed with its dependencies) and system commands; `/app` (the backend) and the worker's logs are root-only. The root filesystem is read-only; only `/tmp` and the logins on EFS are writable. Because `al` writes into its project on every launch, each task gets its own copy of the worker's Agent Layer project (`docker/agent-project`: Grok's settings only, none of this repository's agent instructions or skills) in `/tmp/agent-project`. Grok's login is readable by the agent's user (Grok needs it to run); the agent itself has no tool that reads files, but if the login ever leaks, sign out of that session from the Grok account.
- Parallel jobs take turns running the agent (a lock file next to the login on EFS), because two agents refreshing one login at once can invalidate it. Each Fargate task is capped at 40 minutes (each agent run at 15: a list, then its shopping).

### Logs

Everything logs structured JSON, one object per line: `{"ts", "level", "logger", "event", ...fields}`. On AWS each part has a CloudWatch log group, kept 90 days: the API's, the worker's, and the failure handler's (`aws logs tail <group> --follow --profile conn`, or Logs Insights). Locally, the API and CLI log to stderr and `~/.local/state/castle-chef/logs/castle-chef.jsonl` (rotated at 10 MB, 5 files). A job's worker runs in the worker image and logs to its container's output: `make worker-job` shows it, and saves it with the job's files as `~/.local/state/castle-chef/docker-jobs/<job-id>/worker.log`.

- Worker: `worker.started` (release, commit), `job.claimed`, `agent.lock_acquired` (seconds waited), `agent.started`, `agent.finished` (seconds, turns, cost), `job.succeeded`, `job.failed`, `schema.behind`.
- Failure handler: `job.worker_stopped` and `job.never_started` (both errors).
- App: each entry is `{"ts", "level", "event", "launch", ...fields}`. Entries at info and above wait in an outbox on the device (1,000 entries; overflow drops the oldest and reports `logs.dropped`) and are uploaded automatically, every minute while the app is open and as soon as it goes to the background, for signed-in household members (`src/lib/logUpload.ts`). The API writes a `client.batch` line (sender, device) and one `client.log` line per entry, at the entry's level, to its log group (logger `castle_chef.client`). The app logs `error` only for defects (a crash, an uncaught exception), so only those email an alarm; expected failures (offline, a refused request) are warnings.

Every line at level `error` or `critical` emails an alarm ([AWS](docs/operations/aws.md)).

```sh
tail -f ~/.local/state/castle-chef/logs/castle-chef.jsonl | jq -c '{ts, event, job_id}'
```


## AWS

AWS (the stacks, signing in, notifications, alarms, the agent and Instacart logins, deploying by hand) and every secret (SSM is the source of truth) are in [docs/operations/aws.md](docs/operations/aws.md). Local sign-in always uses a device code: `aws sso login --profile conn --use-device-code`.

## App

`apps/mobile/` is the Capacitor project and `src/` is the app's only source: `make dev` serves it directly, `make build` compiles it to `dist/`, and Capacitor copies `dist/` into the iOS project (`ios/App/App/public/`, gitignored). Both are build output.

The UI is [Ionic React](https://ionicframework.com/docs/react) 9 (iOS mode on every platform) with `IonReactRouter` (react-router 6, which Ionic 9 requires): three tabs, **Orders** (`/week`: start an order, or continue or discard the household's draft), **History** (`/history`: past orders and their meals), and **Settings** (`/settings`), each with its own navigation stack. The create-order flow is a card over the page, one thing per step: pick meals (with a recipe view inside it), servings, the delivery date, the next delivery date, and a final review that places the order. Sign-in and "not in a household" are shown instead of the tabs. `src/` is organized by feature:

| Path | What |
| --- | --- |
| `app/` | The shell: providers, session gate, tabs and routes, error boundary, offline banner, native launch |
| `features/<feature>/` | One folder per feature (`session`, `week`, `orders`, `history`, `meals`, `settings`): its screens, API calls and queries, and pure logic with its tests |
| `ui/` | Shared components (page frame, buttons, stepper, grouped rows, empty states, illustrations) |
| `lib/` | Infrastructure: API client, auth (`auth.ts`, `neonAuth.ts`), query client, logging, haptics |
| `theme/` | Design tokens (`tokens.css`: every color, radius, shadow, and font) and the styling of Ionic's structural components (`app.css`) |

The look is light only: white surfaces, ink text, one warm accent, the system font for text (SF Pro; Inter where it is missing) and SF Pro Rounded for titles, a floating glass tab bar, and headers that turn to glass once content scrolls under them. Inter is bundled (`@fontsource`), so no font loads from the network. Component styles are CSS modules next to their components and use only the tokens. Native plugins: Haptics (on meaningful actions only), Status Bar (dark text), Splash Screen (hidden once the first screen renders), and Keyboard; after adding a plugin, `pnpm exec cap sync ios` updates `ios/App/CapApp-SPM/Package.swift`.

Public URLs (not secrets): `.env.development` (Neon `dev`'s auth, and the API at `/api`, which the dev server proxies to `localhost:8000`) is used by `pnpm dev`; `.env.production` (Neon `main`'s auth and the prod API's Function URL, which stays the same across deploys) by `pnpm build`, so by TestFlight **and by any local iOS build**; `.env` holds what both share. Settings shows the app version and the release (`YYYY.M.D.N`, or `dev` for local builds). `__APP_VERSION__` (`package.json`) also invalidates the persisted cache; bump it when the cached data's shape changes.

The dev server listens on the local network over HTTPS with a self-signed certificate (browsers warn once; accept it). It also proxies Neon Auth at `/neon-auth` so the session cookie is first-party in any browser, including Safari on a phone, and sends the trusted origin Neon Auth requires (`vite.config.ts`). If other devices can't connect, allow the port through the firewall: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

iOS (on a Mac): `make ios` builds, copies the build into the iOS project, and opens Xcode. This build talks to **production** (`main`). Opening Xcode without `make ios` runs whatever was copied last.

Data handling (`src/lib/query/queryClient.ts` and each feature's queries):

- Server data lives in TanStack Query: shown from cache immediately, refreshed in the background (stale-while-revalidate).
- The cache is persisted on the device (Capacitor Preferences) for 7 days, so a relaunch shows the last data instantly. Sign-out deletes it.
- Refreshes when the app returns to the foreground (`@capacitor/app`) and pauses while offline (`@capacitor/network`), with an offline banner. Requests made offline are sent when the connection returns.
- An order refreshes every 3 s while a batch of suggestions is pending or running; Home refreshes the next order every 30 s while it shows (to see who is ordering), and the order flow every 30 s while someone else has the order's lock.
- Changes to a draft carry the order flow's session (`Order-Session` header). Requests that only the open flow can make (opening it, taking and releasing the lock) aren't kept for after a relaunch.
- Every request sends the phone's time zone (`Time-Zone` header); the API uses it for "today".
- Render errors show a recovery screen and are logged.

On iOS, Neon Auth requests go through Capacitor's native HTTP with the trusted origin `https://app.example.test`, because the WebView's `capacitor://localhost` origin can't be trusted and its cookies would be third-party. See `src/lib/auth/neonAuth.ts`, the Neon Auth implementation of `src/lib/auth/auth.ts` (the app's only dependency on the auth provider). `src/lib/api/client.ts` calls the API with a fresh token per request.

### TestFlight

The release workflow builds on a macOS runner and uploads with fastlane (`apps/mobile/fastlane/`) when `apps/mobile/` changed since the last upload ([Releases](#releases)). It signs with the App Store certificate and profile that fastlane match keeps, encrypted, in `nicholasjconn/conn-castle-apple-certs` (shared with other apps; read with a one-hour token from the Castle Chef Bot app).

Set up (team `EXAMP12345`):

- Bundle id `com.conncastle.castlechef` ("Castle Chef"), registered through the App Store Connect API.
- Distribution certificate `EXAMP12345` (expires 2027-09-24) and the profile `match AppStore com.conncastle.castlechef`, created by the `certificates` lane. The app ID has Push Notifications on, and the profile includes it (`aps-environment` production; regenerated 2026-09-26). To renew, or after the app ID gains a capability (which invalidates the profile), run the lane with write access to the certificates repository, as Nick (it's done with him, not by automation): on a Mac, `cd apps/mobile && bundle exec fastlane certificates` with the `APP_STORE_CONNECT_*` and `MATCH_PASSWORD` values from SSM, `MATCH_GIT_BASIC_AUTHORIZATION` (base64 of `nicholasjconn:$(gh auth token)`), and `MATCH_FORCE=true` (to recreate a profile after a capability change) in the environment. On Linux it also works in a `ruby` container, provided `FastlaneCore::ProvisioningProfile.install` is stubbed out (it needs Xcode) and git has an author identity.
- The app record in App Store Connect (**My Apps → + → New App**: iOS, name "Castle Chef", bundle id `com.conncastle.castlechef`, SKU `castle-chef`); Apple's API can't create it.
- Repository secrets `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_BASE64` (base64 of the `.p8`), `MATCH_PASSWORD`, `CASTLE_CHEF_APP_CLIENT_ID`, and `CASTLE_CHEF_APP_PRIVATE_KEY`, copied from SSM ([Secrets](docs/operations/aws.md#secrets)).

`Info.plist` declares `ITSAppUsesNonExemptEncryption = false` (the app uses only standard HTTPS), so builds don't wait for an export-compliance answer.

## Releases

Every push to `main` is a release, versioned by date: `YYYY.M.D.N`, the Nth release that day (US Eastern), e.g. `2026.9.24.1`. The iOS app built from it has the same numbers: version `2026.9.24`, build `1`, which is what TestFlight shows. Releases before 2026-09-24 were numbered `r<N>`.

Every push to `main` runs `.github/workflows/release.yml`:

1. **CI** (`ci.yml`): lint and tests for the backend (including migration up/down and the migration guard), the app (and its build), and `infra/`.
2. **Images**, alongside CI: `api` and `worker` built from `docker/Dockerfile` and pushed to ECR as `commit-<sha>` (the worker with zstd layers, which Fargate starts faster). A re-run reuses images already pushed for its commit.
3. **Migrate**: a Neon snapshot of `main` named `pre-<release>` (the restore point, kept 14 days), `alembic upgrade head` on `main`, then checks that `main` is at this release's migrations and matches the models.
4. **Deploy** (`.github/actions/deploy`): `cdk deploy` of both stacks (stage `prod`) with the images' digests, through the `castle-chef-github-deploy` role (GitHub OIDC; no AWS keys stored), then a smoke test (`GET /health` must answer and `GET /v1/me` without a token must return 401), then every member's reminder schedule is rebuilt from the database (the Reminders Lambda's sync; it fails the deploy if any schedule couldn't be saved).
5. **Publish**: the GitHub release for the commit, with `release.json` (commit, image digests, migration), which `rollback.yml` uses.
6. **iOS**, only if `apps/mobile/` changed since the last upload (or when run manually with `force_ios`): build on macOS, check that the app has the production push entitlement, upload, then wait for Apple's processing on Linux (cheaper). Tag `ios-<release>` marks a processed upload; without it, the next release builds again.

If CI, the images, the migration, or its checks fail, nothing is deployed: AWS keeps running the previous release, which works with the migrated database (backward-compatible migrations), and GitHub emails you. A failed migration leaves `main` unchanged (it runs in one transaction). A failed `cdk deploy` is rolled back by CloudFormation. If the smoke test fails, the new code is deployed but no release is published: fix forward, or go back with `rollback.yml`.

Re-run a failed release from the Actions tab, or release the current `main` with `gh workflow run release.yml` (add `-f force_ios=true` to upload the app again). A run never deploys a commit older than the latest release, and only runs from `main`.

Repository secrets: `NEON_API_KEY`, `MIGRATION_DATABASE_URL` (direct owner connection to `main`), plus the [TestFlight](#testflight) secrets, all copied from SSM ([Secrets](docs/operations/aws.md#secrets)). AWS access needs no secret.

### Rollback

**Rollback** (`rollback.yml`, run from the Actions tab or `gh workflow run rollback.yml -f release=<release> -f confirm="roll back to <release>"`) redeploys an earlier release's images with that release's own CDK code, without rebuilding, and smoke-tests it. Only releases with `release.json` (from `r6` on, the first deployed to AWS) can be rolled back to. Don't push while it runs (a new release run would cancel it while it waits).

| What went wrong | What to do |
| --- | --- |
| The release failed | Nothing to undo. Fix and push. |
| The release is bad, the data is fine | Run **Rollback** with the previous release. The database keeps the newer schema, which the older code works with; the workflow refuses if a contract migration since then removed something it needs or holds data it can't handle safely (e.g. `households`: older releases would show one household's requests to another). Then `git revert` the bad commits and push (the next push releases `main` again). |
| Data was damaged | Run **Rollback** with the last good release, `restore_database` checked, and the confirmation `roll back to <release> and restore the database`. It stops the Pipe (no new jobs start; requests wait in the queue), waits for running workers, snapshots `main` as it is (`before-rollback-<release>-…`), restores `main` from the snapshot taken before the next release, deploys the release, and starts the Pipe again. Everything written since that snapshot is lost, including sign-ins (Neon Auth lives in the same database), so everyone signs in again. Restore points expire after 14 days. Then follow its summary: `git revert` the bad commits and their migrations and push; if `main`'s branch id changed, update `CASTLE_CHEF_NEON_BRANCH_ID` where you run `household` commands; delete the pre-restore branch Neon keeps when done, and reset `dev` if it was a child of it. |
| A bad iOS build | Expire it in App Store Connect; testers can reinstall the previous build from TestFlight → Previous Builds. Then fix forward. |

A code rollback was rehearsed on 2026-09-24 (r8 to r7). The database restore has not been exercised (Neon doesn't allow a test root branch in this project); it also assumes the restored branch keeps `main`'s endpoints, so check sign-in and a request afterwards.

## Development

Development uses the Neon branch `dev` (`example-development-branch`), a copy of `main` with its own Neon Auth (users and keys copied when the branch was made, then independent). Test in the browser with [local development](#local-development); iOS-only behavior (native sign-in in `src/lib/auth/neonAuth.ts`, the persisted cache across relaunches) still needs a TestFlight build.

`make env` writes `backend/.env` for `dev`: `DATABASE_URL` from SSM `/castle-chef/development/database-url` ([Secrets](docs/operations/aws.md#secrets)), the Neon project and `dev` branch ids (`Makefile`), `CASTLE_CHEF_NEON_AUTH_URL` (from `apps/mobile/.env.development`), and `CASTLE_CHEF_JOB_DISPATCH=local`. The database and the Neon ids must all point at `dev`, or `household invite` creates the account on one branch and the membership on the other.

`make dev-reset` (it asks first) replaces `dev` with a copy of production as it is now, then applies this checkout's migrations. It discards everything on `dev`, including its Neon Auth state (users and sessions become production's, so sign in again). Connection details stay the same, so `backend/.env` still works. The copy is the family's real data: keep it on this machine.

## Neon

Project `castle-chef` (`example-project`), database `neondb`, branches `main` (production) and `dev` ([Development](#development)), each with its own Neon Auth.

Security model:

- The app signs in with Neon Auth using an emailed one-time code (no passwords, no social providers) and gets a 15-minute JWT (EdDSA). The API verifies it against the branch's published keys (`castle_chef/auth.py`, behind an `AuthVerifier` interface so the provider can be swapped).
- Invite-only: the API serves only accounts in a household (an `app_user` row, the app's side of a Neon Auth account, with a `household_id`); anyone else gets 403. Sign-up is disabled in Neon Auth, but only for passwords: Neon's setting doesn't cover emailed codes, so anyone can request a code for any address, and entering it creates an empty account (tested on `dev`, 2026-09-24). Such accounts get no data (403). Accepted for the private TestFlight app. To close it, use Neon Auth's `send.otp` and `user.before_create` webhooks to email codes only to invited people, or wait for Neon to apply disable-sign-up to emailed codes.
- Households are separate: every order, job, and meal belongs to a household through its order, members see only their household's, and another household's is 404 like a missing one. `GET /v1/me` reports the caller's `household` (`id`, `name`, or null).
- The API enforces the ordering rules: one draft per household, one batch of suggestions at a time per draft, client-generated job ids that can't be reused for another order or by someone else, picks only from the draft's suggestions, valid dates, and no changes once placed.
- The API and worker use the owner role through Neon's pooled endpoint; the agent has no database access. Local tools use the direct endpoint.
- Trusted auth origin: `https://app.example.test`, presented by the iOS app and the dev server's auth proxy. Keep this domain under your control. Localhost is not trusted.
- The app doesn't use the Neon Data API. The `remove Data API access` migration removed its grants, row-level security, policies, and `client_log_batch`, so its roles (`authenticated`, `anonymous`, which Neon keeps while Neon Auth is on) can't read or write any table (`tests/test_data_api_access.py`), and the Data API itself is deleted per branch with `neonctl data-api delete --project-id example-project --branch <branch> --database neondb` (done on `dev` and `main`).

### Households (invite-only)

Each account belongs to at most one household and sees only that household's orders and meals. The family is `Conn Castle`. Keep testing in a household of its own so its accounts and orders never show up in the family's: on `dev` that is `Test Kitchen`, with the account `operator@example.com` (its codes arrive in the owner's Gmail). `make dev-reset` replaces `dev` with production, which has neither; create them again with the commands below.

From your checkout these act on `dev` (`backend/.env`). For production, run them with `main`'s credentials from SSM (nothing is written to disk):

```sh
cd backend
uv run castle-chef household create "Test Household"   # a new, empty household
uv run castle-chef household list                       # names, member and order counts
uv run castle-chef household invite mom@example.com --name "Mom" --household "Conn Castle"
                                                        # create the account if needed and add it
uv run castle-chef household members "Conn Castle"      # its members' emails and names
uv run castle-chef household remove mom@example.com     # remove and delete the account

# Production: prefix each command with `prod`
prod() { DATABASE_URL="$(aws ssm get-parameter --profile conn --region us-east-1 --with-decryption \
  --name /castle-chef/production/database-url --query Parameter.Value --output text)" \
  CASTLE_CHEF_NEON_BRANCH_ID=br-example-00000000 "$@"; }
prod uv run castle-chef household list
```

`invite` refuses someone already in another household (remove them first); inviting them to their own household again changes nothing. `invite` and `remove` need `CASTLE_CHEF_NEON_PROJECT_ID` and `CASTLE_CHEF_NEON_BRANCH_ID` and a logged-in `neonctl`. Sign-in is passwordless: the person enters their email in the app, then the 6-digit code Neon Auth emails them (from `auth@mail.myneon.app`; check spam).

## Status and next steps

Production runs on AWS (since release `r6`; now date-versioned, e.g. `2026.9.24.1`), with alarms emailing the owner, and the iOS app on TestFlight (verified on a phone: sign-in, relaunch, requests, sending logs). A code rollback was rehearsed. Next:

1. Product: give the agent meal history so it avoids repeats, and iterate on the app.
2. Invite the family ([Households](#households-invite-only)), as App Store Connect users (internal testers) or external testers (Apple reviews the first build).
3. Build the ordering spec one piece at a time, in the order in [`spec/README.md`](spec/README.md#build-order): the create-order flow is done; Home (Orders) is next.
