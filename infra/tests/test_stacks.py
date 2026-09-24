"""What the stacks must guarantee (see castle_chef_infra/runtime.py), checked on
the synthesized CloudFormation."""

import json
import os
import subprocess
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from castle_chef_infra.repositories import RepositoriesStack
from castle_chef_infra.runtime import RuntimeConfig, RuntimeStack

API_DIGEST = "sha256:" + "a" * 64
WORKER_DIGEST = "sha256:" + "b" * 64
OIDC = "token.actions.githubusercontent.com"
MAIN_BRANCH = "repo:owner@1/castle-chef@2:ref:refs/heads/main"


@pytest.fixture(scope="module")
def runtime() -> Template:
    app = cdk.App()
    stack = RuntimeStack(
        app,
        "Runtime",
        config=RuntimeConfig(
            neon_auth_url="https://ep-x.neonauth.example/neondb/auth",
            database_url_parameter="/castle-chef/test/database-url-pooled",
            cors_origins=["capacitor://localhost"],
            alarm_email="owner@example.com",
            api_image_digest=API_DIGEST,
            worker_image_digest=WORKER_DIGEST,
        ),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def repositories() -> Template:
    app = cdk.App()
    stack = RepositoriesStack(
        app,
        "Repositories",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
        github_subject="repo:owner@1/castle-chef@2",
    )
    return Template.from_stack(stack)


def worker_container(runtime: Template) -> dict:
    [task] = runtime.find_resources("AWS::ECS::TaskDefinition").values()
    [container] = task["Properties"]["ContainerDefinitions"]
    return container


def test_nothing_costs_money_while_idle(runtime: Template) -> None:
    runtime.resource_count_is("AWS::EC2::NatGateway", 0)
    runtime.resource_count_is("AWS::ECS::Service", 0)  # workers are one-off tasks


def test_worker_is_locked_down_and_pinned(runtime: Template) -> None:
    container = worker_container(runtime)
    assert container["Name"] == "worker"
    assert container["ReadonlyRootFilesystem"] is True
    assert WORKER_DIGEST in str(container["Image"])
    capabilities = container["LinuxParameters"]["Capabilities"]
    dropped = set(capabilities["Drop"])
    assert {"NET_RAW", "MKNOD", "SYS_CHROOT"} <= dropped
    # Needed to start, and stop, the agent as its own user.
    assert not dropped & {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL"}
    assert "Add" not in capabilities
    assert [s["Name"] for s in container["Secrets"]] == ["DATABASE_URL"]
    assert "DATABASE_URL" not in {e["Name"] for e in container.get("Environment", [])}


def test_worker_login_storage_belongs_to_the_agent_user(runtime: Template) -> None:
    [login_dir] = [
        m["ContainerPath"]
        for m in worker_container(runtime)["MountPoints"]
        if m["SourceVolume"] == "grok-login"
    ]
    environment = {e["Name"]: e["Value"] for e in worker_container(runtime)["Environment"]}
    # The login and the lock that serializes its users both live on EFS.
    assert environment["GROK_AUTH_PATH"].startswith(f"{login_dir}/")
    assert environment["CASTLE_CHEF_AGENT_LOCK_FILE"].startswith(f"{login_dir}/")
    runtime.has_resource_properties("AWS::EFS::FileSystem", {"Encrypted": True})
    runtime.has_resource("AWS::EFS::FileSystem", {"DeletionPolicy": "Retain"})
    runtime.has_resource_properties(
        "AWS::EFS::AccessPoint",
        {
            "PosixUser": {"Uid": "65532", "Gid": "65532"},
            "RootDirectory": {
                "Path": "/grok",
                "CreationInfo": Match.object_like({"Permissions": "700"}),
            },
        },
    )


def test_each_queued_job_starts_one_worker_with_its_id(runtime: Template) -> None:
    runtime.has_resource_properties(
        "AWS::Pipes::Pipe",
        {
            "SourceParameters": {"SqsQueueParameters": {"BatchSize": 1}},
            "TargetParameters": {
                "EcsTaskParameters": Match.object_like(
                    {
                        "LaunchType": "FARGATE",
                        "TaskCount": 1,
                        "NetworkConfiguration": {
                            "AwsvpcConfiguration": Match.object_like({"AssignPublicIp": "ENABLED"})
                        },
                        "Overrides": {
                            "ContainerOverrides": [
                                {
                                    "Name": "worker",
                                    "Environment": [
                                        {"Name": "CASTLE_CHEF_JOB_ID", "Value": "$.body.job_id"}
                                    ],
                                }
                            ]
                        },
                    }
                )
            },
        },
    )
    runtime.has_resource_properties(
        "AWS::SQS::Queue", {"RedrivePolicy": Match.object_like({"maxReceiveCount": 3})}
    )


def functions(runtime: Template) -> dict[str, dict]:
    return {
        name: resource["Properties"]
        for name, resource in runtime.find_resources("AWS::Lambda::Function").items()
        if "ImageUri" in str(resource["Properties"].get("Code", {}))
    }


def test_api_runs_outside_any_vpc_within_limits(runtime: Template) -> None:
    [api] = [
        f
        for f in functions(runtime).values()
        if "CASTLE_CHEF_JOBS_QUEUE_URL" in f["Environment"]["Variables"]
    ]
    assert "VpcConfig" not in api
    assert api["Timeout"] == 30
    assert api["ReservedConcurrentExecutions"] == 5
    variables = api["Environment"]["Variables"]
    assert variables["CASTLE_CHEF_SSM_PARAMETERS"] == (
        '{"DATABASE_URL": "/castle-chef/test/database-url-pooled"}'
    )
    assert "DATABASE_URL" not in variables  # never in the function's configuration
    runtime.has_resource_properties("AWS::Lambda::Url", {"AuthType": "NONE"})


def test_only_the_api_has_a_url(runtime: Template) -> None:
    runtime.resource_count_is("AWS::Lambda::Url", 1)
    [handler] = [
        f
        for f in functions(runtime).values()
        if f["Environment"]["Variables"].get("CASTLE_CHEF_FAILURE_HANDLER") == "true"
    ]
    assert "CASTLE_CHEF_JOBS_QUEUE_URL" not in handler["Environment"]["Variables"]


def test_stopped_workers_reach_the_failure_handler(runtime: Template) -> None:
    runtime.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": Match.object_like(
                {
                    "source": ["aws.ecs"],
                    "detail-type": ["ECS Task State Change"],
                    "detail": Match.object_like({"lastStatus": ["STOPPED"]}),
                }
            )
        },
    )
    [rule] = runtime.find_resources("AWS::Events::Rule").values()
    [target] = rule["Properties"]["Targets"]
    [mapping] = runtime.find_resources("AWS::Lambda::EventSourceMapping").values()
    [dead_letters] = [
        name
        for name, queue in runtime.find_resources("AWS::SQS::Queue").items()
        if "RedrivePolicy" not in queue["Properties"]
    ]
    [handler] = [
        name
        for name, f in runtime.find_resources("AWS::Lambda::Function").items()
        if f["Properties"]["Environment"]["Variables"].get("CASTLE_CHEF_FAILURE_HANDLER")
    ]
    assert target["Arn"] == {"Fn::GetAtt": [handler, "Arn"]}
    assert mapping["Properties"]["FunctionName"] == {"Ref": handler}
    assert mapping["Properties"]["EventSourceArn"] == {"Fn::GetAtt": [dead_letters, "Arn"]}


