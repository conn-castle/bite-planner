# Deployment and operations

This guide describes the supported deployment shape and configuration contract. It is not a shared hosted environment: operators provision their own accounts and private settings. Start with [Contributing](../../CONTRIBUTING.md) for checks and development; see [Architecture](../architecture.md) for the component boundaries.

## Prerequisites

- An AWS account/region and an authenticated CLI profile with deployment access.
- CDK bootstrap resources in that account/region, and GitHub's OIDC provider at `token.actions.githubusercontent.com`.
- A Neon project with production and development branches, Neon Auth enabled, and separate direct/pooled database URLs. Use an operator-controlled trusted auth origin.
- An OpenAI project API key, the Codex CLI/ChatGPT sign-in with Instacart connected, and an Instacart website session for checkout.
- For native distribution, an Apple developer team, the `com.conncastle.biteplanner` App ID and App Store Connect record, APNs credentials for that app, and a signing repository/profile.

The source uses `BitePlannerRepositories` and `BitePlanner` as the production CDK stack names, `bite-planner-api` / `bite-planner-worker` as ECR names, and `bite-planner-github-deploy` as the GitHub role. A development runtime uses `BitePlanner-dev`. These are deployment identities, not just displayed labels. Creating them does not move an existing service's database, login state, or signing records.

## Configuration

**AWS SSM Parameter Store is canonical.** Store private settings and credentials as `SecureString` before use. Local environment files and GitHub encrypted secrets are generated copies. Do not commit generated settings or paste values into issues, command arguments, or logs.

Paths use `/bite-planner/`. The following inventory lists the configuration contract, not a claim that any particular account has been provisioned:

| Parameter suffix | Purpose |
| --- | --- |
| `shared/deployment-account-id`, `shared/deployment-region` | Target AWS deployment |
| `shared/github-oidc-subject` | Immutable GitHub repository subject prefix; the role adds `:ref:refs/heads/main` |
| `shared/alarm-email` | Operator notification address |
| `shared/neon-project-id`, `development/neon-branch-id` | Neon administration and development reset |
| `development/database-url`, `production/database-url` | Direct database connections for development, migrations, and administration |
| `development/database-url-pooled`, `production/database-url-pooled` | Runtime connections |
| `development/neon-auth-url`, `production/neon-auth-url` | Branch auth endpoints |
| `shared/neon-auth-trusted-origin` | Origin supplied by native auth and the development proxy |
| `production/api-url` | Deployed production API endpoint; create after the runtime exists |
| `shared/neon-api-key` | Project-scoped Neon administration |
| `shared/openai-api-key` | Worker inference |
| `shared/apple-team-id` | Signing and APNs token issuer |
| `shared/apns-key-base64`, `shared/apns-key-id` | APNs key material and ID, valid for this App ID |
| `shared/apple-signing-git-url`, `shared/match-password` | Private match signing repository and encryption |
| `shared/app-store-connect-key-id`, `shared/app-store-connect-issuer-id`, `shared/app-store-connect-api-key-base64` | App Store Connect upload credentials |
| `shared/github-app-client-id`, `shared/github-app-private-key` | Project GitHub App used to read the signing repository |
| `shared/instacart-session` | Checkout browser session, renewed by the tools |
| `development/instacart-booking`, `production/instacart-booking` | Nonsecret mode: `off`, `dry_run`, or `live` |

The refreshable Codex/ChatGPT sign-in is the exception: it stays in the worker's protected login directory on EFS, not in a stale SSM copy. The Instacart website session is a separate credential stored in SSM. Neither belongs in job records.

For interactive AWS access, select your profile and use a device code:

```sh
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"  # the region Boto3 reads
aws sso login --profile "$AWS_PROFILE" --use-device-code
```

Write a parameter without putting its value in shell history or an argument:

```sh
read -rs private_parameter_value
printf %s "$private_parameter_value" | aws ssm put-parameter \
  --name /bite-planner/shared/openai-api-key --type SecureString \
  --value file:///dev/stdin
unset private_parameter_value
```

Add `--overwrite` only for an intentional update. For a key file, pipe its contents or required base64 encoding directly to SSM. Rotate in SSM first, regenerate every consumer, and verify the affected connection.

### Generate consumers

After the development/deployment metadata exists:

```sh
make env
```

This writes ignored mode-600 `.env.deployment.local`, `backend/.env`, and `apps/mobile/.env.development.local`. It does not require a production API URL or write production client settings. The deployment file exports the canonical region as both `AWS_REGION` (AWS CLI, CDK) and `AWS_DEFAULT_REGION` (Boto3). Source `.env.deployment.local` before invoking CDK or fastlane directly; Make includes and exports it automatically.

After the production API exists, set `production/api-url` in SSM and generate its consumers:

```sh
uv run --project backend python -m bite_planner.configure production
uv run --project backend python -m bite_planner.configure github \
  --repo your-owner/your-repository
```

The production command writes `apps/mobile/.env.production.local`. The GitHub command copies the canonical release/signing configuration through stdin to encrypted repository secrets. It requires repository administration access and prints no values. API/auth URLs still appear in the built client and network requests.

