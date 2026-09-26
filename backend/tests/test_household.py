"""`castle-chef household`, through the CLI. Neon Auth account calls go to a
fake `neonctl` that applies them to the test database."""

import json
import stat
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner, Result

from castle_chef import push
from castle_chef.cli import app
from castle_chef.models import AppUser, GroceryOrder, GroceryOrderStatus, Household, PushDevice

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


@pytest.fixture(autouse=True)
def households() -> None:
    for name in ("Conn Castle", "Test Household"):
        assert run("household", "create", name).exit_code == 0


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def membership(session: Session) -> dict[str, str | None]:
    """Each member's email and household name."""
    session.expire_all()
    rows = session.execute(
        text(
            'SELECT u.email, h.name FROM app_user AS m JOIN neon_auth."user" AS u '
            "ON u.id = m.user_id LEFT JOIN household AS h ON h.id = m.household_id"
        )
    ).all()
    return {row.email: row.name for row in rows}


def add_account(session: Session, email: str, name: str) -> None:
    session.execute(
        text('INSERT INTO neon_auth."user" (email, name) VALUES (:email, :name)'),
        {"email": email, "name": name},
    )
    session.commit()


def test_households_are_created_once_by_name() -> None:
    again = run("household", "create", "Conn Castle")

    assert again.exit_code == 1
    assert "A household named 'Conn Castle' already exists." in again.stderr
    assert run("household", "list").stdout == "Conn Castle\t0\t0\nTest Household\t0\t0\n"


def test_list_counts_each_households_members_and_orders(session: Session) -> None:
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")
    run("household", "invite", "dad@example.com", "--name", "Dad", "--household", "Conn Castle")
    run("household", "invite", "me@example.com", "--name", "Me", "--household", "Test Household")
    house = session.exec(select(Household.id).where(Household.name == "Test Household")).one()
    # A placed order and the current draft.
    member = uuid4()
    session.add(
        GroceryOrder(
            household_id=house,
            created_by=member,
            status=GroceryOrderStatus.PLACED,
            placed_at=datetime(2026, 9, 25, 14, tzinfo=UTC),
            placed_by=member,
            delivery_date=date(2026, 9, 27),
            next_delivery_date=date(2026, 10, 1),
        )
    )
    session.add(GroceryOrder(household_id=house, created_by=uuid4()))
    session.commit()

    assert run("household", "list").stdout == "Conn Castle\t2\t0\nTest Household\t1\t2\n"


def test_invite_creates_account_and_adds_it_to_the_household(
    session: Session, fake_neonctl: Path
) -> None:
    result = run(
        "household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle"
    )

    assert result.exit_code == 0, result.output
    assert [call[:4] for call in calls(fake_neonctl)] == [
        ["api", "/projects/proj-test/branches/br-test/auth/users", "-X", "POST"]
    ]
    assert run("household", "members", "Conn Castle").stdout == "mom@example.com\tMom\n"
    assert run("household", "members", "Test Household").stdout == ""
    assert membership(session) == {"mom@example.com": "Conn Castle"}


def test_inviting_an_existing_account_only_adds_membership(
    session: Session, fake_neonctl: Path
) -> None:
    add_account(session, "kid@example.com", "Kid")

    first = run(
        "household", "invite", "KID@example.com", "--name", "Kid", "--household", "Conn Castle"
    )
    again = run(
        "household", "invite", "kid@example.com", "--name", "Kid", "--household", "Conn Castle"
    )

    assert (first.exit_code, again.exit_code) == (0, 0), first.output + again.output
    assert calls(fake_neonctl) == []
    assert membership(session) == {"kid@example.com": "Conn Castle"}


def test_someone_in_another_household_is_not_moved(session: Session, fake_neonctl: Path) -> None:
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")

    result = run(
        "household", "invite", "mom@example.com", "--name", "Mom", "--household", "Test Household"
    )

    assert result.exit_code == 1
    assert "already in the household 'Conn Castle'" in result.stderr
    assert membership(session) == {"mom@example.com": "Conn Castle"}
    assert len(calls(fake_neonctl)) == 1  # only the first invite's account


@pytest.mark.parametrize(
    "command",
    [
        ["household", "invite", "mom@example.com", "--name", "Mom", "--household", "Nowhere"],
        ["household", "members", "Nowhere"],
    ],
)
def test_unknown_households_fail_clearly(
    session: Session, fake_neonctl: Path, command: list[str]
) -> None:
    result = run(*command)

    assert result.exit_code == 1
    assert "No household named 'Nowhere'" in result.stderr
    assert calls(fake_neonctl) == []
    assert membership(session) == {}


def test_remove_deletes_membership_and_account(session: Session, fake_neonctl: Path) -> None:
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")

    result = run("household", "remove", "mom@example.com")

    assert result.exit_code == 0, result.output
    assert calls(fake_neonctl)[-1][3] == "DELETE"
    assert session.exec(select(AppUser)).all() == []
    assert run("household", "members", "Conn Castle").stdout == ""


def test_notify_test_reaches_a_members_phones(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")
    [mom] = session.exec(select(AppUser)).all()
    session.add(PushDevice(token="ab" * 32, user_id=mom.user_id))
    session.commit()
    sent: list[tuple[str, str]] = []

    class Recording:
        def send(self, token: str, notification: push.Notification) -> push.Sent:
            sent.append((token, notification.title))
            return push.Sent(push.Outcome.SENT)

    monkeypatch.setattr(push, "get_sender", Recording)

    result = run("household", "notify-test", "mom@example.com")

    assert result.exit_code == 0, result.output
    assert sent == [("ab" * 32, "Test notification")]
    assert (
        "nobody@example.com isn't in a household."
        in run("household", "notify-test", "nobody@example.com").stderr
    )


def test_removing_someone_forgets_their_phones(session: Session) -> None:
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")
    [mom] = session.exec(select(AppUser)).all()
    session.add(PushDevice(token="ab" * 32, user_id=mom.user_id))
    session.commit()

    assert run("household", "remove", "mom@example.com").exit_code == 0

    session.expire_all()
    assert session.exec(select(PushDevice)).all() == []


def test_removing_an_unknown_account_fails_clearly(fake_neonctl: Path) -> None:
    result = run("household", "remove", "nobody@example.com")

    assert result.exit_code == 1
    assert "No account for nobody@example.com." in result.stderr
    assert calls(fake_neonctl) == []


def test_invite_fails_loudly_when_neon_rejects_it(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NEONCTL_FAIL", "1")

    result = run(
        "household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle"
    )

    assert result.exit_code != 0
    assert "simulated Neon failure" in str(result.exception)
    assert session.exec(select(AppUser)).all() == []


@pytest.mark.parametrize("missing", ["--name", "--household"])
def test_invite_requires_a_name_and_a_household(fake_neonctl: Path, missing: str) -> None:
    options = {"--name": "Mom", "--household": "Conn Castle"}
    del options[missing]

    result = run("household", "invite", "mom@example.com", *sum(options.items(), ()))

    assert result.exit_code == 2  # usage error: missing required option
    assert calls(fake_neonctl) == []
