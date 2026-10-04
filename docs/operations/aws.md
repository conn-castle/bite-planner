# AWS operations

Everything Castle Chef runs in AWS, how to sign in to it, and where every secret
lives. Account `123456789012`, region `us-east-1`, profile `conn`.

**AWS SSM Parameter Store is the source of truth for every secret.** Nothing is
kept only in GitHub, a `.env` file, a keychain, or someone's notes: those are
copies made from SSM ([Secrets](#secrets)).

## Signing in

**Local sign-in always uses a device code.** The `conn` profile is AWS IAM
Identity Center (SSO), and its sign-in expires after some hours. When a command
says the token has expired, sign in again with:

```sh
aws sso login --profile conn --use-device-code
```

It prints a link and a code: open the link in any browser signed in to the AWS
account (a phone works), check that the code matches, and approve. Never use
`aws sso login` without `--use-device-code`: that opens a browser on this
machine, which isn't where the person signing in is.

Local work that needs AWS: `make env` (reads SSM), `make review` and
`make worker-job` (the worker reads the Instacart session from SSM with
short-lived credentials from this sign-in), `make instacart-login`, deploying
by hand, and reading logs. AWS services and GitHub never use this sign-in: they
have their own roles (the task role, the Lambdas' roles, and GitHub's OIDC role
`castle-chef-github-deploy`).

## Stack and services

Account `123456789012` (profile `conn`), region `us-east-1`, defined in `infra/` (CDK, Python). Releases deploy it ([Releases](../../README.md#releases)); nothing needs a manual deploy. Stacks:

- `CastleChefRepositories`: ECR repositories `castle-chef-api` and `castle-chef-worker` (immutable tags; the last 30 released images `commit-<sha>` and 5 development pushes `dev-…` are kept), and the role `castle-chef-github-deploy`, which only this repository's `main` branch can assume (GitHub OIDC).
- `CastleChef` (stage `prod`, Neon `main`): a VPC with public subnets only (no NAT), the API, failure-handler, and Reminders Lambdas (Reminders also runs the daily booking), the jobs queue and its dead-letter queue, the Pipe, the ECS cluster and worker task definition, EFS for the agent logins (Grok's and Codex's), the reminder schedule group and the role its schedules run as, log groups, the job records bucket, and alarms. Its outputs name the API URL, cluster, task definition, subnets, security group, log group, job records bucket, Pipe, and Reminders function. `CastleChef-dev` (stage `dev`, Neon `dev`) is the same stack for trying infrastructure changes; it is not deployed now.

Costs: nothing runs while idle; EFS, ECR, and alarms are well under $1 a month, a job is a fraction of a cent of Fargate, and Grok is the subscription. Lambda and the Pipe scale to zero.

Secrets never sit in configuration: the Lambdas read `DATABASE_URL` (and Reminders the APNs key) from SSM at startup (`CASTLE_CHEF_SSM_PARAMETERS`), and ECS injects them into the worker from SSM. Both use Neon's pooled endpoint.

**Notifications** (push, iPhone only) go straight to Apple's push service with the APNs key (`backend/src/castle_chef/push.py`); the phones' tokens are in `push_device`, registered by the app for whoever is signed in. "Your meal ideas are ready" is sent by the worker when a batch succeeds, to whoever started it. The daily reminders (`reminders.py`): each member with a known time zone has an EventBridge Scheduler schedule `reminder-<user id>` in the group `CastleChef-reminders`, at their notification time in their zone, which invokes the `CastleChef-Reminders` Lambda; it checks the database (the member's "Daily reminders" switch is on, the next order isn't placed, reminders have started, none sent today) and sends. The API creates and updates a member's schedule when their time or zone changes; every deploy rebuilds them all from the database (the deploy's "Rebuild the reminder schedules" step), and a firing that doesn't match the database rebuilds that one. Scheduler skips a time in the hour lost when clocks go forward (that day has no reminder at it) and runs a repeated time once. To check push end to end for someone: `castle-chef household notify-test <email>` with `CASTLE_CHEF_PUSH=apns` and `CASTLE_CHEF_SSM_PARAMETERS='{"DATABASE_URL": "/castle-chef/production/database-url-pooled", "CASTLE_CHEF_APNS_KEY": "/castle-chef/shared/apns-key-base64", "CASTLE_CHEF_APNS_KEY_ID": "/castle-chef/shared/apns-key-id"}'` (AWS profile `conn`); each phone's result is in the log.

**Booking** (`backend/src/castle_chef/booking.py`): an approved order books at Instacart four days before its delivery, at 9:00 AM Eastern, when the schedule `BookingSchedule` (the default group, not the members') invokes the Reminders Lambda with `{"book_due": {}}`; it starts a `book` job for each order that's due and queues it, and settles any booking whose worker was lost. An order approved after its day books right away (the API queues it). Whether the worker presses Place order is the stage's switch, the SSM String `/castle-chef/production/instacart-booking` (`/castle-chef/development/instacart-booking` for `CastleChef-dev`): `off` runs every step up to Place order and marks the order "Not booked: booking at Instacart isn't turned on yet"; `live` orders; `dry_run` (local development's default) stops just before and marks it booked, "Dry run". Each worker task reads it as it starts, so a change applies to the next booking without a deploy. Production is `live` (turned on 2026-10-04, at the owner's word; it was `off` from 2026-10-01). To stop real orders, set it back to `off`:

```sh
aws ssm put-parameter --profile conn --region us-east-1 --overwrite \
  --name /castle-chef/production/instacart-booking --type String --value off
```

To run the daily booking now (e.g. after a fix): `castle-chef job book-due` against the database in question.

**Sign-up** (invite-only): on `main`, Neon Auth calls the API before it creates any account (its blocking `user.before_create` webhook, signed with Neon Auth's own keys), and the API allows only an email someone has invited (`POST /v1/auth/neon-webhook`; `backend/src/castle_chef/household.py`). It fails closed: while the API is down, nobody new can sign up (members already in sign in as usual). The webhook is Neon Auth configuration on the branch; every deploy sets it on `main` with the API's URL (10 seconds per attempt, so a cold API answers) and checks it is exactly that (`.github/actions/invite-only`), and so does every rollback, with the current `main`'s action whatever release it goes back to, so a restored `main` gets it again. To see it: `neonctl api /projects/example-project/branches/<main's id>/auth/webhooks`. `dev` has none: its API is a laptop, which Neon can't reach. To set it by hand:

```sh
api=$(aws cloudformation describe-stacks --profile conn --region us-east-1 --stack-name CastleChef \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrlOutput'].OutputValue" --output text)
neonctl api "/projects/example-project/branches/$(neonctl branches get main \
  --project-id example-project -o json | jq -r .id)/auth/webhooks" -X PUT \
  -d "{\"enabled\": true, \"webhook_url\": \"${api%/}/v1/auth/neon-webhook\", \"enabled_events\": [\"user.before_create\"], \"timeout_seconds\": 10}"
```

**Alarms** email `operator@example.com` (SNS topic "Castle Chef alarms"; the subscription must be confirmed from the first email): any error-level log line, Lambda errors, Pipe failures, and reminder schedules that couldn't invoke Reminders. A request that fails because of the agent (bad output, timeout, an expired login) also logs at error level, so it emails too.

**Failures** mark jobs failed without polling: a message the Pipe can't start a task for goes to the dead-letter queue after 3 tries, and a worker task that stops with a non-zero exit (crash, out of memory, image pull or EFS failure, the 40-minute cap) sends an ECS event; the failure handler fails its job either way, and the shopping its task went on to. As a backstop, the API fails a person's jobs that stayed pending for 10 minutes or running for 20.

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

The task ends when you approve (`agent.login_finished`) or the code expires. The login is shared by every job; this is one of the two credentials not in SSM, because the agent rewrites it as it refreshes.

**Instacart connection.** The same way, with the command `agent-login codex`: Codex signs in to the household's ChatGPT account with a device code (skipped if it's signed in), then installs the Instacart app and prints its connect link. Open the link signed in to **the same** ChatGPT account and connect the household's Instacart account; the task waits until Instacart answers (`instacart.connected`, "Instacart is connected: …"), up to 15 minutes for both steps together. The login is on EFS next to Grok's (`/codex`), and Codex refreshes it itself.

**Deploying by hand** (not needed normally; e.g. the first deploy of a new account or of `CastleChef-dev`), with AWS credentials from the `conn` profile:

```sh
cd infra && uv sync
eval "$(aws configure export-credentials --profile conn --format env)"
npx aws-cdk@2.1143.0 deploy CastleChefRepositories -c repositoriesOnly=true
# Push images first (tags dev-<sha>-<time>), then:
npx aws-cdk@2.1143.0 deploy CastleChef-dev -c stage=dev -c apiImageDigest=sha256:… -c workerImageDigest=sha256:…
```

A new stack has its own EFS, so run both logins for it once. `cd infra && uv run pytest` checks what the stacks must guarantee (CI runs it).

## Finding a problem

When something goes wrong in the app (the owner reports it, with roughly when and whose phone), the logs say what happened. In CloudWatch Logs Insights, over the time it happened, on the API's log group (which has the app's own lines too):

```
fields @timestamp, level, event, route, status, detail, user_id, entry.event, entry.message, exception
| filter level in ["warning", "error", "critical"] or status >= 400
| sort @timestamp desc
```

- A member's lines: add `| filter user_id = "<id>"` (`client.batch` lines give the id and email for each phone's uploads). The app's own lines are `client.log`, with what it logged in `entry`.
- A request that failed unexpectedly is `http.failed` (level error), with its route, member, and traceback in `exception`.
- An order or job: `filter grocery_order_id = "<id>" or job_id = "<id>"`, on the worker's log group too (and the failure handler's, for a worker that stopped).
- A job's agent: its record ([Job records](#job-records)) has everything it was given and did.
- A slow first request after a release: Lambda's `INIT_REPORT … Status: timeout` lines (startup over 10 seconds) happen when a new version first starts, during the release's own checks.

## Job records

Each job's record is kept in the S3 bucket `JobRecordsBucketOutput` for 90 days (like the logs) as `jobs/<job-id>.tar.gz`: the job's directory as `job/` (the prompt, the agent's output and errors, what its tools wrote) and Grok's session files as `grok-sessions/` (every tool call and what it returned). The Instacart session never goes in. The worker saves it every minute while the job runs (so a task that's killed still leaves most of it) and once the job ends, however it ended (`job.record_saved`, with the parts it has; `job.record_failed` if it couldn't, at error level once the job has ended, which changes nothing about the job). The worker can only add records; to read one:

```sh
mkdir -p /tmp/job-record && aws s3 cp "s3://$(out JobRecordsBucketOutput)/jobs/<job-id>.tar.gz" - --profile conn | tar xz -C /tmp/job-record
```

(`out` is defined under Agent login.) Locally, a job's files stay on disk (`make worker-job` copies them out).

## Secrets

**AWS SSM Parameter Store is the source of truth for every secret** (profile `conn`, region `us-east-1`, `SecureString`). GitHub secrets and `.env` files are copies generated from it. The agent manages all of them: creating, storing, copying, rotating, and revoking. Add or rotate a secret in SSM first, then update its copies and this table.

| SSM parameter | Used by | Status |
| --- | --- | --- |
| `/castle-chef/production/database-url` (direct) | GitHub `MIGRATION_DATABASE_URL`; `household` commands for production ([Households](../../README.md#households-invite-only)) | set |
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
| `/castle-chef/shared/apns-key-base64` (APNs auth key `EXAMP12345`, `.p8` base64; Production, topic `com.conncastle.castlechef` only) | the Reminders Lambda (read at startup) and the worker (ECS secret) | set |
| `/castle-chef/shared/apns-key-id` (`EXAMP12345`, not secret) | the Reminders Lambda and the worker | set |
| `/castle-chef/shared/instacart-session` (the household's Instacart website session: its instacart.com cookies, gzip then base64) | the worker's browser for checkout (read, and written back as Instacart renews it; the task role may get and put only it), from piece 9 part 3; locally, `make worker-job` passes short-lived `conn` credentials by environment | set |
| `/castle-chef/production/instacart-booking` (String, not secret: `off`, `live`, or `dry_run`) | the `CastleChef` worker (ECS secret): whether booking presses Place order ([Stack and services](#stack-and-services), Booking) | `live` (since 2026-10-04) |
| `/castle-chef/development/instacart-booking` (String, not secret) | the `CastleChef-dev` worker, when deployed | `dry_run` |

The two credentials not in SSM are the worker's subscription logins on the stack's EFS: Grok's, and Codex's (the household's ChatGPT sign-in, with Instacart connected). Each refreshes itself, so a copy would go stale. `castle-chef agent-login grok` and `agent-login codex` write them ([Stack and services](#stack-and-services)). AWS access from GitHub uses OIDC, so there are no AWS keys.

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
