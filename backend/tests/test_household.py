"""`bite-planner household`, through the CLI: the admin's way to make households,
invite people (they join on their first sign-in, as when a member invites them
from the app), and remove them."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from typer.testing import CliRunner, Result

from bite_planner import household, push
from bite_planner.cli import app
from bite_planner.models import (
    BookState,
    GroceryOrder,
    GroceryOrderStatus,
    Household,
    HouseholdInvite,
    PushDevice,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def households() -> None:
    for name in ("Conn Castle", "Test Household"):
        assert run("household", "create", name).exit_code == 0


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


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


def add_account(session: Session, email: str, name: str) -> UUID:
    """An account, as signing in with an emailed code makes one."""
    user_id: UUID = session.execute(
        text('INSERT INTO neon_auth."user" (email, name) VALUES (:email, :name) RETURNING id'),
        {"email": email, "name": name},
    ).scalar_one()
    session.commit()
    return user_id


def joined(session: Session, email: str, name: str, household_name: str) -> UUID:
    """Invited, then signed in for the first time: a member."""
    assert (
        run("household", "invite", email, "--name", name, "--household", household_name).exit_code
        == 0
    )
    user_id = add_account(session, email, name)
    assert household.join_if_invited(session, user_id) is not None
    return user_id


def test_households_are_created_once_by_name() -> None:
    again = run("household", "create", "Conn Castle")

    assert again.exit_code == 1
    assert "A household named 'Conn Castle' already exists." in again.stderr
    assert run("household", "list").stdout == "Conn Castle\t0\t0\nTest Household\t0\t0\n"


def test_list_counts_each_households_members_and_orders(session: Session) -> None:
    joined(session, "mom@example.com", "Mom", "Conn Castle")
    joined(session, "dad@example.com", "Dad", "Conn Castle")
    joined(session, "me@example.com", "Me", "Test Household")
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
            book_state=BookState.BOOKED,
        )
    )
    session.add(GroceryOrder(household_id=house, created_by=uuid4()))
    session.commit()

    assert run("household", "list").stdout == "Conn Castle\t2\t0\nTest Household\t1\t2\n"


def test_invite_records_an_invitation_and_they_join_on_first_sign_in(session: Session) -> None:
    result = run(
        "household", "invite", "Mom@Example.com", "--name", "Mom", "--household", "Conn Castle"
    )

    assert result.exit_code == 0, result.output
    # No account yet: they're invited.
    assert membership(session) == {}
    assert run("household", "members", "Conn Castle").stdout == "Mom@Example.com\tMom\tinvited\n"
    assert household.may_sign_up(session, "mom@example.com")
    # Their emailed code makes the account; their first sign-in joins them.
    mom = add_account(session, "mom@example.com", "Mom")
    assert household.join_if_invited(session, mom) is not None
    assert membership(session) == {"mom@example.com": "Conn Castle"}
    assert run("household", "members", "Conn Castle").stdout == "mom@example.com\tMom\n"
    # The invitation is used up: no other account can be made with it.
    assert not household.may_sign_up(session, "mom@example.com")


def test_inviting_the_same_email_again_only_renames_the_invitation(session: Session) -> None:
    run("household", "invite", "kid@example.com", "--name", "Kid", "--household", "Conn Castle")

    again = run(
        "household", "invite", "KID@example.com", "--name", "Sam", "--household", "Conn Castle"
    )

    assert again.exit_code == 0, again.output
    [invited] = session.exec(select(HouseholdInvite)).all()
    assert (invited.email, invited.name) == ("kid@example.com", "Sam")


def test_someone_in_or_invited_to_another_household_is_not_moved(session: Session) -> None:
    joined(session, "mom@example.com", "Mom", "Conn Castle")
    run("household", "invite", "dad@example.com", "--name", "Dad", "--household", "Conn Castle")

    member = run(
        "household", "invite", "mom@example.com", "--name", "Mom", "--household", "Test Household"
    )
    invited = run(
        "household", "invite", "dad@example.com", "--name", "Dad", "--household", "Test Household"
    )
    again = run(
        "household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle"
    )

    assert (member.exit_code, invited.exit_code, again.exit_code) == (1, 1, 1)
    assert "mom@example.com is in another household." in member.stderr
    assert "dad@example.com is invited to another household." in invited.stderr
    assert "mom@example.com is already in this household." in again.stderr
    assert membership(session) == {"mom@example.com": "Conn Castle"}


@pytest.mark.parametrize(
    ("email", "name", "said"),
    [
        ("mom", "Mom", "mom isn't an email address."),
        ("mom@example.com", " ", "Give them a name"),
    ],
)
def test_an_invitation_needs_an_email_and_a_name(email: str, name: str, said: str) -> None:
    result = run("household", "invite", email, "--name", name, "--household", "Conn Castle")

    assert result.exit_code == 1
    assert said in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        ["household", "invite", "mom@example.com", "--name", "Mom", "--household", "Nowhere"],
        ["household", "members", "Nowhere"],
    ],
)
def test_unknown_households_fail_clearly(session: Session, command: list[str]) -> None:
    result = run(*command)

    assert result.exit_code == 1
    assert "No household named 'Nowhere'" in result.stderr
    assert session.exec(select(HouseholdInvite)).all() == []


def test_remove_ends_the_membership_and_keeps_the_account(session: Session) -> None:
    mom = joined(session, "mom@example.com", "Mom", "Conn Castle")
    joined(session, "dad@example.com", "Dad", "Conn Castle")
    session.add(PushDevice(token="ab" * 32, user_id=mom))
    session.commit()

    result = run("household", "remove", "mom@example.com")

    assert result.exit_code == 0, result.output
    assert membership(session) == {"dad@example.com": "Conn Castle"}
    # Her phones stop getting the household's notifications; her account stays.
    assert session.exec(select(PushDevice)).all() == []
    assert session.execute(text('SELECT count(*) FROM neon_auth."user"')).scalar_one() == 2
    # Invited again, she's back on her next sign-in.
    run("household", "invite", "mom@example.com", "--name", "Mom", "--household", "Conn Castle")
    assert household.join_if_invited(session, mom) is not None


def test_the_last_member_cant_be_removed(session: Session) -> None:
    joined(session, "mom@example.com", "Mom", "Conn Castle")

    result = run("household", "remove", "mom@example.com")

    assert result.exit_code == 1
    assert "only member" in result.stderr
    assert membership(session) == {"mom@example.com": "Conn Castle"}


def test_removing_someone_not_in_a_household_fails_clearly(session: Session) -> None:
    add_account(session, "stranger@example.com", "Stranger")

    for email in ("stranger@example.com", "nobody@example.com"):
        result = run("household", "remove", email)
        assert result.exit_code == 1
        assert f"{email} isn't in a household." in result.stderr


def test_notify_test_reaches_a_members_phones(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    mom = joined(session, "mom@example.com", "Mom", "Conn Castle")
    session.add(PushDevice(token="ab" * 32, user_id=mom))
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


@pytest.mark.parametrize("missing", ["--name", "--household"])
def test_invite_requires_a_name_and_a_household(session: Session, missing: str) -> None:
    options = {"--name": "Mom", "--household": "Conn Castle"}
    del options[missing]

    result = run("household", "invite", "mom@example.com", *sum(options.items(), ()))

    assert result.exit_code == 2  # usage error: missing required option
    assert session.exec(select(HouseholdInvite)).all() == []
