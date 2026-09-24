# Castle Chef

An agent-driven meal service for the family. The iOS app talks to a small API on AWS; meal requests go through a queue to a one-off container that runs an AI agent (Grok, through Agent Layer) and saves its meals in Neon Postgres. Nothing runs while nobody is using it.

```text
iOS app (Capacitor) ── Neon Auth (sign-in, emailed code) ── JWT
        │
        ▼
API: FastAPI on AWS Lambda (Function URL) ──► Neon Postgres (meals, jobs, members)
        │ POST /v1/jobs
        ▼
SQS jobs queue ──► EventBridge Pipe ──► one Fargate task per job
                                          `castle-chef job run`
                                          └─ `al grok` (unprivileged user) ── `castle-chef meal add`
Failures (dead-letter queue, task stopped) ──► failure-handler Lambda ──► job failed
Error log lines, Lambda and Pipe failures ──► CloudWatch alarms ──► email
```

1. The app asks the API for N meals (`POST /v1/jobs` with a job id it generates, so retries are safe). The API checks the person is a household member, allows at most 3 open requests each, stores a `pending` job, and sends its id to the queue.
2. The Pipe starts one Fargate task per message. The worker claims the job and runs Grok with a prompt asking for N low-effort meals.
3. Grok calls `castle-chef meal add` once per meal. The CLI validates the meal and saves it as JSON in the job directory; the agent never touches the database.
4. After Grok exits, the worker checks that exactly N meals were saved, then stores them (linked through `job_meal`) and marks the job `succeeded` in one transaction. Otherwise the job is `failed` with an error and nothing is stored. The task then exits.
5. The app polls the job, shows its meals, and lets you rescale servings.

A request takes one to two minutes: about 30 s to start the task (mostly Fargate provisioning), the rest is Grok.

## Layout

| Path | What |
| --- | --- |
| `backend/` | Python (uv): the API, SQLModel models, Alembic migrations, `castle-chef` CLI, job worker, failure handler, tests |
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
```

`make dev` runs the API (each request starts `castle-chef job run`) and the app's dev server, which proxies `/api` to it, until you press `Ctrl+C` or either one exits. For phones, open port 5173 once: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

**Try it:** open `https://<this machine's LAN IP>:5173` (e.g. `https://192.168.1.252:5173`) on a phone or computer and accept the certificate warning (Safari: Show Details → visit this website). Sign in with your email and the emailed code, then request meals. Grok takes 30–60 s. Local jobs use your own Grok login (`al grok`) and Grok's sandbox ([Worker](#worker)).

**Stop:** `Ctrl+C`. A job already started keeps running to the end.

To try the app against a deployed API instead of the local one: `cd apps/mobile && CASTLE_CHEF_API_DEV_URL=<its URL> pnpm dev` (the deployed stack must use the same Neon branch as `.env.development`).

## Backend

