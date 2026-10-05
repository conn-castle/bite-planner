"""CDK app: released by .github/actions/deploy; by hand, docs/operations/aws.md "Deploying by hand".

Context:
  stage              dev | prod (cdk.json `stages`); default prod
  release            the release version deployed (YYYY.M.D.N; e.g. dev-<sha> by hand)
  apiImageDigest     sha256:… of the api image in ECR (runtime stack)
  workerImageDigest  sha256:… of the worker image in ECR (runtime stack)
"""

import aws_cdk as cdk

from castle_chef_infra.repositories import RepositoriesStack
from castle_chef_infra.runtime import RuntimeConfig, RuntimeStack

app = cdk.App()
environment = cdk.Environment(
    account=app.node.get_context("account"), region=app.node.get_context("region")
)
stage_name = app.node.try_get_context("stage") or "prod"
stage = app.node.get_context("stages")[stage_name]


def image_digest(name: str) -> str:
    value = app.node.try_get_context(name)
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise SystemExit(f"Pass -c {name}=sha256:… (the image digest in ECR).")
    return value


repositories = RepositoriesStack(
    app,
    "CastleChefRepositories",
    env=environment,
    github_subject=app.node.get_context("githubSubject"),
    budget_email=app.node.get_context("alarmEmail"),
)
if app.node.try_get_context("repositoriesOnly") != "true":
    runtime = RuntimeStack(
        app,
        # One stack per stage; prod keeps the plain name.
        "CastleChef" if stage_name == "prod" else f"CastleChef-{stage_name}",
        env=environment,
        config=RuntimeConfig(
            neon_auth_url=stage["neonAuthUrl"],
            database_url_parameter=stage["databaseUrlParameter"],
            instacart_booking_parameter=stage["instacartBookingParameter"],
            cors_origins=stage["corsOrigins"],
            alarm_email=app.node.get_context("alarmEmail"),
            release=app.node.get_context("release"),
            api_image_digest=image_digest("apiImageDigest"),
            worker_image_digest=image_digest("workerImageDigest"),
        ),
    )
    cdk.Tags.of(runtime).add("project", "castle-chef")
    cdk.Tags.of(runtime).add("stage", stage_name)
cdk.Tags.of(repositories).add("project", "castle-chef")

app.synth()