def test_alarms_email_the_owner(runtime: Template) -> None:
    runtime.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "owner@example.com"}
    )
    runtime.has_resource_properties(
        "AWS::Logs::MetricFilter",
        {"FilterPattern": '{ ($.level = "error") || ($.level = "critical") }'},
    )


def test_deploys_come_only_from_this_repositorys_main_branch(repositories: Template) -> None:
    repositories.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": Match.object_like(
                {
                    "Statement": [
                        Match.object_like(
                            {
                                "Condition": {
                                    "StringEquals": {
                                        f"{OIDC}:aud": "sts.amazonaws.com",
                                        f"{OIDC}:sub": MAIN_BRANCH,
                                    }
                                }
                            }
                        )
                    ]
                }
            )
        },
    )
    repositories.has_resource_properties(
        "AWS::ECR::Repository", {"ImageTagMutability": "IMMUTABLE"}
    )


def test_each_stage_deploys_its_own_runtime_stack(tmp_path: Path) -> None:
    infra = Path(__file__).parents[1]
    context = json.loads((infra / "cdk.json").read_text())["context"]
    runtime_stacks = set()
    for stage in context["stages"]:
        outdir = tmp_path / stage
        subprocess.run(
            [sys.executable, "app.py"],
            cwd=infra,
            env=os.environ
            | {
                "CDK_OUTDIR": str(outdir),
                "CDK_CONTEXT_JSON": json.dumps(
                    context
                    | {
                        "stage": stage,
                        "apiImageDigest": API_DIGEST,
                        "workerImageDigest": WORKER_DIGEST,
                    }
                ),
            },
            check=True,
        )
        manifest = json.loads((outdir / "manifest.json").read_text())
        [stack] = [
            name
            for name, artifact in manifest["artifacts"].items()
            if artifact["type"] == "aws:cloudformation:stack" and name != "CastleChefRepositories"
        ]
        runtime_stacks.add(stack)
    assert len(runtime_stacks) == len(context["stages"])


def test_development_images_never_expire_releases(repositories: Template) -> None:
    for repository in repositories.find_resources("AWS::ECR::Repository").values():
        policy = json.loads(repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
        counted = {
            tuple(rule["selection"].get("tagPrefixList", [])): rule["selection"]["countNumber"]
            for rule in policy["rules"]
            if rule["selection"]["countType"] == "imageCountMoreThan"
        }
        # Releases (r<N>, what rollback.yml deploys) are counted apart from dev- pushes.
        assert counted == {("dev-",): 5, ("r",): 30}
