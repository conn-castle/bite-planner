"""Private configuration is generated from canonical SSM without disclosure."""

import subprocess
import sys
from pathlib import Path

import boto3
import pytest
from dotenv import dotenv_values

from bite_planner import configure


class SsmStandIn:
    def __init__(self, values: dict[str, str]) -> None:
        self.values: dict[str, str] = values
        self.requests: list[list[str]] = []

    def get_parameters(self, *, Names: list[str], WithDecryption: bool) -> object:
        assert WithDecryption
        self.requests.append(Names)
        return {
            "Parameters": [
                {"Name": name, "Value": self.values[name]} for name in Names if name in self.values
            ],
            "InvalidParameters": [name for name in Names if name not in self.values],
        }


@pytest.fixture
def configuration() -> dict[str, str]:
    return {
        "AWS_ACCOUNT_ID": "123456789012",
        "AWS_REGION": "us-east-1",
        "BITE_PLANNER_GITHUB_SUBJECT": "repo:example-owner@1/example-repo@2",
        "BITE_PLANNER_ALARM_EMAIL": "operator@example.com",
        "NEON_PROJECT_ID": "example-project",
        "NEON_DEV_BRANCH_ID": "example-development-branch",
        "NEON_AUTH_URL_DEV": "https://dev-auth.example.test/neondb/auth",
        "NEON_AUTH_URL_PROD": "https://auth.example.test/neondb/auth",
        "API_URL_PROD": "https://api.example.test",
        "VITE_NEON_AUTH_TRUSTED_ORIGIN": "https://app.example.test",
        "APPLE_TEAM_ID": "EXAMP12345",
        "MATCH_GIT_URL": "https://github.com/example-owner/private-certificates.git",
        "DATABASE_URL": "postgresql://example:synthetic-password@localhost/example",
    }


@pytest.fixture
def canonical_parameters(configuration: dict[str, str]) -> dict[str, str]:
    # External SSM contract, independent of the loader's lookup table.
    return {
        "/bite-planner/shared/deployment-account-id": configuration["AWS_ACCOUNT_ID"],
        "/bite-planner/shared/deployment-region": configuration["AWS_REGION"],
        "/bite-planner/shared/github-oidc-subject": configuration["BITE_PLANNER_GITHUB_SUBJECT"],
        "/bite-planner/shared/alarm-email": configuration["BITE_PLANNER_ALARM_EMAIL"],
        "/bite-planner/shared/neon-project-id": configuration["NEON_PROJECT_ID"],
        "/bite-planner/development/neon-branch-id": configuration["NEON_DEV_BRANCH_ID"],
        "/bite-planner/development/neon-auth-url": configuration["NEON_AUTH_URL_DEV"],
        "/bite-planner/production/neon-auth-url": configuration["NEON_AUTH_URL_PROD"],
        "/bite-planner/production/api-url": configuration["API_URL_PROD"],
        "/bite-planner/shared/neon-auth-trusted-origin": configuration[
            "VITE_NEON_AUTH_TRUSTED_ORIGIN"
        ],
        "/bite-planner/shared/apple-team-id": configuration["APPLE_TEAM_ID"],
        "/bite-planner/shared/apple-signing-git-url": configuration["MATCH_GIT_URL"],
        "/bite-planner/development/database-url": configuration["DATABASE_URL"],
        "/bite-planner/production/database-url": "postgresql://example:synthetic@localhost/production",
        "/bite-planner/shared/neon-api-key": "synthetic-neon-api-key",
        "/bite-planner/shared/app-store-connect-key-id": "EXAMPLEKEY",
        "/bite-planner/shared/app-store-connect-issuer-id": "synthetic-issuer",
        "/bite-planner/shared/app-store-connect-api-key-base64": "synthetic-key-base64",
        "/bite-planner/shared/match-password": "synthetic-match-password",
        "/bite-planner/shared/github-app-client-id": "synthetic-client-id",
        "/bite-planner/shared/github-app-private-key": "synthetic-private-key",
    }