## Provisioning sequence

1. Configure Neon/Auth, the Apple identity and required credentials, and the SSM inventory. Leave live booking disabled until purchase behavior has been deliberately verified. `production/api-url` is populated after the API exists.
2. Configure GitHub's immutable OIDC subject for the repository. Inspect `gh api repos/OWNER/REPO/actions/oidc/customization/sub`, verify immutable identity is enabled, and store its returned `sub_claim_prefix` in SSM. The trust policy permits only that repository's `main` branch and the STS audience.
3. Create GitHub's AWS OIDC provider and CDK bootstrap resources. Generate/source the deployment environment, then bootstrap outside `infra/` so the runtime app is not synthesized before images exist:

   ```sh
   source .env.deployment.local
   npx --yes aws-cdk@2.1143.0 bootstrap "aws://$AWS_ACCOUNT_ID/$AWS_REGION"
   ```

4. In `infra/`, install locked dependencies and deploy the repositories/role/budget stack:

   ```sh
   uv sync --locked
   npx --yes aws-cdk@2.1143.0 deploy --all -c repositoriesOnly=true
   ```

5. From the repository root, apply Alembic migrations to the production branch using its direct URL, without writing production credentials into the development environment:

   ```sh
   DATABASE_URL="$(aws ssm get-parameter --with-decryption \
     --name /bite-planner/production/database-url --query Parameter.Value --output text)" \
     uv run --directory backend alembic upgrade head
   ```

   Build the `api` and `worker` targets from [`docker/Dockerfile`](../../docker/Dockerfile), push them to the configured ECR repositories, and obtain their image digests. Use the `images` job in [release.yml](../../.github/workflows/release.yml) as the canonical build/push reference. Run the following CDK command from `infra/`. The runtime requires digests, not mutable tags:

   ```sh
   npx --yes aws-cdk@2.1143.0 deploy --all \
     -c stage=prod -c release=bootstrap \
     -c apiImageDigest=sha256:YOUR_API_DIGEST \
     -c workerImageDigest=sha256:YOUR_WORKER_DIGEST \
     --outputs-file cdk-outputs.json
   ```

6. Store the API output as `production/api-url` in SSM, configure invite-only sign-up, create/invite the first household, and connect the worker to Instacart. Generate production/GitHub consumers before enabling release workflows. Keep output files private; they contain deployment identifiers.
7. Verify authentication, household isolation, quote approval, reminders, and a controlled booking mode. Native signing additionally needs a valid profile with push entitlements for the configured bundle ID.

These steps provision resources; `make check` and a successful synthesis do not establish that a live deployment is ready. When migrating an existing instance, retain its data and credentials until the new instance and rollback route have been verified. Do not run two live booking schedulers against the same production orders during a cutover.

## Runtime and connections

The runtime stack creates the Lambda API/failure/reminder handlers, SQS queues and EventBridge Pipe, one-shot Fargate workers, EFS login storage, S3 job records, schedules and alarms. There are no continuously running application servers, but booking schedules and member reminders run while the app is closed.

The hourly booking schedule retries due approved orders from 09:00 through 21:00 in the configured Eastern schedule. Member reminder schedules follow their time zone and settings. Booking modes are explicit: `off` refuses a live purchase; `dry_run` exercises checkout without buying; `live` can place an approved order. Never infer the current mode from repository documentation—read the configured parameter.

Local worker commands use the same image layout as Fargate:

```sh
make worker-image
make worker-login
make instacart-login
make worker-job JOB=the-job-id
```

### Connect the deployed worker

Local `make worker-login` initializes only the local Docker volume. The deployed worker has a separate EFS login directory. After the runtime stack exists, run an authentication task and follow its private logs to complete the browser sign-in and connect Instacart:

```sh
out() { aws cloudformation describe-stacks --stack-name BitePlanner \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
aws ecs run-task --launch-type FARGATE \
  --cluster "$(out ClusterNameOutput)" --task-definition "$(out WorkerTaskDefinitionOutput)" \
  --network-configuration "awsvpcConfiguration={subnets=[$(out WorkerSubnetsOutput)],securityGroups=[$(out WorkerSecurityGroupOutput)],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"worker","command":["agent-login","codex"]}]}'
aws logs tail "$(out WorkerLogGroupOutput)" --follow
```

The commands use the selected `AWS_PROFILE` and `AWS_REGION`. Keep the authentication code and logs private. Re-run this task to reconnect an expired connection.

The Codex login is mounted at `/mnt/agent-auth/codex` in the worker. AWS login on the host and Codex/ChatGPT sign-in are distinct. A new deployment needs its own working login state; copying a source repository does not provision external-service access.

## Households and sign-up

Neon Auth verifies emailed codes. The API's signed `user.before_create` webhook permits invited emails; disabling password signup alone is not the complete invitation gate. The deployment's invite-only action configures and checks the webhook. Verify it after deployment or a database restore.

With the configured development database:

```sh
uv run --project backend bite-planner household create "Example Household"
uv run --project backend bite-planner household invite person@example.com \
  --name "Example Member" --household "Example Household"
uv run --project backend bite-planner household list
uv run --project backend bite-planner household members "Example Household"
```

