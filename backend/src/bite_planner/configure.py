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

from bite_planner.settings import REPO_ROOT

ROOT: Path = REPO_ROOT
PARAMETERS: dict[str, str] = {
    "AWS_ACCOUNT_ID": "/bite-planner/shared/deployment-account-id",
    "AWS_REGION": "/bite-planner/shared/deployment-region",
    "BITE_PLANNER_GITHUB_SUBJECT": "/bite-planner/shared/github-oidc-subject",
    "BITE_PLANNER_ALARM_EMAIL": "/bite-planner/shared/alarm-email",
    "NEON_PROJECT_ID": "/bite-planner/shared/neon-project-id",
    "NEON_DEV_BRANCH_ID": "/bite-planner/development/neon-branch-id",
    "NEON_AUTH_URL_DEV": "/bite-planner/development/neon-auth-url",
    "NEON_AUTH_URL_PROD": "/bite-planner/production/neon-auth-url",
    "API_URL_PROD": "/bite-planner/production/api-url",
    "VITE_NEON_AUTH_TRUSTED_ORIGIN": "/bite-planner/shared/neon-auth-trusted-origin",
    "APPLE_TEAM_ID": "/bite-planner/shared/apple-team-id",
    "MATCH_GIT_URL": "/bite-planner/shared/apple-signing-git-url",
}
RELEASE_SECRETS: dict[str, str] = {
    "NEON_API_KEY": "/bite-planner/shared/neon-api-key",
    "MIGRATION_DATABASE_URL": "/bite-planner/production/database-url",
    "APP_STORE_CONNECT_KEY_ID": "/bite-planner/shared/app-store-connect-key-id",
    "APP_STORE_CONNECT_ISSUER_ID": "/bite-planner/shared/app-store-connect-issuer-id",
    "APP_STORE_CONNECT_API_KEY_BASE64": "/bite-planner/shared/app-store-connect-api-key-base64",
    "MATCH_PASSWORD": "/bite-planner/shared/match-password",
    "BITE_PLANNER_APP_CLIENT_ID": "/bite-planner/shared/github-app-client-id",
    "BITE_PLANNER_APP_PRIVATE_KEY": "/bite-planner/shared/github-app-private-key",
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
    private: dict[str, str] = {name: values[name] for name in PARAMETERS if name != "API_URL_PROD"}
    # Boto3 reads only AWS_DEFAULT_REGION; the AWS CLI and CDK read AWS_REGION. Both
    # come from the one canonical deployment region.
    private["AWS_DEFAULT_REGION"] = values["AWS_REGION"]
    write_env(root / ".env.deployment.local", private, shell=True)
    shared: dict[str, str] = {
        "VITE_NEON_AUTH_TRUSTED_ORIGIN": values["VITE_NEON_AUTH_TRUSTED_ORIGIN"]
    }
    write_env(
        root / "apps/mobile/.env.development.local",
        shared | {"VITE_NEON_AUTH_URL": values["NEON_AUTH_URL_DEV"], "VITE_API_URL": "/api"},
    )
    write_env(
        root / "backend/.env",
        {
            "DATABASE_URL": values["DATABASE_URL"],
            "BITE_PLANNER_NEON_AUTH_URL": values["NEON_AUTH_URL_DEV"],
            "BITE_PLANNER_JOB_DISPATCH": "local",
        },
    )


def production_file(root: Path, values: dict[str, str]) -> None:
    write_env(
        root / "apps/mobile/.env.production.local",
        {
            "VITE_NEON_AUTH_TRUSTED_ORIGIN": values["VITE_NEON_AUTH_TRUSTED_ORIGIN"],
            "VITE_NEON_AUTH_URL": values["NEON_AUTH_URL_PROD"],
            "VITE_API_URL": values["API_URL_PROD"],
        },
    )


def github_values(values: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = dict(values)
    result["DEPLOY_ROLE"] = (
        f"arn:aws:iam::{values['AWS_ACCOUNT_ID']}:role/bite-planner-github-deploy"
    )
    parsed: SplitResult = urlsplit(values["MATCH_GIT_URL"])
    parts: list[str] = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(parts) != 2:
        raise RuntimeError("MATCH_GIT_URL must identify one GitHub owner/repository over HTTPS")
    result["APPLE_SIGNING_OWNER"], result["APPLE_SIGNING_REPOSITORY"] = parts
    return result


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("local", "production", "github"))
    parser.add_argument("--repo", help="Target GitHub repository for generated secret copies")
    args: argparse.Namespace = parser.parse_args()
    names: dict[str, str] = dict(PARAMETERS)
    if args.target == "local":
        del names["API_URL_PROD"]
        names["DATABASE_URL"] = "/bite-planner/development/database-url"
    elif args.target == "github":
        if not args.repo:
            parser.error("github requires --repo owner/repository")
        names.update(RELEASE_SECRETS)
    client: Ssm = boto3.client("ssm")
    values: dict[str, str] = read_parameters(client, names)
    if args.target == "local":
        local_files(ROOT, values)
        print("Generated private deployment and development environment files.")
    elif args.target == "production":
        production_file(ROOT, values)
        print("Generated private production frontend environment file.")
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
