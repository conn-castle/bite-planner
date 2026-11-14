"""The Bite Planner runtime (docs/operations/aws.md "Runtime and connections"):

  app ─► API Lambda (Function URL) ─► Neon; SQS jobs queue
         jobs queue ─► EventBridge Pipe ─► one Fargate worker task per job
         failures (jobs DLQ, ECS "task stopped") ─► failure-handler Lambda
  one EventBridge Scheduler schedule per member ─► Reminders Lambda ─► APNs
  the booking schedule (hourly, 9 AM-9 PM store time) ─► Reminders Lambda ─► jobs queue
         CloudWatch alarms ─► SNS email

Nothing runs or polls while idle.
"""

import json
from dataclasses import dataclass
from typing import Any

from aws_cdk import CfnOutput, Duration, Environment, RemovalPolicy, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as events_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_pipes as pipes
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subscriptions
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

WORKER_CONTAINER = "worker"  # bite_planner.failures.WORKER_CONTAINER
AGENT_UID = 65532  # the image's agent user (BITE_PLANNER_AGENT_UID)
# Codex's login (the household's ChatGPT account, with Instacart connected).
CODEX_HOME_DIR = "/mnt/agent-auth/codex"
# The Codex login's EFS access point, volume, and mount.
LOGIN_DIRS = {"codex": CODEX_HOME_DIR}
LOG_RETENTION = logs.RetentionDays.THREE_MONTHS
# Job records are kept as long as the logs (THREE_MONTHS is 90 days).
RECORD_RETENTION = Duration.days(90)
# The APNs auth key (Production, topic com.conncastle.biteplanner) and its id.
APNS_KEY_PARAMETER = "/bite-planner/shared/apns-key-base64"
APNS_KEY_ID_PARAMETER = "/bite-planner/shared/apns-key-id"
APNS_TEAM_ID_PARAMETER = "/bite-planner/shared/apple-team-id"
OPENAI_API_KEY_PARAMETER = "/bite-planner/shared/openai-api-key"
# The household's Instacart website session (browser cookies), which the worker
# reads for checkout and writes back as Instacart renews it.
INSTACART_SESSION_PARAMETER = "/bite-planner/shared/instacart-session"
# When booking runs (bite_planner.booking), in the store's time zone: at 9:00 AM,
# when orders become due, and each hour after it to 9 PM, so a run that failed is
# made up an hour later.
BOOKING_SCHEDULE = "cron(0 9-21 * * ? *)"
# Name suffix, kept outside the reminder- prefix managed by reminder sync.
BOOKING_SCHEDULE_NAME = "booking"
STORE_TIME_ZONE = "America/New_York"


@dataclass(frozen=True)
class RuntimeConfig:
    """Per-environment settings (cdk.json `stages`)."""

    neon_auth_url: str
    database_url_parameter: str  # SSM SecureString: pooled Neon URL
    # SSM String: whether booking presses Place order (live, dry_run, or off),
    # set by hand (docs/operations/aws.md), read by each worker task as it starts.
    instacart_booking_parameter: str
    cors_origins: list[str]
    alarm_email: str
    release: str  # the release version, logged by the services (BITE_PLANNER_RELEASE)
    api_image_digest: str
    worker_image_digest: str


class RuntimeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: RuntimeConfig,
        env: Environment | None = None,
    ) -> None:
        super().__init__(scope, construct_id, env=env)

        # Looked up by name (created by the repositories stack), so the two stacks
        # share no CloudFormation exports.
        repositories = {
            name: ecr.Repository.from_repository_name(
                self, f"{name.capitalize()}Repository", f"bite-planner-{name}"
            )
            for name in ("api", "worker")
        }

        database_url = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "DatabaseUrl", parameter_name=config.database_url_parameter
        )
        apns_key = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "ApnsKey", parameter_name=APNS_KEY_PARAMETER
        )
        apns_key_id = ssm.StringParameter.from_string_parameter_name(
            self, "ApnsKeyId", string_parameter_name=APNS_KEY_ID_PARAMETER
        )
        apns_team_id = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "ApnsTeamId", parameter_name=APNS_TEAM_ID_PARAMETER
        )
        openai_api_key = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "OpenAiApiKey", parameter_name=OPENAI_API_KEY_PARAMETER
        )
        instacart_booking = ssm.StringParameter.from_string_parameter_name(
            self, "InstacartBooking", string_parameter_name=config.instacart_booking_parameter
        )

        # Network: public subnets only, no NAT (nothing to pay for while idle).
        # The worker gets a public IP for outbound calls; nothing reaches in.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)
            ],
        )
        worker_sg = ec2.SecurityGroup(
            self, "WorkerSecurityGroup", vpc=vpc, description="Bite Planner worker tasks"
        )

        # Agent subscription logins (self-refreshing session files): EFS, one
        # access point per provider, owned by the agent user.
        logins = efs.FileSystem(
            self,
            "AgentLogins",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            encrypted=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        logins.connections.allow_default_port_from(worker_sg, "Worker tasks mount agent logins")
        login_access_points = {
            provider: logins.add_access_point(
                f"{provider.capitalize()}Login",
                path=f"/{provider}",
                create_acl=efs.Acl(
                    owner_uid=str(AGENT_UID), owner_gid=str(AGENT_UID), permissions="700"
                ),
                posix_user=efs.PosixUser(uid=str(AGENT_UID), gid=str(AGENT_UID)),
            )
            for provider in LOGIN_DIRS
        }

        # Jobs queue. The Pipe removes a message once RunTask accepts it; messages
        # it can't start a task for are retried, then dead-lettered.
        dead_letters = sqs.Queue(
            self, "JobsDeadLetters", retention_period=Duration.days(14), enforce_ssl=True
        )
        jobs = sqs.Queue(
            self,
            "Jobs",
            visibility_timeout=Duration.seconds(60),
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(queue=dead_letters, max_receive_count=3),
        )

        # Worker: one Fargate task per job.
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        worker_logs = logs.LogGroup(self, "WorkerLogs", retention=LOG_RETENTION)
        # Each job's record (what its agent was given and did: bite_planner.records).
        job_records = s3.Bucket(
            self,
            "JobRecords",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=RECORD_RETENTION)],
            removal_policy=RemovalPolicy.RETAIN,
        )
        worker = ecs.FargateTaskDefinition(
            self,
            "Worker",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        for provider, access_point in login_access_points.items():
            worker.add_volume(
                name=f"{provider}-login",
                efs_volume_configuration=ecs.EfsVolumeConfiguration(
                    file_system_id=logins.file_system_id,
                    transit_encryption="ENABLED",
                    authorization_config=ecs.AuthorizationConfig(
                        access_point_id=access_point.access_point_id, iam="ENABLED"
                    ),
                ),
            )
        worker.add_volume(name="tmp")
        linux = ecs.LinuxParameters(self, "WorkerLinux", init_process_enabled=True)
        # The worker (root) needs CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, and
        # KILL to prepare for, start, and stop the agent as another user.
        linux.drop_capabilities(
            ecs.Capability.AUDIT_WRITE,
            ecs.Capability.FSETID,
            ecs.Capability.MKNOD,
            ecs.Capability.NET_BIND_SERVICE,
            ecs.Capability.NET_RAW,
            ecs.Capability.SETFCAP,
            ecs.Capability.SETPCAP,
            ecs.Capability.SYS_CHROOT,
        )
        container = worker.add_container(
            WORKER_CONTAINER,
            image=ecs.ContainerImage.from_ecr_repository(
                repositories["worker"], config.worker_image_digest
            ),
            essential=True,
            readonly_root_filesystem=True,
            linux_parameters=linux,
            stop_timeout=Duration.seconds(120),
            logging=ecs.LogDriver.aws_logs(stream_prefix="worker", log_group=worker_logs),
            # The agent the worker starts never gets these (worker.py).
            secrets={
                "DATABASE_URL": ecs.Secret.from_ssm_parameter(database_url),
                "BITE_PLANNER_APNS_KEY": ecs.Secret.from_ssm_parameter(apns_key),
                "BITE_PLANNER_APNS_KEY_ID": ecs.Secret.from_ssm_parameter(apns_key_id),
                "BITE_PLANNER_APNS_TEAM_ID": ecs.Secret.from_ssm_parameter(apns_team_id),
                "BITE_PLANNER_INSTACART_BOOKING": ecs.Secret.from_ssm_parameter(instacart_booking),
                "OPENAI_API_KEY": ecs.Secret.from_ssm_parameter(openai_api_key),
            },
            environment={
                "BITE_PLANNER_RELEASE": config.release,
                # "Your meal ideas are ready", when a batch succeeds.
                "BITE_PLANNER_PUSH": "apns",
                "BITE_PLANNER_CODEX_HOME": CODEX_HOME_DIR,
                "BITE_PLANNER_CODEX_LOCK_FILE": f"{CODEX_HOME_DIR}/codex.lock",
                "BITE_PLANNER_INSTACART_SESSION_PARAMETER": INSTACART_SESSION_PARAMETER,
                "BITE_PLANNER_JOB_RECORDS_BUCKET": job_records.bucket_name,
                "BITE_PLANNER_WORKER_CLUSTER_ARN": cluster.cluster_arn,
            },
        )
        container.add_mount_points(
            *(
                ecs.MountPoint(
                    source_volume=f"{provider}-login", container_path=login_dir, read_only=False
                )
                for provider, login_dir in LOGIN_DIRS.items()
            ),
            ecs.MountPoint(source_volume="tmp", container_path="/tmp", read_only=False),
        )
        # The task role can only mount the agent logins (the agent can reach it).
        worker.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"],
                resources=[logins.file_system_arn],
                conditions={
                    "StringEquals": {
                        "elasticfilesystem:AccessPointArn": [
                            access_point.access_point_arn
                            for access_point in login_access_points.values()
                        ]
                    }
                },
            )
        )

        # The worker reads the Instacart session and writes it back when Instacart
        # renews it (the agent's environment has no AWS credentials, and it has no shell).
        worker.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:PutParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{INSTACART_SESSION_PARAMETER}"
                ],
            )
        )

        # The worker adds each job's record; it never reads or removes them.
        worker.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:PutObject"], resources=[job_records.arn_for_objects("jobs/*")]
            )
        )

        # Pipe: each queued job starts one worker task with its id.
        pipe_role = iam.Role(
            self, "PipeRole", assumed_by=iam.ServicePrincipal("pipes.amazonaws.com")
        )
        jobs.grant_consume_messages(pipe_role)
        pipe_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[worker.task_definition_arn],
                conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
            )
        )
        # Tagging the tasks it starts (propagate_tags below).
        pipe_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:TagResource"],
                resources=[
                    f"arn:aws:ecs:{self.region}:{self.account}:task/{cluster.cluster_name}/*"
                ],
                conditions={"StringEquals": {"ecs:CreateAction": "RunTask"}},
            )
        )
        pipe_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[worker.task_role.role_arn, worker.obtain_execution_role().role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            )
        )
        pipe = pipes.CfnPipe(
            self,
            "JobsPipe",
            role_arn=pipe_role.role_arn,
            source=jobs.queue_arn,
            source_parameters=pipes.CfnPipe.PipeSourceParametersProperty(
                # Batch size 1: the job id path below resolves only for one message.
                sqs_queue_parameters=pipes.CfnPipe.PipeSourceSqsQueueParametersProperty(
                    batch_size=1
                )
            ),
            target=cluster.cluster_arn,
            target_parameters=pipes.CfnPipe.PipeTargetParametersProperty(
                ecs_task_parameters=pipes.CfnPipe.PipeTargetEcsTaskParametersProperty(
                    task_definition_arn=worker.task_definition_arn,
                    launch_type="FARGATE",
                    task_count=1,
                    # Tasks carry the stack's tags, so their cost counts toward the budget.
                    propagate_tags="TASK_DEFINITION",
                    network_configuration=pipes.CfnPipe.NetworkConfigurationProperty(
                        awsvpc_configuration=pipes.CfnPipe.AwsVpcConfigurationProperty(
                            subnets=[subnet.subnet_id for subnet in vpc.public_subnets],
                            security_groups=[worker_sg.security_group_id],
                            assign_public_ip="ENABLED",
                        )
                    ),
                    overrides=pipes.CfnPipe.EcsTaskOverrideProperty(
                        container_overrides=[
                            pipes.CfnPipe.EcsContainerOverrideProperty(
                                name=WORKER_CONTAINER,
                                environment=[
                                    # A dynamic path: only as the whole value.
                                    pipes.CfnPipe.EcsEnvironmentVariableProperty(
                                        name="BITE_PLANNER_JOB_ID", value="$.body.job_id"
                                    )
                                ],
                            )
                        ]
                    ),
                )
            ),
        )

        # API, failure handler, and Reminders: the same image; only the API has a URL.
        api_image = lambda_.DockerImageCode.from_ecr(
            repositories["api"], tag_or_digest=config.api_image_digest
        )
        common_environment = {
            "BITE_PLANNER_SSM_PARAMETERS": json.dumps(
                {"DATABASE_URL": config.database_url_parameter}
            ),
            "AWS_LWA_READINESS_CHECK_TIMEOUT_SECONDS": "20",
            "BITE_PLANNER_RELEASE": config.release,
        }
        # The failure handler and Reminders send push (a booking that stopped tells
        # whoever approved the order).
        push_parameters = (database_url, apns_key, apns_key_id, apns_team_id)
        push_environment = {
            "BITE_PLANNER_SSM_PARAMETERS": json.dumps(
                {
                    "DATABASE_URL": config.database_url_parameter,
                    "BITE_PLANNER_APNS_KEY": APNS_KEY_PARAMETER,
                    "BITE_PLANNER_APNS_KEY_ID": APNS_KEY_ID_PARAMETER,
                    "BITE_PLANNER_APNS_TEAM_ID": APNS_TEAM_ID_PARAMETER,
                }
            ),
            "BITE_PLANNER_PUSH": "apns",
        }
        # They are invoked with events, which the Lambda Web Adapter posts to
        # `/events`. A failed event fails the invocation, so it is retried (the
        # adapter otherwise returns the error response as a success, and the event
        # is dropped).
        event_environment = {
            "AWS_LWA_PASS_THROUGH_PATH": "/events",
            "AWS_LWA_ERROR_STATUS_CODES": "500-599",
        }
        api = self._api_image_function(
            "Api",
            api_image,
            memory_size=2048,  # more CPU with it: faster cold starts
            timeout=Duration.seconds(30),
            reserved_concurrent_executions=5,  # bounds connections to Neon's pooler
            environment=common_environment
            | {
                "BITE_PLANNER_NEON_AUTH_URL": config.neon_auth_url,
                "BITE_PLANNER_JOB_DISPATCH": "sqs",
                "BITE_PLANNER_JOBS_QUEUE_URL": jobs.queue_url,
                "BITE_PLANNER_CORS_ORIGINS": json.dumps(config.cors_origins),
            },
        )
        api_url = api.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        database_url.grant_read(api)
        jobs.grant_send_messages(api)

        stop_delivery_dead_letters = sqs.Queue(
            self,
            "WorkerStopDeliveryDeadLetters",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        stop_processing_dead_letters = sqs.Queue(
            self,
            "WorkerStopProcessingDeadLetters",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        worker_stops = sqs.Queue(
            self,
            "WorkerStops",
            retention_period=Duration.days(14),
            visibility_timeout=Duration.seconds(180),
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=stop_processing_dead_letters, max_receive_count=5
            ),
        )
        failure_handler = self._api_image_function(
            "FailureHandler",
            api_image,
            memory_size=512,
            timeout=Duration.seconds(30),
            reserved_concurrent_executions=4,
            environment=common_environment
            | {
                **push_environment,
                "BITE_PLANNER_FAILURE_HANDLER": "true",
                "BITE_PLANNER_WORKER_CLUSTER_ARN": cluster.cluster_arn,
                "BITE_PLANNER_WORKER_STOPS_QUEUE_ARN": worker_stops.queue_arn,
                "BITE_PLANNER_JOBS_DEAD_LETTERS_QUEUE_ARN": dead_letters.queue_arn,
                **event_environment,
            },
        )
        for parameter in push_parameters:
            parameter.grant_read(failure_handler)
        failure_handler.add_event_source(
            lambda_event_sources.SqsEventSource(dead_letters, batch_size=1, max_concurrency=2)
        )
        failure_handler.add_event_source(
            lambda_event_sources.SqsEventSource(worker_stops, batch_size=1, max_concurrency=2)
        )
        events.Rule(
            self,
            "WorkerStopped",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={"clusterArn": [cluster.cluster_arn], "lastStatus": ["STOPPED"]},
            ),
            targets=[
                events_targets.SqsQueue(worker_stops, dead_letter_queue=stop_delivery_dead_letters)
            ],
        )

        # Reminders: one schedule per member (created by the API, not here) invokes
        # this function at their notification time; it sends through APNs.
        reminders_name = f"{self.stack_name}-Reminders"
        # Named, so the API and the function itself can be told its ARN without a
        # circular reference.
        reminders_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:{reminders_name}"
        schedule_group_name = f"{self.stack_name}-reminders"
        schedule_group = scheduler.CfnScheduleGroup(
            self, "ReminderSchedules", name=schedule_group_name
        )
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        scheduler_role.add_to_policy(
            iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[reminders_arn])
        )
        scheduler_environment = {
            "BITE_PLANNER_REMINDER_SCHEDULER": "aws",
            "BITE_PLANNER_REMINDER_SCHEDULE_GROUP": schedule_group_name,
            "BITE_PLANNER_REMINDER_FUNCTION_ARN": reminders_arn,
            "BITE_PLANNER_REMINDER_ROLE_ARN": scheduler_role.role_arn,
        }
        manage_schedules = [
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=[
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/{schedule_group_name}/*"
                ],
            ),
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}},
            ),
        ]
        reminders = self._api_image_function(
            "Reminders",
            api_image,
            function_name=reminders_name,
            memory_size=512,
            timeout=Duration.seconds(60),
            reserved_concurrent_executions=2,
            # Scheduler invokes it asynchronously: Lambda's own retries of a
            # failed firing, and how late it may still run one.
            retry_attempts=2,
            max_event_age=Duration.minutes(15),
            environment=common_environment
            | scheduler_environment
            | {
                "BITE_PLANNER_REMINDERS_HANDLER": "true",
                **push_environment,
                # The booking run starts book jobs.
                "BITE_PLANNER_JOB_DISPATCH": "sqs",
                "BITE_PLANNER_JOBS_QUEUE_URL": jobs.queue_url,
                **event_environment,
            },
        )
        for parameter in push_parameters:
            parameter.grant_read(reminders)
        jobs.grant_send_messages(reminders)
        # CloudFormation identifies schedules by Name alone, even across groups:
        # each runtime needs its own name. Reminder sync ignores this schedule
        # because it manages only `reminder-<user id>` names.
        booking_schedule = scheduler.CfnSchedule(
            self,
            "BookingSchedule",
            name=f"{self.stack_name}-{BOOKING_SCHEDULE_NAME}",
            description="Books approved orders that are due (bite_planner.booking)",
            group_name=schedule_group_name,
            schedule_expression=BOOKING_SCHEDULE,
            schedule_expression_timezone=STORE_TIME_ZONE,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=reminders_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps({"book_due": {}}),
            ),
        )
        # The group is named, not referenced: created first.
        booking_schedule.add_dependency(schedule_group)
        # Settings confirms a specific street address synchronously through Amazon Location.
        api.add_to_role_policy(
            iam.PolicyStatement(
                actions=["geo-places:Geocode", "geo-places:Autocomplete"],
                resources=[f"arn:{self.partition}:geo-places:{self.region}::provider/default"],
            )
        )
        for statement in manage_schedules:
            reminders.add_to_role_policy(statement)
            api.add_to_role_policy(statement)
        # The deploy's sync finds schedules left by removed members (the action
        # only takes "*").
        reminders.add_to_role_policy(
            iam.PolicyStatement(actions=["scheduler:ListSchedules"], resources=["*"])
        )
        for name, value in scheduler_environment.items():
            api.add_environment(name, value)

        # Alarms → email. Unexpected problems log at error level everywhere.
        alarms = sns.Topic(self, "Alarms", display_name="Bite Planner alarms")
        alarms.add_subscription(sns_subscriptions.EmailSubscription(config.alarm_email))
        notify = cloudwatch_actions.SnsAction(alarms)
        for name, queue in {
            "WorkerStopDelivery": stop_delivery_dead_letters,
            "WorkerStopProcessing": stop_processing_dead_letters,
        }.items():
            self._alarm(
                f"{name}DeadLettersAlarm",
                queue.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)),
                "Worker stop evidence could not be processed; replay the dead-lettered event. "
                "Purchase recovery remains blocked.",
                notify,
            )
        for name, log_group in {
            "Api": api.log_group,
            "Worker": worker_logs,
            "FailureHandler": failure_handler.log_group,
            "Reminders": reminders.log_group,
        }.items():
            errors = logs.MetricFilter(
                self,
                f"{name}ErrorLines",
                log_group=log_group,
                filter_pattern=logs.FilterPattern.any(
                    logs.FilterPattern.string_value("$.level", "=", "error"),
                    logs.FilterPattern.string_value("$.level", "=", "critical"),
                ),
                metric_namespace=f"BitePlanner/{self.stack_name}",  # per stage
                metric_name=f"{name}ErrorLines",
                metric_value="1",
            )
            self._alarm(
                f"{name}Errors",
                errors.metric(statistic="Sum", period=Duration.minutes(5)),
                f"Bite Planner {name.lower()} logged an error",
                notify,
            )
        for name, function in {
            "Api": api,
            "FailureHandler": failure_handler,
            "Reminders": reminders,
        }.items():
            self._alarm(
                f"{name}LambdaErrors",
                function.metric_errors(period=Duration.minutes(5)),
                f"Bite Planner {name.lower()} Lambda errors",
                notify,
            )
        self._alarm(
            "PipeFailures",
            cloudwatch.Metric(
                namespace="AWS/EventBridge/Pipes",
                metric_name="ExecutionFailed",
                dimensions_map={"PipeName": pipe.ref},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            "Bite Planner jobs pipe could not start a worker",
            notify,
        )

        for metric_name, description in {
            "TargetErrorCount": "Bite Planner reminder or booking schedule couldn't run Reminders",
            "InvocationDroppedCount": "Bite Planner reminder or booking schedule dropped a firing",
        }.items():
            self._alarm(
                f"Reminders{metric_name}",
                cloudwatch.Metric(
                    namespace="AWS/Scheduler",
                    metric_name=metric_name,
                    dimensions_map={"ScheduleGroup": schedule_group_name},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                description,
                notify,
            )

        # What the workflows and docs/operations/aws.md read (`<id>` in cdk-outputs.json
        # and describe-stacks).
        for output_id, value in {
            "ApiUrlOutput": api_url.url,
            "RemindersFunctionOutput": reminders.function_name,
            "ClusterNameOutput": cluster.cluster_name,
            "JobsPipeOutput": pipe.ref,
            "WorkerTaskDefinitionOutput": worker.task_definition_arn,
            "WorkerSecurityGroupOutput": worker_sg.security_group_id,
            "WorkerSubnetsOutput": ",".join(subnet.subnet_id for subnet in vpc.public_subnets),
            "WorkerLogGroupOutput": worker_logs.log_group_name,
            "JobRecordsBucketOutput": job_records.bucket_name,
        }.items():
            CfnOutput(self, output_id, value=value)

    def _api_image_function(
        self, construct_id: str, image: lambda_.DockerImageCode, **options: Any
    ) -> lambda_.DockerImageFunction:
        """A function from the api image, logging to its own group (`<id>Logs`)."""
        return lambda_.DockerImageFunction(
            self,
            construct_id,
            code=image,
            architecture=lambda_.Architecture.X86_64,
            log_group=logs.LogGroup(self, f"{construct_id}Logs", retention=LOG_RETENTION),
            **options,
        )

    def _alarm(
        self,
        construct_id: str,
        metric: cloudwatch.IMetric,
        description: str,
        action: cloudwatch_actions.SnsAction,
    ) -> None:
        alarm = cloudwatch.Alarm(
            self,
            construct_id,
            metric=metric,
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=description,
        )
        alarm.add_alarm_action(action)