Members can manage invitations and membership in Settings. The API keeps an account's identity when membership ends, so past attribution and re-invitation continue working. Production administration uses its direct SSM database URL supplied only to that process; never overwrite development `.env` with production credentials.

## Recover an unconfirmed receipt

For an order still showing **Check Instacart**, an administrator can restore its
complete original booking receipt without making another purchase. First inspect
the original job record and worker logs (Diagnostics below): verify Instacart's
confirmation, order number, delivery date/window and **authorized amount**. Its
later estimated Total can differ and is not the authorization. Check that the
worker made no `cart.swapped` changes; those require shopping-list reconciliation
and are outside this command. Do not use the app's “It was placed” action first:
that resolves only the status, without a receipt.

Keep the verified receipt in a private JSON file outside tracked source:

```json
{
  "attempt_job_id": "00000000-0000-4000-8000-000000000001",
  "delivery_date": "2026-10-12",
  "order_number": "EXAMPLE-1234567890",
  "window": {"start": "14:00:00", "end": "16:00:00"},
  "authorized_total_cents": 10000
}
```

Use the configured target database (production's URL supplied directly from SSM
to this process, as for other administration). Validate before applying:

```sh
uv run --project backend bite-planner recover-booking ORDER_UUID \
  --household "Example Household" --receipt path/to/private-receipt.json --check
uv run --project backend bite-planner recover-booking ORDER_UUID \
  --household "Example Household" --receipt path/to/private-receipt.json
```

The command checks the exact booking attempt and its recorded purchase authority,
refuses mismatched dates/windows/amounts and already-associated order numbers, and
restores receipt details with confirmation and booking audit entries in one
transaction. The administrator attests the receipt's external evidence; the
command does not contact Instacart to verify a submitted order number. An exact
repeat changes nothing; a different or incomplete receipt on an already booked
order is refused. Administrative recoveries should be run one at a time. The
command retains purchase authority, jobs, meal picks, list items and dates. It
sends no notification and neither changes the cart nor retries checkout. Verify
the receipt in the app and the two new audit entries after applying it.

## Releases and recovery

The release workflow is restricted to `main`. When enabled, a qualifying push runs CI, builds/pushes images, snapshots and migrates Neon, deploys/smoke-tests the service, and publishes `release.json`. An iOS change also signs/uploads through fastlane and records the processed upload with an `ios-` tag. Date versions use `YYYY.M.D.N`; the app version and build number derive from that release.

The signing App needs read access only to the signing repository. Certificate/profile renewal is an operator action with write access, not a reason to expand the release token. App Store Connect's record, App ID, APNs scope, bundle ID and match profile must agree before the first native release.

A failed migration/deploy is different from a failed smoke test: CloudFormation can roll back a deployment, but a failed post-deploy smoke test can leave new code running without a published release. Investigate actual stack/API state before retrying or rolling back.

Rollback redeploys the selected release's images and CDK code from its `release.json`. A database restore discards later writes, including auth state, and needs an explicit confirmation. Contract migrations can make a code-only rollback unsafe. Restore points expire, so verify the chosen release and snapshot exist before acting. A bad iOS build is expired in App Store Connect; testers can reinstall a previous available build.

Do not assume pre-migration tags or copied repository metadata provide a valid rollback for renamed resources. Establish a known-good release and tested recovery route for the new deployment before retiring its predecessor. Keep Release/Rollback disabled during an incomplete identity migration.

## Diagnostics

CloudWatch receives structured API, worker, failure/reminder and uploaded client logs. Error/critical lines trigger configured alarms. Job directories are protected S3 archives for the configured retention (90 days in the runtime source). They contain job prompts/transcripts, not a public debugging feed.

Services report actual job failures at error level. The app records an already-failed job being displayed as an informational `orders.*_failure_shown` event, including the order, job, and error. Reopening a failed screen does not create another failure alarm; client defects such as crashes remain error-level events.

Find output names in the deployed stack, then query the relevant log group:

```sh
aws logs tail YOUR_LOG_GROUP --follow
```

Filter by event, request/job/order ID and UTC time. Distinguish an approval, an attempted purchase and a confirmed grocery order; no one log entry establishes all three. Local logs and worker artifacts live under `~/.local/state/bite-planner/`. Keep raw private records out of public GitHub issues and workflow artifacts.

For an APNs test, include all required startup mappings:

```sh
BITE_PLANNER_PUSH=apns \
BITE_PLANNER_SSM_PARAMETERS='{"DATABASE_URL":"/bite-planner/production/database-url-pooled","BITE_PLANNER_APNS_KEY":"/bite-planner/shared/apns-key-base64","BITE_PLANNER_APNS_KEY_ID":"/bite-planner/shared/apns-key-id","BITE_PLANNER_APNS_TEAM_ID":"/bite-planner/shared/apple-team-id"}' \
uv run --project backend bite-planner household notify-test person@example.com
```

This sends a real notification to that member's registered devices. Treat it as an operator action, not part of contributor checks.