def test_local_setup_generates_private_consumers_without_printing_values(
    configuration: dict[str, str],
    canonical_parameters: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canonical_parameters.pop("/bite-planner/production/api-url")
    ssm = SsmStandIn(canonical_parameters)
    monkeypatch.setattr(boto3, "client", lambda service: ssm)
    monkeypatch.setattr(configure, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["configure", "local"])

    configure.main()

    development = dotenv_values(tmp_path / "apps/mobile/.env.development.local")
    backend = dotenv_values(tmp_path / "backend/.env")
    assert development["VITE_NEON_AUTH_URL"] == configuration["NEON_AUTH_URL_DEV"]
    assert backend["BITE_PLANNER_NEON_AUTH_URL"] == development["VITE_NEON_AUTH_URL"]
    assert not (tmp_path / "apps/mobile/.env.production.local").exists()
    assert backend["DATABASE_URL"] == configuration["DATABASE_URL"]
    assert backend["BITE_PLANNER_JOB_DISPATCH"] == "local"
    assert development["VITE_API_URL"] == "/api"
    for path in tmp_path.rglob("*.local"):
        assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "backend/.env").stat().st_mode & 0o777 == 0o600
    assert max(map(len, ssm.requests)) <= 10
    output = capsys.readouterr()
    assert "Generated private deployment" in output.out and not output.err
    assert not any(value in output.out for value in configuration.values())
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s" "$BITE_PLANNER_ALARM_EMAIL"',
            "bash",
            str(tmp_path / ".env.deployment.local"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == configuration["BITE_PLANNER_ALARM_EMAIL"]


def test_missing_canonical_parameter_fails_without_writing_local_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssm = SsmStandIn({})
    monkeypatch.setattr(boto3, "client", lambda service: ssm)
    monkeypatch.setattr(configure, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["configure", "local"])
    with pytest.raises(RuntimeError, match="Missing canonical SSM"):
        configure.main()
    assert not list(tmp_path.iterdir())


def test_signing_and_deploy_secret_views_derive_from_canonical_values(
    configuration: dict[str, str],
) -> None:
    values = configure.github_values(configuration)
    assert values["DEPLOY_ROLE"] == "arn:aws:iam::123456789012:role/bite-planner-github-deploy"
    assert values["APPLE_SIGNING_OWNER"] == "example-owner"
    assert values["APPLE_SIGNING_REPOSITORY"] == "private-certificates"
    assert values["MATCH_GIT_URL"] == configuration["MATCH_GIT_URL"]


def test_github_copies_pass_values_only_over_stdin(
    configuration: dict[str, str],
    canonical_parameters: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssm = SsmStandIn(canonical_parameters)
    monkeypatch.setattr(boto3, "client", lambda service: ssm)
    monkeypatch.setattr(
        sys, "argv", ["configure", "github", "--repo", "example-owner/example-repo"]
    )
    copied: dict[str, str] = {}

    def run(command: list[str], *, input: str, text: bool, check: bool) -> None:
        assert command[:3] == ["gh", "secret", "set"] and text and check
        assert command[-2:] == ["--repo", "example-owner/example-repo"]
        assert input not in command
        copied[command[3]] = input

    monkeypatch.setattr(subprocess, "run", run)
    configure.main()
    assert copied["APPLE_TEAM_ID"] == configuration["APPLE_TEAM_ID"]
    assert copied["MATCH_GIT_URL"] == configuration["MATCH_GIT_URL"]
    assert copied["BITE_PLANNER_APP_PRIVATE_KEY"] == "synthetic-private-key"
    assert copied["MIGRATION_DATABASE_URL"] == "postgresql://example:synthetic@localhost/production"


@pytest.mark.parametrize("value", ["$(touch should-not-exist)", "https://example.test/?a=b&c=d"])
def test_shell_environment_rejects_commands_before_creating_a_file(
    tmp_path: Path, value: str
) -> None:
    target = tmp_path / ".env.deployment.local"
    with pytest.raises(RuntimeError, match="cannot be represented"):
        configure.write_env(target, {"VALUE": value}, shell=True)
    assert not target.exists()
    assert not (tmp_path / "should-not-exist").exists()


def test_production_configuration_writes_only_production_consumer(
    configuration: dict[str, str],
    canonical_parameters: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boto3, "client", lambda service: SsmStandIn(canonical_parameters))
    monkeypatch.setattr(configure, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["configure", "production"])
    configure.main()
    path = tmp_path / "apps/mobile/.env.production.local"
    production = dotenv_values(path)
    assert production["VITE_NEON_AUTH_URL"] == configuration["NEON_AUTH_URL_PROD"]
    assert production["VITE_API_URL"] == configuration["API_URL_PROD"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "backend/.env").exists()
    assert not (tmp_path / ".env.deployment.local").exists()
