"""What the stacks must guarantee (see bite_planner_infra/runtime.py), checked on
the synthesized CloudFormation."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from bite_planner_infra.repositories import RepositoriesStack
from bite_planner_infra.runtime import RuntimeConfig, RuntimeStack

API_DIGEST = "sha256:" + "a" * 64
WORKER_DIGEST = "sha256:" + "b" * 64
OIDC = "token.actions.githubusercontent.com"
MAIN_BRANCH = "repo:owner@1/bite-planner@2:ref:refs/heads/main"
REPOSITORY = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def runtime() -> Template:
    app = cdk.App()
    stack = RuntimeStack(
        app,
        "Runtime",
        config=RuntimeConfig(
            neon_auth_url="https://ep-x.neonauth.example/neondb/auth",
            database_url_parameter="/bite-planner/test/database-url-pooled",
            instacart_booking_parameter="/bite-planner/test/instacart-booking",
            cors_origins=["capacitor://localhost"],
            alarm_email="owner@example.com",
            release="2026.9.24.1",
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
        github_subject="repo:owner@1/bite-planner@2",
        budget_email="owner@example.com",
    )
    return Template.from_stack(stack)


def worker_container(runtime: Template) -> dict[str, Any]:
    [task] = runtime.find_resources("AWS::ECS::TaskDefinition").values()
    [container] = task["Properties"]["ContainerDefinitions"]
    assert isinstance(container, dict)
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
    secrets = [
        "DATABASE_URL",
        "BITE_PLANNER_APNS_KEY",
        "BITE_PLANNER_APNS_KEY_ID",
        "BITE_PLANNER_APNS_TEAM_ID",
        "BITE_PLANNER_INSTACART_BOOKING",
        "OPENAI_API_KEY",
    ]
    assert [s["Name"] for s in container["Secrets"]] == secrets
    environment = {e["Name"] for e in container.get("Environment", [])}
    assert not environment & set(secrets)


@pytest.mark.parametrize(
    ("volume", "path", "variables"),
    [
        ("codex-login", "/codex", ["BITE_PLANNER_CODEX_HOME", "BITE_PLANNER_CODEX_LOCK_FILE"]),
    ],
)
def test_worker_login_storage_belongs_to_the_agent_user(
    runtime: Template, volume: str, path: str, variables: list[str]
) -> None:
    [mount] = [m for m in worker_container(runtime)["MountPoints"] if m["SourceVolume"] == volume]
    assert mount["ReadOnly"] is False
    login_dir = mount["ContainerPath"]
    environment = {e["Name"]: e["Value"] for e in worker_container(runtime)["Environment"]}
    # The login and the lock that serializes its users both live on EFS.
    for variable in variables:
        assert environment[variable] == login_dir or environment[variable].startswith(
            f"{login_dir}/"
        )
    runtime.has_resource_properties("AWS::EFS::FileSystem", {"Encrypted": True})
    runtime.has_resource("AWS::EFS::FileSystem", {"DeletionPolicy": "Retain"})
    runtime.has_resource_properties(
        "AWS::EFS::AccessPoint",
        {
            "PosixUser": {"Uid": "65532", "Gid": "65532"},
            "RootDirectory": {
                "Path": path,
                "CreationInfo": Match.object_like({"Permissions": "700"}),
            },
        },
    )


def test_the_worker_can_mount_only_the_login_access_points(runtime: Template) -> None:
    access_points = runtime.find_resources("AWS::EFS::AccessPoint")
    [statement] = [
        statement
        for policy in runtime.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if statement["Action"] == ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    ]
    allowed = statement["Condition"]["StringEquals"]["elasticfilesystem:AccessPointArn"]
    # Each ARN is built from its access point's reference.
    named = {part["Ref"] for arn in allowed for part in arn["Fn::Join"][1] if "Ref" in part}
    assert named & set(access_points) == set(access_points)
    assert len(allowed) == len(access_points)


def test_the_worker_reads_and_renews_the_instacart_session(runtime: Template) -> None:
    granted = [
        (policy["Properties"]["Roles"], statement)
        for policy in runtime.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "instacart-session" in json.dumps(statement["Resource"])
    ]
    # Only the worker's task role (not its execution role, or any other).
    [(roles, statement)] = granted
    assert "WorkerTaskRole" in json.dumps(roles)
    assert statement["Action"] == ["ssm:GetParameter", "ssm:PutParameter"]
    environment = {e["Name"]: e["Value"] for e in worker_container(runtime)["Environment"]}
    assert environment["BITE_PLANNER_INSTACART_SESSION_PARAMETER"] == (
        "/bite-planner/shared/instacart-session"
    )


def test_each_jobs_record_is_kept_privately_as_long_as_the_logs(runtime: Template) -> None:
    [(bucket_id, bucket)] = runtime.find_resources("AWS::S3::Bucket").items()
    properties = bucket["Properties"]
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    [rule] = properties["LifecycleConfiguration"]["Rules"]
    assert rule["ExpirationInDays"] == 90 and rule["Status"] == "Enabled"
    assert bucket["DeletionPolicy"] == "Retain"
    # Only the worker's task role writes there, and only records.
    granted = [
        (policy["Properties"]["Roles"], statement)
        for policy in runtime.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if bucket_id in json.dumps(statement["Resource"])
    ]
    [(roles, statement)] = granted
    assert "WorkerTaskRole" in json.dumps(roles)
    assert statement["Action"] == "s3:PutObject"
    assert "/jobs/*" in json.dumps(statement["Resource"])
    environment = {e["Name"]: e["Value"] for e in worker_container(runtime)["Environment"]}
    assert environment["BITE_PLANNER_JOB_RECORDS_BUCKET"] == {"Ref": bucket_id}


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
                        "PropagateTags": "TASK_DEFINITION",
                        "NetworkConfiguration": {
                            "AwsvpcConfiguration": Match.object_like({"AssignPublicIp": "ENABLED"})
                        },
                        "Overrides": {
                            "ContainerOverrides": [
                                {
                                    "Name": "worker",
                                    "Environment": [
                                        {"Name": "BITE_PLANNER_JOB_ID", "Value": "$.body.job_id"}
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


def functions(runtime: Template) -> dict[str, dict[str, Any]]:
    return {
        name: resource["Properties"]
        for name, resource in runtime.find_resources("AWS::Lambda::Function").items()
        if "ImageUri" in str(resource["Properties"].get("Code", {}))
    }


def test_api_runs_outside_any_vpc_within_limits(runtime: Template) -> None:
    [api] = [
        f
        for f in functions(runtime).values()
        if "BITE_PLANNER_NEON_AUTH_URL" in f["Environment"]["Variables"]
    ]
    assert "VpcConfig" not in api
    assert api["Timeout"] == 30
    assert api["ReservedConcurrentExecutions"] == 5
    variables = api["Environment"]["Variables"]
    assert variables["BITE_PLANNER_SSM_PARAMETERS"] == (
        '{"DATABASE_URL": "/bite-planner/test/database-url-pooled"}'
    )
    assert "DATABASE_URL" not in variables  # never in the function's configuration
    runtime.has_resource_properties("AWS::Lambda::Url", {"AuthType": "NONE"})


def test_only_the_api_has_a_url(runtime: Template) -> None:
    runtime.resource_count_is("AWS::Lambda::Url", 1)
    [handler] = [
        f
        for f in functions(runtime).values()
        if f["Environment"]["Variables"].get("BITE_PLANNER_FAILURE_HANDLER") == "true"
    ]
    assert "BITE_PLANNER_JOBS_QUEUE_URL" not in handler["Environment"]["Variables"]


def test_booking_presses_place_order_only_as_the_stages_switch_says(runtime: Template) -> None:
    [secret] = [
        s
        for s in worker_container(runtime)["Secrets"]
        if s["Name"] == "BITE_PLANNER_INSTACART_BOOKING"
    ]
    # Read by each task as it starts, from the parameter set by hand.
    assert "/bite-planner/test/instacart-booking" in json.dumps(secret["ValueFrom"])


def backend_literal(module: str, name: str) -> object:
    """The literal assigned to `name` in backend/src/bite_planner/<module> (a constant, or
    a setting's default), read from the source: infra doesn't install the backend."""
    tree = ast.parse((REPOSITORY / "backend" / "src" / "bite_planner" / module).read_text())
    [value] = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and node.value is not None
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    return ast.literal_eval(value)


def test_booking_runs_hourly_through_the_day_once_orders_become_due(runtime: Template) -> None:
    _, reminders = reminders_function(runtime)
    schedules = runtime.find_resources("AWS::Scheduler::Schedule")
    [schedule] = [s["Properties"] for s in schedules.values()]
    # An order is due from BOOK_HOUR in the store's time zone (booking.is_due), which
    # no stack overrides; a first run at another hour would book every order late.
    # Each hour after, to 9 PM, makes up a run that failed.
    book_hour = backend_literal("booking.py", "BOOK_HOUR")
    assert schedule["ScheduleExpression"] == f"cron(0 {book_hour}-21 * * ? *)"
    store_time_zone = backend_literal("settings.py", "store_time_zone")
    assert schedule["ScheduleExpressionTimezone"] == store_time_zone
    environments = [
        {e["Name"] for e in worker_container(runtime)["Environment"]},
        *(f["Environment"]["Variables"] for f in functions(runtime).values()),
    ]
    assert not any("BITE_PLANNER_STORE_TIME_ZONE" in env for env in environments)
    # In the members' group, which the Scheduler alarms watch: a run Scheduler
    # can't deliver emails the owner.
    assert schedule["GroupName"] == "Runtime-reminders"
    # Named (Name is create-only), so its move out of the default group, where its
    # name was generated, replaces it rather than updating a schedule Scheduler
    # can't find in the new group; and not a name the reminder sync would delete.
    assert schedule["Name"] == "booking"
    assert not schedule["Name"].startswith(backend_literal("reminders.py", "SCHEDULE_PREFIX"))
    [group] = runtime.find_resources("AWS::Scheduler::ScheduleGroup")
    assert [group] == next(iter(schedules.values()))["DependsOn"]
    for metric in ("TargetErrorCount", "InvocationDroppedCount"):
        runtime.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "AWS/Scheduler",
                "MetricName": metric,
                "Dimensions": [{"Name": "ScheduleGroup", "Value": "Runtime-reminders"}],
            },
        )
    assert json.loads(schedule["Target"]["Input"]) == {"book_due": {}}
    assert "function:Runtime-Reminders" in json.dumps(schedule["Target"]["Arn"])
    # The run queues the book jobs that are due.
    variables = reminders["Environment"]["Variables"]
    assert variables["BITE_PLANNER_JOB_DISPATCH"] == "sqs"
    assert "BITE_PLANNER_JOBS_QUEUE_URL" in variables
    sends = [
        statement
        for policy in runtime.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "sqs:SendMessage" in json.dumps(statement["Action"])
        and policy["Properties"]["Roles"][0]["Ref"].startswith("Reminders")
    ]
    assert len(sends) == 1


def test_a_stopped_booking_tells_whoever_approved_the_order(runtime: Template) -> None:
    [handler] = [
        f
        for f in functions(runtime).values()
        if f["Environment"]["Variables"].get("BITE_PLANNER_FAILURE_HANDLER")
    ]
    variables = handler["Environment"]["Variables"]
    assert variables["BITE_PLANNER_PUSH"] == "apns"
    assert json.loads(variables["BITE_PLANNER_SSM_PARAMETERS"]) == {
        "DATABASE_URL": "/bite-planner/test/database-url-pooled",
        "BITE_PLANNER_APNS_KEY": "/bite-planner/shared/apns-key-base64",
        "BITE_PLANNER_APNS_KEY_ID": "/bite-planner/shared/apns-key-id",
        "BITE_PLANNER_APNS_TEAM_ID": "/bite-planner/shared/apple-team-id",
    }


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
    [(rule_id, rule)] = runtime.find_resources("AWS::Events::Rule").items()
    [target] = rule["Properties"]["Targets"]
    mappings = runtime.find_resources("AWS::Lambda::EventSourceMapping")
    queues = runtime.find_resources("AWS::SQS::Queue")
    [stops] = [name for name in queues if name.startswith("WorkerStops")]
    [dead_letters] = [name for name in queues if name.startswith("JobsDeadLetters")]
    [handler] = [
        name
        for name, f in runtime.find_resources("AWS::Lambda::Function").items()
        if f["Properties"]["Environment"]["Variables"].get("BITE_PLANNER_FAILURE_HANDLER")
    ]
    assert target["Arn"] == {"Fn::GetAtt": [stops, "Arn"]}
    for queue in (stops, dead_letters):
        assert any(
            mapping["Properties"]["EventSourceArn"] == {"Fn::GetAtt": [queue, "Arn"]}
            and mapping["Properties"]["FunctionName"] == {"Ref": handler}
            and mapping["Properties"]["BatchSize"] == 1
            and mapping["Properties"]["ScalingConfig"]["MaximumConcurrency"] == 2
            for mapping in mappings.values()
        )
    assert "RedrivePolicy" in queues[stops]["Properties"]
    [delivery_dlq] = [name for name in queues if name.startswith("WorkerStopDeliveryDeadLetters")]
    [processing_dlq] = [
        name for name in queues if name.startswith("WorkerStopProcessingDeadLetters")
    ]
    assert target["DeadLetterConfig"]["Arn"] == {"Fn::GetAtt": [delivery_dlq, "Arn"]}
    assert queues[stops]["Properties"]["RedrivePolicy"]["deadLetterTargetArn"] == {
        "Fn::GetAtt": [processing_dlq, "Arn"]
    }
    alarms = runtime.find_resources("AWS::CloudWatch::Alarm")
    assert any(name.startswith("WorkerStopDeliveryDeadLettersAlarm") for name in alarms)
    assert any(name.startswith("WorkerStopProcessingDeadLettersAlarm") for name in alarms)
    # Only the lifecycle rule's AWS service principal can supply stop proof.
    [policy] = [
        policy["Properties"]["PolicyDocument"]
        for policy in runtime.find_resources("AWS::SQS::QueuePolicy").values()
        if {"Ref": stops} in policy["Properties"]["Queues"]
    ]
    [send] = [statement for statement in policy["Statement"] if statement["Effect"] == "Allow"]
    assert send["Principal"] == {"Service": "events.amazonaws.com"}
    assert "sqs:SendMessage" in send["Action"]
    assert send["Condition"] == {"ArnEquals": {"aws:SourceArn": {"Fn::GetAtt": [rule_id, "Arn"]}}}
    # An error response fails the invocation, so the event is delivered again
    # instead of dropped.
    variables = functions(runtime)[handler]["Environment"]["Variables"]
    assert variables["AWS_LWA_ERROR_STATUS_CODES"] == "500-599"
    assert functions(runtime)[handler]["ReservedConcurrentExecutions"] == 4


def reminders_function(runtime: Template) -> tuple[str, dict[str, Any]]:
    [(name, reminders)] = [
        (name, f["Properties"])
        for name, f in runtime.find_resources("AWS::Lambda::Function").items()
        if f["Properties"]["Environment"]["Variables"].get("BITE_PLANNER_REMINDERS_HANDLER")
    ]
    return name, reminders


def test_each_members_schedule_invokes_the_reminders_function_only(runtime: Template) -> None:
    name, reminders = reminders_function(runtime)
    variables = reminders["Environment"]["Variables"]
    assert json.loads(variables["BITE_PLANNER_SSM_PARAMETERS"]) == {
        "DATABASE_URL": "/bite-planner/test/database-url-pooled",
        "BITE_PLANNER_APNS_KEY": "/bite-planner/shared/apns-key-base64",
        "BITE_PLANNER_APNS_KEY_ID": "/bite-planner/shared/apns-key-id",
        "BITE_PLANNER_APNS_TEAM_ID": "/bite-planner/shared/apple-team-id",
    }
    assert variables["BITE_PLANNER_PUSH"] == "apns"
    assert variables["AWS_LWA_PASS_THROUGH_PATH"] == "/events"
    # An error response fails the invocation, so Lambda retries the firing.
    assert variables["AWS_LWA_ERROR_STATUS_CODES"] == "500-599"
    assert reminders["FunctionName"] == "Runtime-Reminders"
    # Late firings aren't retried for long.
    runtime.has_resource_properties(
        "AWS::Lambda::EventInvokeConfig",
        {"FunctionName": {"Ref": name}, "MaximumEventAgeInSeconds": 900, "MaximumRetryAttempts": 2},
    )
    runtime.has_resource_properties("AWS::Scheduler::ScheduleGroup", {"Name": "Runtime-reminders"})
    # The role the schedules run as can only invoke Reminders.
    [role] = [
        name
        for name, role in runtime.find_resources("AWS::IAM::Role").items()
        if "scheduler.amazonaws.com" in str(role["Properties"]["AssumeRolePolicyDocument"])
    ]
    [policy] = [
        p["Properties"]["PolicyDocument"]["Statement"]
        for p in runtime.find_resources("AWS::IAM::Policy").values()
        if p["Properties"]["Roles"] == [{"Ref": role}]
    ]
    assert [(s["Action"], s["Effect"]) for s in policy] == [("lambda:InvokeFunction", "Allow")]
    assert "function:Runtime-Reminders" in json.dumps(policy[0]["Resource"])


def test_the_api_keeps_members_schedules_but_never_holds_the_push_key(runtime: Template) -> None:
    [api] = [
        f
        for f in functions(runtime).values()
        if "BITE_PLANNER_NEON_AUTH_URL" in f["Environment"]["Variables"]
    ]
    variables = api["Environment"]["Variables"]
    assert variables["BITE_PLANNER_REMINDER_SCHEDULER"] == "aws"
    assert variables["BITE_PLANNER_REMINDER_SCHEDULE_GROUP"] == "Runtime-reminders"
    assert "APNS" not in variables["BITE_PLANNER_SSM_PARAMETERS"]


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


def test_release_can_authenticate_public_pulls_without_public_writes(
    repositories: Template,
) -> None:
    [role] = [
        name
        for name, resource in repositories.find_resources("AWS::IAM::Role").items()
        if resource["Properties"].get("RoleName") == "bite-planner-github-deploy"
    ]
    actions: set[str] = {
        action
        for policy in repositories.find_resources("AWS::IAM::Policy").values()
        if {"Ref": role} in policy["Properties"]["Roles"]
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if statement["Effect"] == "Allow"
        for action in (
            [statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"]
        )
    }
    assert {"ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"} <= actions
    assert {action for action in actions if action.startswith("ecr-public:")} == {
        "ecr-public:GetAuthorizationToken"
    }
    assert "*" not in actions and "sts:*" not in actions


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
                "AWS_ACCOUNT_ID": "123456789012",
                "AWS_REGION": "us-east-1",
                "BITE_PLANNER_GITHUB_SUBJECT": "repo:example-owner@1/example-repo@2",
                "BITE_PLANNER_ALARM_EMAIL": "operator@example.com",
                "NEON_AUTH_URL_DEV": "https://dev-auth.example.test/neondb/auth",
                "NEON_AUTH_URL_PROD": "https://auth.example.test/neondb/auth",
                "CDK_OUTDIR": str(outdir),
                "CDK_CONTEXT_JSON": json.dumps(
                    context
                    | {
                        "stage": stage,
                        "release": "2026.9.24.1",
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
            if artifact["type"] == "aws:cloudformation:stack" and name != "BitePlannerRepositories"
        ]
        runtime_stacks.add(stack)
        template = json.loads(
            (outdir / manifest["artifacts"][stack]["properties"]["templateFile"]).read_text()
        )
        urls = {
            resource["Properties"]["Environment"]["Variables"]["BITE_PLANNER_NEON_AUTH_URL"]
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::Lambda::Function"
            and "BITE_PLANNER_NEON_AUTH_URL"
            in resource["Properties"].get("Environment", {}).get("Variables", {})
        }
        assert urls == {f"https://{'dev-' if stage == 'dev' else ''}auth.example.test/neondb/auth"}
    assert len(runtime_stacks) == len(context["stages"])


def test_deploy_without_private_configuration_fails_before_synthesizing(tmp_path: Path) -> None:
    infra = Path(__file__).parents[1]
    env = {
        name: value
        for name, value in os.environ.items()
        if name
        not in (
            "AWS_ACCOUNT_ID",
            "AWS_REGION",
            "BITE_PLANNER_GITHUB_SUBJECT",
            "BITE_PLANNER_ALARM_EMAIL",
            "NEON_AUTH_URL_DEV",
            "NEON_AUTH_URL_PROD",
        )
    }
    result = subprocess.run(
        [sys.executable, "app.py"],
        cwd=infra,
        env=env | {"CDK_OUTDIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Missing AWS_ACCOUNT_ID" in result.stderr
    assert not list(tmp_path.glob("*.template.json"))


def test_development_images_never_expire_releases(repositories: Template) -> None:
    for repository in repositories.find_resources("AWS::ECR::Repository").values():
        policy = json.loads(repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
        counted = {
            tuple(rule["selection"].get("tagPrefixList", [])): rule["selection"]["countNumber"]
            for rule in policy["rules"]
            if rule["selection"]["countType"] == "imageCountMoreThan"
        }
        # Released images (commit-<sha>, what rollback.yml deploys) are counted apart
        # from dev- pushes.
        assert counted == {("dev-",): 5, ("commit-",): 30}


def test_a_monthly_budget_emails_the_owner(repositories: Template) -> None:
    repositories.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": Match.object_like(
                {
                    "TimeUnit": "MONTHLY",
                    "CostFilters": {"TagKeyValue": ["user:project$bite-planner"]},
                }
            ),
            "NotificationsWithSubscribers": Match.array_with(
                [
                    Match.object_like(
                        {
                            "Subscribers": [
                                {"SubscriptionType": "EMAIL", "Address": "owner@example.com"}
                            ]
                        }
                    )
                ]
            ),
        },
    )


def test_services_log_the_deployed_release(runtime: Template) -> None:
    environments = [
        {e["Name"]: e["Value"] for e in worker_container(runtime)["Environment"]},
        *(f["Environment"]["Variables"] for f in functions(runtime).values()),
    ]
    assert len(environments) == 4  # the worker, the API, the failure handler, Reminders
    assert all(env["BITE_PLANNER_RELEASE"] == "2026.9.24.1" for env in environments)


def test_only_the_api_can_verify_addresses_with_the_default_provider(runtime: Template) -> None:
    [api] = [
        f
        for f in functions(runtime).values()
        if "BITE_PLANNER_NEON_AUTH_URL" in f["Environment"]["Variables"]
    ]
    matches = [
        (policy["Properties"]["Roles"], statement)
        for policy in runtime.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "geo-places:Geocode" in json.dumps(statement.get("Action"))
    ]
    assert len(matches) == 1
    roles, statement = matches[0]
    assert roles == [{"Ref": api["Role"]["Fn::GetAtt"][0]}]
    assert set(statement["Action"]) == {"geo-places:Geocode", "geo-places:Autocomplete"}
    resource = json.dumps(statement["Resource"])
    assert ":geo-places:" in resource and "::provider/default" in resource
    assert statement["Effect"] == "Allow"
