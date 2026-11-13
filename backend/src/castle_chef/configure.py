"""Generate private environment copies from canonical SSM; never print their values."""

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

import boto3
from pydantic import BaseModel, ConfigDict

from castle_chef.settings import REPO_ROOT

ROOT: Path = REPO_ROOT
PARAMETERS: dict[str, str] = {
    "AWS_ACCOUNT_ID": "/castle-chef/shared/deployment-account-id",
    "AWS_REGION": "/castle-chef/shared/deployment-region",
    "CASTLE_CHEF_GITHUB_SUBJECT": "/castle-chef/shared/github-oidc-subject",
    "CASTLE_CHEF_ALARM_EMAIL": "/castle-chef/shared/alarm-email",
    "NEON_PROJECT_ID": "/castle-chef/shared/neon-project-id",
    "NEON_DEV_BRANCH_ID": "/castle-chef/development/neon-branch-id",
    "NEON_AUTH_URL_DEV": "/castle-chef/development/neon-auth-url",
    "NEON_AUTH_URL_PROD": "/castle-chef/production/neon-auth-url",
    "API_URL_PROD": "/castle-chef/production/api-url",
    "VITE_NEON_AUTH_TRUSTED_ORIGIN": "/castle-chef/shared/neon-auth-trusted-origin",
    "APPLE_TEAM_ID": "/castle-chef/shared/apple-team-id",
    "MATCH_GIT_URL": "/castle-chef/shared/apple-signing-git-url",
}
RELEASE_SECRETS: dict[str, str] = {
    "NEON_API_KEY": "/castle-chef/shared/neon-api-key",
    "MIGRATION_DATABASE_URL": "/castle-chef/production/database-url",
    "APP_STORE_CONNECT_KEY_ID": "/castle-chef/shared/app-store-connect-key-id",
    "APP_STORE_CONNECT_ISSUER_ID": "/castle-chef/shared/app-store-connect-issuer-id",
    "APP_STORE_CONNECT_API_KEY_BASE64": "/castle-chef/shared/app-store-connect-api-key-base64",
    "MATCH_PASSWORD": "/castle-chef/shared/match-password",
    "CASTLE_CHEF_APP_CLIENT_ID": "/castle-chef/shared/github-app-client-id",
    "CASTLE_CHEF_APP_PRIVATE_KEY": "/castle-chef/shared/github-app-private-key",
}


class Ssm(Protocol):
    def get_parameters(self, *, Names: list[str], WithDecryption: bool) -> object: ...


class Parameter(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    Name: str
    Value: str


class Reply(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    Parameters: list[Parameter]
    InvalidParameters: list[str]


def read_parameters(client: Ssm, names: dict[str, str]) -> dict[str, str]:
    paths: list[str] = list(dict.fromkeys(names.values()))
    values: dict[str, str] = {}
    for start in range(0, len(paths), 10):
        reply: Reply = Reply.model_validate(
            client.get_parameters(Names=paths[start : start + 10], WithDecryption=True)
        )
        if reply.InvalidParameters:
            raise RuntimeError(
                "Missing canonical SSM parameters: " + ", ".join(reply.InvalidParameters)
            )
        values.update({parameter.Name: parameter.Value for parameter in reply.Parameters})
    missing: list[str] = [name for name in paths if name not in values or not values[name]]
    if missing:
        raise RuntimeError("Missing or empty canonical SSM parameters: " + ", ".join(missing))
    return {name: values[path] for name, path in names.items()}


def write_env(path: Path, values: dict[str, str], *, shell: bool = False) -> None:
    """Atomic mode-600 consumer file. Shell copies contain validated config, never keys."""
    import json

    lines: list[str] = ["# Generated from AWS SSM by make env. Do not commit or edit.\n"]
    for name, value in values.items():
        if shell and not re.fullmatch(r"[A-Za-z0-9_:@./?=%+\-]+", value):
            raise RuntimeError(f"{name} cannot be represented in the deployment environment file")
        lines.append(
            f"{'export ' if shell else ''}{name}={value if shell else json.dumps(value)}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.writelines(lines)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            with temporary.open("r+b") as cleanup:
                cleanup.write(bytes(temporary.stat().st_size))
                cleanup.flush()
                os.fsync(cleanup.fileno())
            temporary.unlink()


def local_files(root: Path, values: dict[str, str]) -> None:
    private: dict[str, str] = {name: values[name] for name in PARAMETERS}
    write_env(root / ".env.deployment.local", private, shell=True)
    shared: dict[str, str] = {
        "VITE_NEON_AUTH_TRUSTED_ORIGIN": values["VITE_NEON_AUTH_TRUSTED_ORIGIN"]
    }
    write_env(
        root / "apps/mobile/.env.development.local",
        shared | {"VITE_NEON_AUTH_URL": values["NEON_AUTH_URL_DEV"], "VITE_API_URL": "/api"},
    )
    write_env(
        root / "apps/mobile/.env.production.local",
        shared
        | {
            "VITE_NEON_AUTH_URL": values["NEON_AUTH_URL_PROD"],
            "VITE_API_URL": values["API_URL_PROD"],
        },
    )
    write_env(
        root / "backend/.env",
        {
            "DATABASE_URL": values["DATABASE_URL"],
            "CASTLE_CHEF_NEON_AUTH_URL": values["NEON_AUTH_URL_DEV"],
            "CASTLE_CHEF_JOB_DISPATCH": "local",
        },
    )


def github_values(values: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = dict(values)
    result["DEPLOY_ROLE"] = (
        f"arn:aws:iam::{values['AWS_ACCOUNT_ID']}:role/castle-chef-github-deploy"
    )
    parsed: SplitResult = urlsplit(values["MATCH_GIT_URL"])
    parts: list[str] = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2:
        raise RuntimeError("MATCH_GIT_URL must identify one GitHub owner/repository over HTTPS")
    result["APPLE_SIGNING_OWNER"], result["APPLE_SIGNING_REPOSITORY"] = parts
    return result


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("local", "github"))
    parser.add_argument("--repo", help="Target GitHub repository for generated secret copies")
    args: argparse.Namespace = parser.parse_args()
    names: dict[str, str] = dict(PARAMETERS)
    if args.target == "local":
        names["DATABASE_URL"] = "/castle-chef/development/database-url"
    else:
        if not args.repo:
            parser.error("github requires --repo owner/repository")
        names.update(RELEASE_SECRETS)
    client: Ssm = boto3.client("ssm")
    values: dict[str, str] = read_parameters(client, names)
    if args.target == "local":
        local_files(ROOT, values)
        print("Generated private local deployment, frontend and backend environment files.")
    else:
        projected: dict[str, str] = github_values(values)
        for name, value in projected.items():
            subprocess.run(
                ["gh", "secret", "set", name, "--repo", args.repo],
                input=value,
                text=True,
                check=True,
            )
        print(f"Synchronized {len(projected)} GitHub secret copies from SSM.")


if __name__ == "__main__":
    main()
