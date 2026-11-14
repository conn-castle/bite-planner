"""CDK app: released by .github/actions/deploy.

Manual deployment: docs/operations/aws.md "Provisioning sequence".

Context:
  stage              dev | prod (cdk.json `stages`); default prod
  release            the release version deployed (YYYY.M.D.N; e.g. dev-<sha> by hand)
  apiImageDigest     sha256:… of the api image in ECR (runtime stack)
  workerImageDigest  sha256:… of the worker image in ECR (runtime stack)
"""

import os

import aws_cdk as cdk

from bite_planner_infra.repositories import RepositoriesStack
from bite_planner_infra.runtime import RuntimeConfig, RuntimeStack

app = cdk.App()


def required_env(name: str) -> str:
    value: str = os.environ.get(name, "")
    if not value:
        raise SystemExit(
            f"Missing {name}; generate and export .env.deployment.local with make env."
        )
    return value


environment = cdk.Environment(
    account=required_env("AWS_ACCOUNT_ID"), region=required_env("AWS_REGION")
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
    "BitePlannerRepositories",
    env=environment,
    github_subject=required_env("BITE_PLANNER_GITHUB_SUBJECT"),
    budget_email=required_env("BITE_PLANNER_ALARM_EMAIL"),
)
if app.node.try_get_context("repositoriesOnly") != "true":
    runtime = RuntimeStack(
        app,
        # One stack per stage; prod keeps the plain name.
        "BitePlanner" if stage_name == "prod" else f"BitePlanner-{stage_name}",
        env=environment,
        config=RuntimeConfig(
            neon_auth_url=required_env(
                "NEON_AUTH_URL_PROD" if stage_name == "prod" else "NEON_AUTH_URL_DEV"
            ),
            database_url_parameter=stage["databaseUrlParameter"],
            instacart_booking_parameter=stage["instacartBookingParameter"],
            cors_origins=stage["corsOrigins"],
            alarm_email=required_env("BITE_PLANNER_ALARM_EMAIL"),
            release=app.node.get_context("release"),
            api_image_digest=image_digest("apiImageDigest"),
            worker_image_digest=image_digest("workerImageDigest"),
        ),
    )
    cdk.Tags.of(runtime).add("project", "bite-planner")
    cdk.Tags.of(runtime).add("stage", stage_name)
cdk.Tags.of(repositories).add("project", "bite-planner")

app.synth()