`backend/.env` (gitignored, written by `make env`; `make` ignores a `DATABASE_URL` in your shell) holds `DATABASE_URL`, the direct owner-role connection string for the Neon `dev` branch, the Neon ids for `family` commands, `CASTLE_CHEF_NEON_AUTH_URL` (`dev`'s Neon Auth), and `CASTLE_CHEF_JOB_DISPATCH=local` (see [Development](#development)). It is generated from SSM ([Secrets](#secrets)); credentials for `main` are never in your checkout. Every setting is in `src/castle_chef/settings.py`.

The worker's packages are the project's dependencies; the API's extra packages (FastAPI, boto3, PyJWT, uvicorn) are the `api` dependency group, installed by default locally and left out of the worker image.

### Tests

Tests run against a local Postgres (Docker, port 54329), never Neon:

```sh
make test      # starts the local Postgres, then backend, app, and infra tests
make lint      # lint and check formatting
make format    # fix Python formatting
make check     # lint, test, and build: what CI checks
make db-stop   # stop the local Postgres when done (data is kept)
```

The API is tested through FastAPI's test client with a test token verifier (real JWT verification is tested separately against a local JWKS server), the worker with a fake agent, and the failure handler with recorded AWS event shapes. `tests/neon_stubs.sql` creates stand-ins for the roles and `auth.uid()` that Neon provides, which older migrations use.

### Migrations

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
uv run castle-chef job create --count 3   # queue a job and run it (locally, or on AWS with CASTLE_CHEF_JOB_DISPATCH=sqs)
CASTLE_CHEF_JOB_ID=<id> uv run castle-chef job run   # run one pending job; what each Fargate task does
```

Each job gets a directory (locally `~/.local/state/castle-chef/jobs/<job-id>/`, in the container `/tmp/jobs/`) with the prompt, Grok's JSON output, and its stderr.

`job run` logs `worker.started` with its release (`YYYY.M.D.N`, or `dev-<sha>` for an untagged checkout) and commit. It refuses to run (`schema.behind`, exit status 78) if the database's migrations are older than the code. A database that is *newer* than the code (after a rollback) is fine. A job someone else already claimed is left alone (exit 0); a job that doesn't exist exits 1.

The worker runs `al -q grok`, so Grok uses Agent Layer's model settings (`.agent-layer/config.toml`). Grok's headless mode auto-approves every tool call, so the run is contained by construction:

- The working directory is the job directory (no project instructions); the only tool is the shell; subagents are disabled.
- The agent gets a minimal environment (`HOME`, `PATH`, `CASTLE_CHEF_JOB_DIR`, and the Grok settings it needs): no database credentials.
- The prompt contains only the validated meal count, so there is no prompt-injection channel. Keep it that way: a free-text job parameter would become input to an auto-approving shell agent.
- **Locally**, Grok's own sandbox (profile `castle-chef-agent`, extending `strict`) lets commands read only the job directory, system paths, `backend/.venv`, `backend/src`, and Grok's home; write only the job directory and `/tmp`; and blocks network access for commands.
- **In the container**, which is the sandbox there: the worker runs as root and starts the agent as the unprivileged user `agent` (uid 65532) with no other groups and no inherited files, so it can't read the worker's environment (`DATABASE_URL`). The root filesystem is read-only; only `/tmp` and the Grok login on EFS are writable. Because `al` writes into its project on every launch, each task gets its own copy of `.agent-layer` in `/tmp/agent-project`. The agent can read Grok's login (it needs it to run), so a misbehaving agent could copy it into a meal; sign out of that session from the Grok account if that ever happens.
- Parallel jobs take turns running the agent (a lock file next to the login on EFS), because two agents refreshing one login at once can invalidate it. Each Fargate task is capped at 20 minutes (the agent at 15).

### Logs

Everything logs structured JSON, one object per line: `{"ts", "level", "logger", "event", ...fields}`. On AWS each part has a CloudWatch log group, kept 90 days: the API's, the worker's, and the failure handler's (`aws logs tail <group> --follow --profile conn`, or Logs Insights). Locally, the worker and CLI log to stderr and `~/.local/state/castle-chef/logs/castle-chef.jsonl` (rotated at 10 MB, 5 files).

- Worker: `worker.started` (release, commit), `job.claimed`, `agent.lock_acquired` (seconds waited), `agent.started`, `agent.finished` (seconds, turns, cost), `job.succeeded`, `job.failed`, `schema.behind`.
- Failure handler: `job.worker_stopped` and `job.never_started` (both errors).
- App: each entry is `{"ts", "level", "event", "launch", ...fields}`, kept in a 1,000-entry buffer on the device. Account → **Send logs** posts it to the API, which writes a `client.batch` line (sender, device) and one `client.log` line per entry to its log group (logger `castle_chef.client`).

Every line at level `error` or `critical` emails an alarm ([AWS](#aws)).

```sh
tail -f ~/.local/state/castle-chef/logs/castle-chef.jsonl | jq -c '{ts, event, job_id}'
```

## AWS

Account `123456789012` (profile `conn`), region `us-east-1`, defined in `infra/` (CDK, Python). Releases deploy it ([Releases](#releases)); nothing needs a manual deploy. Stacks:

- `CastleChefRepositories`: ECR repositories `castle-chef-api` and `castle-chef-worker` (immutable tags; the last 30 released images `commit-<sha>` and 5 development pushes `dev-…` are kept), and the role `castle-chef-github-deploy`, which only this repository's `main` branch can assume (GitHub OIDC).
- `CastleChef` (stage `prod`, Neon `main`): a VPC with public subnets only (no NAT), the API and failure-handler Lambdas, the jobs queue and its dead-letter queue, the Pipe, the ECS cluster and worker task definition, EFS for the agent's login, log groups, and alarms. Its outputs name the API URL, cluster, task definition, subnets, security group, log group, and Pipe. `CastleChef-dev` (stage `dev`, Neon `dev`) is the same stack for trying infrastructure changes; it is not deployed now.

Costs: nothing runs while idle; EFS, ECR, and alarms are well under $1 a month, a job is a fraction of a cent of Fargate, and Grok is the subscription. Lambda and the Pipe scale to zero.

Secrets never sit in configuration: the Lambdas read `DATABASE_URL` from SSM at startup (`CASTLE_CHEF_SSM_PARAMETERS`), and ECS injects it into the worker from SSM. Both use Neon's pooled endpoint.

**Alarms** email `operator@example.com` (SNS topic "Castle Chef alarms"; the subscription must be confirmed from the first email): any error-level log line, Lambda errors, and Pipe failures. A request that fails because of the agent (bad output, timeout, an expired login) also logs at error level, so it emails too.

**Failures** mark jobs failed without polling: a message the Pipe can't start a task for goes to the dead-letter queue after 3 tries, and a worker task that stops with a non-zero exit (crash, out of memory, image pull or EFS failure, the 20-minute cap) sends an ECS event; the failure handler fails its job either way. As a backstop, the API fails a person's jobs that stayed pending for 10 minutes or running for 20.

**Agent login.** The worker's agent signs in to its subscription (Grok) once, with a device code; the login lives on EFS and the agent refreshes it. To sign in again (e.g. after `job.failed` with an authentication error), run a one-off worker task with the command `agent-login grok` and approve the code it prints:

```sh
out() { aws cloudformation describe-stacks --profile conn --region us-east-1 --stack-name CastleChef \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
aws ecs run-task --profile conn --region us-east-1 --launch-type FARGATE \
  --cluster "$(out ClusterNameOutput)" --task-definition "$(out WorkerTaskDefinitionOutput)" \
  --network-configuration "awsvpcConfiguration={subnets=[$(out WorkerSubnetsOutput)],securityGroups=[$(out WorkerSecurityGroupOutput)],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"worker","command":["agent-login","grok"]}]}'
aws logs tail "$(out WorkerLogGroupOutput)" --follow --profile conn --region us-east-1   # the link and code
```

The task ends when you approve (`agent.login_finished`) or the code expires. The login is shared by every job; this is the one credential not in SSM, because the agent rewrites it as it refreshes.

**Deploying by hand** (not needed normally; e.g. the first deploy of a new account or of `CastleChef-dev`), with AWS credentials from the `conn` profile:

```sh
cd infra && uv sync
eval "$(aws configure export-credentials --profile conn --format env)"
npx aws-cdk@2.1143.0 deploy CastleChefRepositories -c repositoriesOnly=true
# Push images first (tags dev-<sha>-<time>), then:
npx aws-cdk@2.1143.0 deploy CastleChef-dev -c stage=dev -c apiImageDigest=sha256:… -c workerImageDigest=sha256:…
```

A new stack has its own EFS, so run the agent login for it once. `cd infra && uv run pytest` checks what the stacks must guarantee (CI runs it).

## App

`apps/mobile/` is the Capacitor project and `src/` is the app's only source: `make dev` serves it directly, `make build` compiles it to `dist/`, and Capacitor copies `dist/` into the iOS project (`ios/App/App/public/`, gitignored). Both are build output.

Public URLs (not secrets): `.env.development` (Neon `dev`'s auth, and the API at `/api`, which the dev server proxies to `localhost:8000`) is used by `pnpm dev`; `.env.production` (Neon `main`'s auth and the prod API's Function URL, which stays the same across deploys) by `pnpm build`, so by TestFlight **and by any local iOS build**; `.env` holds what both share. The Account page shows the app version and the release (`YYYY.M.D.N`, or `dev` for local builds). `__APP_VERSION__` (`package.json`) also invalidates the persisted cache; bump it when the cached data's shape changes.

The dev server listens on the local network over HTTPS with a self-signed certificate (browsers warn once; accept it). It also proxies Neon Auth at `/neon-auth` so the session cookie is first-party in any browser, including Safari on a phone, and sends the trusted origin Neon Auth requires (`vite.config.ts`). If other devices can't connect, allow the port through the firewall: `sudo ufw allow from 192.168.1.0/24 to any port 5173 proto tcp`.

iOS (on a Mac): `make ios` builds, copies the build into the iOS project, and opens Xcode. This build talks to **production** (`main`). Opening Xcode without `make ios` runs whatever was copied last.

Data handling (`src/queryClient.ts`, `src/queries.ts`):

- Server data lives in TanStack Query: shown from cache immediately, refreshed in the background (stale-while-revalidate).
- The cache is persisted on the device (Capacitor Preferences) for 7 days, so a relaunch shows the last data instantly. Sign-out deletes it.
- Refreshes when the app returns to the foreground (`@capacitor/app`) and pauses while offline (`@capacitor/network`), with an offline banner. Requests made offline are sent when the connection returns.
- The request list refreshes every 10 s while any request is pending or running.
- Requesting meals is optimistic: the request appears at once and reconciles with the server.
- Meals of recent finished requests are prefetched, so opening one is instant.
- Hash routing (`/`, `/requests/:id`, `/account`) gives real back navigation in the WebView. Render errors show a recovery screen and are logged.

On iOS, Neon Auth requests go through Capacitor's native HTTP with the trusted origin `https://app.example.test`, because the WebView's `capacitor://localhost` origin can't be trusted and its cookies would be third-party. See `src/neonAuth.ts`, the Neon Auth implementation of `src/auth.ts` (the app's only dependency on the auth provider). `src/api.ts` calls the API with a fresh token per request.

### TestFlight

The release workflow builds on a macOS runner and uploads with fastlane (`apps/mobile/fastlane/`) when `apps/mobile/` changed since the last upload ([Releases](#releases)). It signs with the App Store certificate and profile that fastlane match keeps, encrypted, in `nicholasjconn/conn-castle-apple-certs` (shared with other apps; read with a one-hour token from the Castle Chef Bot app).

Set up (team `EXAMP12345`):

- Bundle id `com.conncastle.castlechef` ("Castle Chef"), registered through the App Store Connect API.
- Distribution certificate `EXAMP12345` (expires 2027-09-24) and the profile `match AppStore com.conncastle.castlechef`, created by the `certificates` lane. To renew (or after `match` reports an invalid certificate), run it with write access to the certificates repository: on a Mac, `cd apps/mobile && bundle exec fastlane certificates` with the `APP_STORE_CONNECT_*` and `MATCH_PASSWORD` values from SSM and `MATCH_GIT_BASIC_AUTHORIZATION` (base64 of `x-access-token:<a token that can push>`) in the environment. On Linux it also works in a `ruby` container, provided `FastlaneCore::ProvisioningProfile.install` is stubbed out (it needs Xcode) and git has an author identity.
- The app record in App Store Connect (**My Apps → + → New App**: iOS, name "Castle Chef", bundle id `com.conncastle.castlechef`, SKU `castle-chef`); Apple's API can't create it.
- Repository secrets `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_BASE64` (base64 of the `.p8`), `MATCH_PASSWORD`, `CASTLE_CHEF_APP_CLIENT_ID`, and `CASTLE_CHEF_APP_PRIVATE_KEY`, copied from SSM ([Secrets](#secrets)).

`Info.plist` declares `ITSAppUsesNonExemptEncryption = false` (the app uses only standard HTTPS), so builds don't wait for an export-compliance answer.

## Releases

Every push to `main` is a release, versioned by date: `YYYY.M.D.N`, the Nth release that day (US Eastern), e.g. `2026.9.24.1`. The iOS app built from it has the same numbers: version `2026.9.24`, build `1`, which is what TestFlight shows. Releases before 2026-09-24 were numbered `r<N>`.

Every push to `main` runs `.github/workflows/release.yml`:

1. **CI** (`ci.yml`): lint and tests for the backend (including migration up/down and the migration guard), the app (and its build), and `infra/`.
2. **Images**, alongside CI: `api` and `worker` built from `docker/Dockerfile` and pushed to ECR as `commit-<sha>` (the worker with zstd layers, which Fargate starts faster). A re-run reuses images already pushed for its commit.
3. **Migrate**: a Neon snapshot of `main` named `pre-<release>` (the restore point, kept 14 days), `alembic upgrade head` on `main`, then checks that `main` is at this release's migrations and matches the models.
4. **Deploy** (`.github/actions/deploy`): `cdk deploy` of both stacks (stage `prod`) with the images' digests, through the `castle-chef-github-deploy` role (GitHub OIDC; no AWS keys stored), then a smoke test: `GET /health` must answer and `GET /v1/me` without a token must return 401.
5. **Publish**: the GitHub release for the commit, with `release.json` (commit, image digests, migration), which `rollback.yml` uses.
6. **iOS**, only if `apps/mobile/` changed since the last upload (or when run manually with `force_ios`): build and upload on macOS, then wait for Apple's processing on Linux (cheaper). Tag `ios-<release>` marks a processed upload; without it, the next release builds again.

If CI, the images, the migration, or its checks fail, nothing is deployed: AWS keeps running the previous release, which works with the migrated database (backward-compatible migrations), and GitHub emails you. A failed migration leaves `main` unchanged (it runs in one transaction). A failed `cdk deploy` is rolled back by CloudFormation. If the smoke test fails, the new code is deployed but no release is published: fix forward, or go back with `rollback.yml`.

Re-run a failed release from the Actions tab, or release the current `main` with `gh workflow run release.yml` (add `-f force_ios=true` to upload the app again). A run never deploys a commit older than the latest release, and only runs from `main`.

Repository secrets: `NEON_API_KEY`, `MIGRATION_DATABASE_URL` (direct owner connection to `main`), plus the [TestFlight](#testflight) secrets, all copied from SSM ([Secrets](#secrets)). AWS access needs no secret.

### Rollback

**Rollback** (`rollback.yml`, run from the Actions tab or `gh workflow run rollback.yml -f release=<release> -f confirm="roll back to <release>"`) redeploys an earlier release's images with that release's own CDK code, without rebuilding, and smoke-tests it. Only releases with `release.json` (from `r6` on, the first deployed to AWS) can be rolled back to. Don't push while it runs (a new release run would cancel it while it waits).

| What went wrong | What to do |
| --- | --- |
| The release failed | Nothing to undo. Fix and push. |
| The release is bad, the data is fine | Run **Rollback** with the previous release. The database keeps the newer schema, which the older code works with; the workflow refuses if a contract migration since then removed something it needs. Then `git revert` the bad commits and push (the next push releases `main` again). |
| Data was damaged | Run **Rollback** with the last good release, `restore_database` checked, and the confirmation `roll back to <release> and restore the database`. It stops the Pipe (no new jobs start; requests wait in the queue), waits for running workers, snapshots `main` as it is (`before-rollback-<release>-…`), restores `main` from the snapshot taken before the next release, deploys the release, and starts the Pipe again. Everything written since that snapshot is lost, including sign-ins (Neon Auth lives in the same database), so everyone signs in again. Restore points expire after 14 days. Then follow its summary: `git revert` the bad commits and their migrations and push; if `main`'s branch id changed, update `CASTLE_CHEF_NEON_BRANCH_ID` where you run `family` commands; delete the pre-restore branch Neon keeps when done, and reset `dev` if it was a child of it. |
| A bad iOS build | Expire it in App Store Connect; testers can reinstall the previous build from TestFlight → Previous Builds. Then fix forward. |

A code rollback was rehearsed on 2026-09-24 (r8 to r7). The database restore has not been exercised (Neon doesn't allow a test root branch in this project); it also assumes the restored branch keeps `main`'s endpoints, so check sign-in and a request afterwards.

## Secrets

**AWS SSM Parameter Store is the source of truth for every secret** (profile `conn`, region `us-east-1`, `SecureString`). GitHub secrets and `.env` files are copies generated from it. The agent manages all of them: creating, storing, copying, rotating, and revoking. Add or rotate a secret in SSM first, then update its copies and this table.

| SSM parameter | Used by | Status |
| --- | --- | --- |
| `/castle-chef/production/database-url` (direct) | GitHub `MIGRATION_DATABASE_URL`; `family` commands for production ([Family members](#family-members-invite-only)) | set |
| `/castle-chef/production/database-url-pooled` | the `CastleChef` stack: API and failure handler (read at startup), worker (injected by ECS) | set |
| `/castle-chef/development/database-url` (direct) | your checkout's `backend/.env` `DATABASE_URL` | set |
| `/castle-chef/development/database-url-pooled` | the `CastleChef-dev` stack, when deployed | set |
| `/castle-chef/shared/neon-api-key` (Neon key `castle-chef-release`, limited to this project) | GitHub `NEON_API_KEY` | set |
| `/castle-chef/shared/github-app-private-key` (Castle Chef Bot GitHub App) | GitHub `CASTLE_CHEF_APP_PRIVATE_KEY` | set |
| `/castle-chef/shared/github-app-client-id` (not secret) | GitHub `CASTLE_CHEF_APP_CLIENT_ID` | set |
| `/castle-chef/shared/app-store-connect-key-id` (`EXAMP12345`) | GitHub `APP_STORE_CONNECT_KEY_ID` | set |
| `/castle-chef/shared/app-store-connect-issuer-id` | GitHub `APP_STORE_CONNECT_ISSUER_ID` | set |
| `/castle-chef/shared/app-store-connect-api-key-base64` (the `.p8`, base64) | GitHub `APP_STORE_CONNECT_API_KEY_BASE64` | set |
| `/castle-chef/shared/match-password` (encrypts `conn-castle-apple-certs`) | GitHub `MATCH_PASSWORD` | set |

The one credential not in SSM is the worker agent's subscription login (Grok), on the stack's EFS: the agent refreshes it itself, so a copy would go stale. `castle-chef agent-login` writes it ([AWS](#aws)). AWS access from GitHub uses OIDC, so there are no AWS keys.

**Castle Chef Bot** is this repository's own GitHub App (one app per repository): private, read-only access to repository contents, installed on `castle-chef` and `conn-castle-apple-certs`, settings at https://github.com/settings/apps. Nothing stores a GitHub token: the release workflow mints a one-hour token from its key that can read only `conn-castle-apple-certs`, for fastlane match.

The release builds iOS only when all six TestFlight secrets are in GitHub (they are).

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

Development uses the Neon branch `dev` (`example-development-branch`), a copy of `main` with its own Neon Auth (users and keys copied when the branch was made, then independent). Test in the browser with [local development](#local-development); iOS-only behavior (native sign-in in `src/neonAuth.ts`, the persisted cache across relaunches) still needs a TestFlight build.

`make env` writes `backend/.env` for `dev`: `DATABASE_URL` from SSM `/castle-chef/development/database-url` ([Secrets](#secrets)), the Neon project and `dev` branch ids (`Makefile`), `CASTLE_CHEF_NEON_AUTH_URL` (from `apps/mobile/.env.development`), and `CASTLE_CHEF_JOB_DISPATCH=local`. The database and the Neon ids must all point at `dev`, or `family invite` creates the account on one branch and the membership on the other.

`make dev-reset` (it asks first) replaces `dev` with a copy of production as it is now, then applies this checkout's migrations. It discards everything on `dev`, including its Neon Auth state (users and sessions become production's, so sign in again). Connection details stay the same, so `backend/.env` still works. The copy is the family's real data: keep it on this machine.

## Neon

Project `castle-chef` (`example-project`), database `neondb`, branches `main` (production) and `dev` ([Development](#development)), each with its own Neon Auth.

Security model:

- The app signs in with Neon Auth using an emailed one-time code (no passwords, no social providers) and gets a 15-minute JWT (EdDSA). The API verifies it against the branch's published keys (`castle_chef/auth.py`, behind an `AuthVerifier` interface so the provider can be swapped).
- Invite-only: the API serves only accounts listed in `household_member`; anyone else gets 403. Sign-up is disabled in Neon Auth.
- The API enforces the request rules: counts from 1 to 14, at most 3 pending or running per person, and client-generated job ids that can't be reused by someone else or with different parameters.
- The API and worker use the owner role through Neon's pooled endpoint; the agent has no database access. Local tools use the direct endpoint.
- Trusted auth origin: `https://app.example.test`, presented by the iOS app and the dev server's auth proxy. Keep this domain under your control. Localhost is not trusted.
- The app doesn't use the Neon Data API. The `remove Data API access` migration removed its grants, row-level security, policies, and `client_log_batch`, so its roles (`authenticated`, `anonymous`, which Neon keeps while Neon Auth is on) can't read or write any table (`tests/test_data_api_access.py`), and the Data API itself is deleted per branch with `neonctl data-api delete --project-id example-project --branch <branch> --database neondb` (done on `dev` and `main`).

### Family members (invite-only)

From your checkout these act on `dev` (`backend/.env`). For production, run them with `main`'s credentials from SSM (nothing is written to disk):

```sh
cd backend
uv run castle-chef family invite mom@example.com --name "Mom"   # create the account and add it
uv run castle-chef family list                                  # invited emails and names
uv run castle-chef family remove mom@example.com                # remove and delete the account

# Production: prefix each command with `prod`
prod() { DATABASE_URL="$(aws ssm get-parameter --profile conn --region us-east-1 --with-decryption \
  --name /castle-chef/production/database-url --query Parameter.Value --output text)" \
  CASTLE_CHEF_NEON_BRANCH_ID=br-example-00000000 "$@"; }
prod uv run castle-chef family list
```

`invite` and `remove` need `CASTLE_CHEF_NEON_PROJECT_ID` and `CASTLE_CHEF_NEON_BRANCH_ID` and a logged-in `neonctl`. Sign-in is passwordless: the person enters their email in the app, then the 6-digit code Neon Auth emails them (from `auth@mail.myneon.app`; check spam).

## Status and next steps

Production runs on AWS (since release `r6`; now date-versioned, e.g. `2026.9.24.1`), with alarms emailing the owner, and the iOS app on TestFlight (verified on a phone: sign-in, relaunch, requests, sending logs). A code rollback was rehearsed. Next:

1. Product: give the agent meal history so it avoids repeats, and iterate on the app.
2. Invite the family ([Family members](#family-members-invite-only)), as App Store Connect users (internal testers) or external testers (Apple reviews the first build).
