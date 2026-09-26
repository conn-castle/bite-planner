"""What every stage shares: image repositories, the GitHub deploy role, and the
monthly budget. Deployed before the runtime stack, so images can be pushed
before anything references them."""

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"


class RepositoriesStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_subject: str,
        budget_email: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repositories = {
            name: ecr.Repository(
                self,
                f"{name.capitalize()}Repository",
                repository_name=f"castle-chef-{name}",
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                image_scan_on_push=True,
                removal_policy=RemovalPolicy.RETAIN,
                # Each prefix is counted on its own, so development pushes never
                # expire the releases rollback.yml can go back to.
                lifecycle_rules=[
                    ecr.LifecycleRule(
                        rule_priority=1,
                        tag_status=ecr.TagStatus.UNTAGGED,
                        max_image_age=Duration.days(1),
                    ),
                    ecr.LifecycleRule(rule_priority=2, tag_prefix_list=["dev-"], max_image_count=5),
                    ecr.LifecycleRule(
                        rule_priority=3, tag_prefix_list=["commit-"], max_image_count=30
                    ),
                ],
            )
            for name in ("api", "worker")
        }

        # The release and rollback workflows assume this role (GitHub OIDC; the
        # account's provider already exists). Only this repository's main branch;
        # the repository uses GitHub's immutable subject claims (owner and
        # repository ids), so a renamed or re-created repository can't match.
        provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GitHubOidc",
            f"arn:aws:iam::{self.account}:oidc-provider/{GITHUB_OIDC_HOST}",
        )
        self.deploy_role = iam.Role(
            self,
            "GitHubDeployRole",
            role_name="castle-chef-github-deploy",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{GITHUB_OIDC_HOST}:aud": "sts.amazonaws.com",
                        f"{GITHUB_OIDC_HOST}:sub": f"{github_subject}:ref:refs/heads/main",
                    }
                },
            ),
        )
        for repository in self.repositories.values():
            repository.grant_pull_push(self.deploy_role)
            # A re-run reuses the images already pushed for its release.
            repository.grant(self.deploy_role, "ecr:DescribeImages")
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        # A database restore (rollback.yml) pauses job starts and waits for
        # running workers.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["pipes:DescribePipe", "pipes:StartPipe", "pipes:StopPipe"],
                resources=[f"arn:aws:pipes:{self.region}:{self.account}:pipe/*"],
            )
        )
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks", "ecs:ListTasks"], resources=["*"]
            )
        )
        # After each deploy, the Reminders function rebuilds every member's
        # reminder schedule from the database.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:CastleChef*-Reminders"
                ],
            )
        )
        # `cdk deploy` works through the account's CDK bootstrap roles.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-{self.account}-*"],
            )
        )

        # Everything tagged project=castle-chef (the `project` cost allocation
        # tag is active in Billing). Idle cost is under $1; this catches a
        # runaway (e.g. tasks that never stop).
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="castle-chef-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=5, unit="USD"),
                cost_filters={"TagKeyValue": ["user:project$castle-chef"]},
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=notification_type,
                        comparison_operator="GREATER_THAN",
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=budget_email
                        )
                    ],
                )
                for notification_type, threshold in [("ACTUAL", 80), ("FORECASTED", 100)]
            ],
        )
