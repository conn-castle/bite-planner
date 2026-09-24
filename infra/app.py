"""CDK app: `uv run cdk deploy` from infra/ (see README "Infrastructure").

Context:
  stage              dev | prod (cdk.json `stages`); default prod
  apiImageDigest     sha256:… of the api image in ECR (runtime stack)
  workerImageDigest  sha256:… of the worker image in ECR (runtime stack)
"""

import aws_cdk as cdk
from aws_cdk import CfnOutput

from castle_chef_infra.repositories import RepositoriesStack
from castle_chef_infra.runtime import RuntimeConfig, RuntimeStack

app = cdk.App()
environment = cdk.Environment(
    account=app.node.get_context("account"), region=app.node.get_context("region")
)
stage_name = app.node.try_get_context("stage") or "prod"
stage = app.node.get_context("stages")[stage_name]


def required(name: str) -> str:
    value = app.node.try_get_context(name)
    if not value or not str(value).startswith("sha256:"):
        raise SystemExit(f"Pass -c {name}=sha256:… (the image digest in ECR).")
    return value


repositories = RepositoriesStack(
    app,
    "CastleChefRepositories",
    env=environment,
    github_subject=app.node.get_context("githubSubject"),
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
            cors_origins=stage["corsOrigins"],
            alarm_email=app.node.get_context("alarmEmail"),
            api_image_digest=required("apiImageDigest"),
            worker_image_digest=required("workerImageDigest"),
        ),
    )
    CfnOutput(runtime, "ApiUrlOutput", value=runtime.api_url)
    CfnOutput(runtime, "ClusterNameOutput", value=runtime.cluster.cluster_name)
    CfnOutput(runtime, "JobsPipeOutput", value=runtime.pipe_name)
    CfnOutput(runtime, "WorkerTaskDefinitionOutput", value=runtime.worker.task_definition_arn)
    CfnOutput(
        runtime, "WorkerSecurityGroupOutput", value=runtime.worker_security_group.security_group_id
    )
    CfnOutput(runtime, "WorkerSubnetsOutput", value=",".join(s.subnet_id for s in runtime.subnets))
    CfnOutput(runtime, "WorkerLogGroupOutput", value=runtime.worker_logs.log_group_name)
    cdk.Tags.of(runtime).add("project", "castle-chef")
    cdk.Tags.of(runtime).add("stage", stage_name)
cdk.Tags.of(repositories).add("project", "castle-chef")

app.synth()
