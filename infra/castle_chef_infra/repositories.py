"""Image repositories and the GitHub deploy role. Deployed once, before the
runtime stack, so images can be pushed before anything references them."""

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"


class RepositoriesStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, github_repo: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repositories = {
            name: ecr.Repository(
                self,
                f"{name.capitalize()}Repository",
                repository_name=f"castle-chef-{name}",
                image_tag_mutability=ecr.TagMutability.IMMUTABLE,
                image_scan_on_push=True,
                removal_policy=RemovalPolicy.RETAIN,
                # Keeps enough releases to roll back to.
                lifecycle_rules=[ecr.LifecycleRule(max_image_count=30)],
            )
            for name in ("api", "worker")
        }

        # The release and rollback workflows assume this role (GitHub OIDC; the
        # account's provider already exists). Only this repository's main branch.
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
                        f"{GITHUB_OIDC_HOST}:sub": f"repo:{github_repo}:ref:refs/heads/main",
                    }
                },
            ),
        )
        for repository in self.repositories.values():
            repository.grant_pull_push(self.deploy_role)
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        # `cdk deploy` works through the account's CDK bootstrap roles.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-{self.account}-*"],
            )
        )
