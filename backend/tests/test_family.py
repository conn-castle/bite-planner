import json
import stat
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner

from castle_chef.cli import app
from castle_chef.models import HouseholdMember

runner = CliRunner()
FAKE_NEONCTL = Path(__file__).parent / "fake_neonctl.py"


@pytest.fixture(autouse=True)
def fake_neonctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "neonctl"
    script.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_NEONCTL} "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin")
    monkeypatch.setenv("CASTLE_CHEF_NEON_PROJECT_ID", "proj-test")
    monkeypatch.setenv("CASTLE_CHEF_NEON_BRANCH_ID", "br-test")
    log = tmp_path / "neonctl.log"
    monkeypatch.setenv("FAKE_NEONCTL_LOG", str(log))
    return log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_invite_creates_account_and_adds_member(session: Session, fake_neonctl: Path) -> None:
    result = runner.invoke(app, ["family", "invite", "mom@example.com", "--name", "Mom"])

    assert result.exit_code == 0, result.output
    assert [call[:4] for call in calls(fake_neonctl)] == [
        ["api", "/projects/proj-test/branches/br-test/auth/users", "-X", "POST"]
    ]
    listed = runner.invoke(app, ["family", "list"])
    assert listed.stdout == "mom@example.com\tMom\n"
    assert len(session.exec(select(HouseholdMember)).all()) == 1


def test_inviting_an_existing_account_only_adds_membership(
    session: Session, fake_neonctl: Path
) -> None:
    session.execute(
        text("""INSERT INTO neon_auth."user" (email, name) VALUES ('kid@example.com', 'Kid')""")
    )
    session.commit()

    first = runner.invoke(app, ["family", "invite", "KID@example.com", "--name", "Kid"])
    again = runner.invoke(app, ["family", "invite", "kid@example.com", "--name", "Kid"])

    assert (first.exit_code, again.exit_code) == (0, 0), first.output + again.output
    assert calls(fake_neonctl) == []
    assert len(session.exec(select(HouseholdMember)).all()) == 1


def test_remove_deletes_membership_and_account(session: Session, fake_neonctl: Path) -> None:
    runner.invoke(app, ["family", "invite", "mom@example.com", "--name", "Mom"])

    result = runner.invoke(app, ["family", "remove", "mom@example.com"])

    assert result.exit_code == 0, result.output
    assert calls(fake_neonctl)[-1][3] == "DELETE"
    assert session.exec(select(HouseholdMember)).all() == []
    assert runner.invoke(app, ["family", "list"]).stdout == ""


def test_invite_fails_loudly_when_neon_rejects_it(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NEONCTL_FAIL", "1")

    result = runner.invoke(app, ["family", "invite", "mom@example.com", "--name", "Mom"])

    assert result.exit_code != 0
    assert "simulated Neon failure" in str(result.exception)
    assert session.exec(select(HouseholdMember)).all() == []


def test_invite_requires_a_name(fake_neonctl: Path) -> None:
    result = runner.invoke(app, ["family", "invite", "mom@example.com"])

    assert result.exit_code == 2  # usage error: missing required option
    assert calls(fake_neonctl) == []
