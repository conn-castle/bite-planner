"""The Castle Chef runtime (.agent-layer/tmp/architecture-plan.md):

  app ─► API Lambda (Function URL) ─► Neon; SQS jobs queue
         jobs queue ─► EventBridge Pipe ─► one Fargate worker task per job
         failures (jobs DLQ, ECS "task stopped") ─► failure-handler Lambda
  one EventBridge Scheduler schedule per member ─► Reminders Lambda ─► APNs
         CloudWatch alarms ─► SNS email

Nothing runs or polls while idle.
"""

import json
from dataclasses import dataclass

from aws_cdk import Duration, RemovalPolicy, Stack
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
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as sns_subscriptions
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

WORKER_CONTAINER = "worker"  # castle_chef.failures.WORKER_CONTAINER
AGENT_UID = 65532  # the image's agent user (CASTLE_CHEF_AGENT_UID)
GROK_AUTH_DIR = "/mnt/agent-auth/grok"
LOG_RETENTION = logs.RetentionDays.THREE_MONTHS
# The APNs auth key (Production, topic com.conncastle.castlechef) and its id.
APNS_KEY_PARAMETER = "/castle-chef/shared/apns-key-base64"
APNS_KEY_ID_PARAMETER = "/castle-chef/shared/apns-key-id"


@dataclass(frozen=True)
class RuntimeConfig:
    """Per-environment settings (cdk.json `stages`)."""

    neon_auth_url: str
    database_url_parameter: str  # SSM SecureString: pooled Neon URL
    cors_origins: list[str]
    alarm_email: str
    release: str  # the release version, logged by the services (CASTLE_CHEF_RELEASE)
    api_image_digest: str
    worker_image_digest: str


class RuntimeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: RuntimeConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Looked up by name (created by the repositories stack), so the two stacks
        # share no CloudFormation exports.
        repositories = {
            name: ecr.Repository.from_repository_name(
                self, f"{name.capitalize()}Repository", f"castle-chef-{name}"
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
            self, "WorkerSecurityGroup", vpc=vpc, description="Castle Chef worker tasks"
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
        grok_logins = logins.add_access_point(
            "GrokLogin",
            path="/grok",
            create_acl=efs.Acl(
                owner_uid=str(AGENT_UID), owner_gid=str(AGENT_UID), permissions="700"
            ),
            posix_user=efs.PosixUser(uid=str(AGENT_UID), gid=str(AGENT_UID)),
        )

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
        worker.add_volume(
            name="grok-login",
            efs_volume_configuration=ecs.EfsVolumeConfiguration(
                file_system_id=logins.file_system_id,
                transit_encryption="ENABLED",
                authorization_config=ecs.AuthorizationConfig(
                    access_point_id=grok_logins.access_point_id, iam="ENABLED"
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
                "CASTLE_CHEF_APNS_KEY": ecs.Secret.from_ssm_parameter(apns_key),
                "CASTLE_CHEF_APNS_KEY_ID": ecs.Secret.from_ssm_parameter(apns_key_id),
            },
            environment={
                "CASTLE_CHEF_RELEASE": config.release,
                # "Your meal ideas are ready", when a batch succeeds.
                "CASTLE_CHEF_PUSH": "apns",
                "GROK_AUTH_PATH": f"{GROK_AUTH_DIR}/auth.json",
                "CASTLE_CHEF_AGENT_LOCK_FILE": f"{GROK_AUTH_DIR}/agent.lock",
            },
        )
        container.add_mount_points(
            ecs.MountPoint(
                source_volume="grok-login", container_path=GROK_AUTH_DIR, read_only=False
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
                        "elasticfilesystem:AccessPointArn": grok_logins.access_point_arn
                    }
                },
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
                                        name="CASTLE_CHEF_JOB_ID", value="$.body.job_id"
                                    )
                                ],
                            )
                        ]
                    ),
                )
            ),
        )

        # API and failure handler: the same image. The failure handler has no URL;
        # the Lambda Web Adapter posts its events to `/events`.
        api_logs = logs.LogGroup(self, "ApiLogs", retention=LOG_RETENTION)
        common_environment = {
            "CASTLE_CHEF_SSM_PARAMETERS": json.dumps(
                {"DATABASE_URL": config.database_url_parameter}
            ),
            "AWS_LWA_READINESS_CHECK_TIMEOUT_SECONDS": "20",
            "CASTLE_CHEF_RELEASE": config.release,
        }
        api = lambda_.DockerImageFunction(
            self,
            "Api",
            code=lambda_.DockerImageCode.from_ecr(
                repositories["api"], tag_or_digest=config.api_image_digest
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=2048,  # more CPU with it: faster cold starts
            timeout=Duration.seconds(30),
            reserved_concurrent_executions=5,  # bounds connections to Neon's pooler
            log_group=api_logs,
            environment=common_environment
            | {
                "CASTLE_CHEF_NEON_AUTH_URL": config.neon_auth_url,
                "CASTLE_CHEF_JOB_DISPATCH": "sqs",
                "CASTLE_CHEF_JOBS_QUEUE_URL": jobs.queue_url,
                "CASTLE_CHEF_CORS_ORIGINS": json.dumps(config.cors_origins),
            },
        )
        api_url = api.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        database_url.grant_read(api)
        jobs.grant_send_messages(api)

        failures_logs = logs.LogGroup(self, "FailureHandlerLogs", retention=LOG_RETENTION)
        failure_handler = lambda_.DockerImageFunction(
            self,
            "FailureHandler",
            code=lambda_.DockerImageCode.from_ecr(
                repositories["api"], tag_or_digest=config.api_image_digest
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=512,
            timeout=Duration.seconds(30),
            reserved_concurrent_executions=2,
            log_group=failures_logs,
            environment=common_environment
            | {
                "CASTLE_CHEF_FAILURE_HANDLER": "true",
                "AWS_LWA_PASS_THROUGH_PATH": "/events",
            },
        )
        database_url.grant_read(failure_handler)
        failure_handler.add_event_source(
            lambda_event_sources.SqsEventSource(dead_letters, batch_size=10)
        )
        events.Rule(
            self,
            "WorkerStopped",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={"clusterArn": [cluster.cluster_arn], "lastStatus": ["STOPPED"]},
            ),
            targets=[events_targets.LambdaFunction(failure_handler)],
        )

        # Reminders: one schedule per member (created by the API, not here) invokes
        # this function at their notification time; it sends through APNs.
        reminders_name = f"{self.stack_name}-Reminders"
        # Named, so the API and the function itself can be told its ARN without a
        # circular reference.
        reminders_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:{reminders_name}"
        schedule_group = scheduler.CfnScheduleGroup(
            self, "ReminderSchedules", name=f"{self.stack_name}-reminders"
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
            "CASTLE_CHEF_REMINDER_SCHEDULER": "aws",
            "CASTLE_CHEF_REMINDER_SCHEDULE_GROUP": schedule_group.name or "",
            "CASTLE_CHEF_REMINDER_FUNCTION_ARN": reminders_arn,
            "CASTLE_CHEF_REMINDER_ROLE_ARN": scheduler_role.role_arn,
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
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/{schedule_group.name}/*"
                ],
            ),
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}},
            ),
        ]
        reminders_logs = logs.LogGroup(self, "RemindersLogs", retention=LOG_RETENTION)
        reminders = lambda_.DockerImageFunction(
            self,
            "Reminders",
            function_name=reminders_name,
            code=lambda_.DockerImageCode.from_ecr(
                repositories["api"], tag_or_digest=config.api_image_digest
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=512,
            timeout=Duration.seconds(60),
            reserved_concurrent_executions=2,
            log_group=reminders_logs,
            # Scheduler invokes it asynchronously: Lambda's own retries of a
            # failed firing, and how late it may still run one.
            retry_attempts=2,
            max_event_age=Duration.minutes(15),
            environment=common_environment
            | scheduler_environment
            | {
                "CASTLE_CHEF_SSM_PARAMETERS": json.dumps(
                    {
                        "DATABASE_URL": config.database_url_parameter,
                        "CASTLE_CHEF_APNS_KEY": APNS_KEY_PARAMETER,
                        "CASTLE_CHEF_APNS_KEY_ID": APNS_KEY_ID_PARAMETER,
                    }
                ),
                "CASTLE_CHEF_REMINDERS_HANDLER": "true",
                "CASTLE_CHEF_PUSH": "apns",
                "AWS_LWA_PASS_THROUGH_PATH": "/events",
                # A failed firing fails the invocation, so Lambda retries it (the
                # adapter otherwise returns the error response as a success).
                "AWS_LWA_ERROR_STATUS_CODES": "500-599",
            },
        )
        for grant in (database_url, apns_key, apns_key_id):
            grant.grant_read(reminders)
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
        alarms = sns.Topic(self, "Alarms", display_name="Castle Chef alarms")
        alarms.add_subscription(sns_subscriptions.EmailSubscription(config.alarm_email))
        notify = cloudwatch_actions.SnsAction(alarms)
        for name, log_group in {
            "Api": api_logs,
            "Worker": worker_logs,
            "FailureHandler": failures_logs,
            "Reminders": reminders_logs,
        }.items():
            errors = logs.MetricFilter(
                self,
                f"{name}ErrorLines",
                log_group=log_group,
                filter_pattern=logs.FilterPattern.any(
                    logs.FilterPattern.string_value("$.level", "=", "error"),
                    logs.FilterPattern.string_value("$.level", "=", "critical"),
                ),
                metric_namespace=f"CastleChef/{self.stack_name}",  # per stage
                metric_name=f"{name}ErrorLines",
                metric_value="1",
            )
            self._alarm(
                f"{name}Errors",
                errors.metric(statistic="Sum", period=Duration.minutes(5)),
                f"Castle Chef {name.lower()} logged an error",
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
                f"Castle Chef {name.lower()} Lambda errors",
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
            "Castle Chef jobs pipe could not start a worker",
            notify,
        )

        for metric_name, description in {
            "TargetErrorCount": "Castle Chef reminder schedules could not invoke Reminders",
            "InvocationDroppedCount": "Castle Chef reminder schedules dropped a reminder",
        }.items():
            self._alarm(
                f"Reminders{metric_name}",
                cloudwatch.Metric(
                    namespace="AWS/Scheduler",
                    metric_name=metric_name,
                    dimensions_map={"ScheduleGroup": schedule_group.name or ""},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                description,
                notify,
            )

        self.api_url = api_url.url
        self.reminders = reminders
        self.cluster = cluster
        self.pipe_name = pipe.ref
        self.worker = worker
        self.worker_security_group = worker_sg
        self.subnets = vpc.public_subnets
        self.worker_logs = worker_logs

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
