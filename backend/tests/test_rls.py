"""What a signed-in app user can do through the Data API (`authenticated` role)."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Thread
from uuid import UUID, uuid4

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlmodel import Session

from castle_chef.db import get_engine
from castle_chef.jobs import GENERATE_MEALS, create_job

ALICE = uuid4()  # an invited household member
STRANGER = uuid4()  # has an account but was never invited


@pytest.fixture(autouse=True)
def household() -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("INSERT INTO household_member (user_id) VALUES (:id)"), {"id": ALICE}
        )


def become(connection: Connection, role: str, user_id: UUID | None) -> None:
    connection.execute(text(f"SET LOCAL ROLE {role}"))
    if user_id:
        connection.execute(text(f"SET LOCAL test.user_id = '{user_id}'"))


@contextmanager
def as_role(role: str, user_id: UUID | None = None) -> Iterator[Connection]:
    with get_engine().connect() as connection, connection.begin() as transaction:
        become(connection, role, user_id)
        yield connection
        transaction.rollback()


def test_household_member_sees_jobs_requested_by_others(session: Session) -> None:
    job = create_job(session, GENERATE_MEALS, {"count": 2})

    with as_role("authenticated", ALICE) as app:
        ids = app.execute(text("SELECT id FROM job")).scalars().all()

    assert ids == [job.id]


def test_member_can_request_meals_as_themselves() -> None:
    with as_role("authenticated", ALICE) as app:
        row = app.execute(
            text(
                "INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 3}') "
                "RETURNING status, requested_by"
            )
        ).one()

    assert (row.status, row.requested_by) == ("pending", ALICE)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO job (kind, params, status) VALUES ('generate_meals', '{}', 'succeeded')",
        "INSERT INTO job (kind, params) VALUES ('something_else', '{}')",
        "INSERT INTO meal (id, name, servings, steps) VALUES (gen_random_uuid(), 'x', 1, '[]')",
        "UPDATE job SET status = 'failed'",
        "DELETE FROM meal",
    ],
)
def test_member_cannot_write_anything_else(statement: str) -> None:
    with pytest.raises(ProgrammingError) as error, as_role("authenticated", ALICE) as app:
        app.execute(text(statement))
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_anonymous_cannot_read_meals() -> None:
    with pytest.raises(ProgrammingError) as error, as_role("anonymous") as app:
        app.execute(text("SELECT * FROM meal"))
    assert isinstance(error.value.orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    "params",
    [
        "{}",
        '{"x": 1}',
        '{"count": 0}',
        '{"count": 15}',
        '{"count": 2.5}',
        '{"count": "3"}',
        '{"count": 3, "x": 1}',
    ],
)
def test_member_cannot_request_invalid_meal_counts(params: str) -> None:
    with pytest.raises(IntegrityError), as_role("authenticated", ALICE) as app:
        app.execute(
            text("INSERT INTO job (kind, params) VALUES ('generate_meals', CAST(:p AS jsonb))"),
            {"p": params},
        )


def test_member_can_have_at_most_three_active_requests() -> None:
    insert = text("INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')")
    with pytest.raises(ProgrammingError) as error, as_role("authenticated", ALICE) as app:
        for _ in range(3):
            app.execute(insert)
        app.execute(insert)
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_uninvited_account_sees_nothing_and_cannot_request(session: Session) -> None:
    create_job(session, GENERATE_MEALS, {"count": 2})

    with as_role("authenticated", STRANGER) as app:
        assert app.execute(text("SELECT id FROM job")).all() == []
        assert app.execute(text("SELECT user_id FROM household_member")).all() == []

    with pytest.raises(ProgrammingError) as error, as_role("authenticated", STRANGER) as app:
        app.execute(
            text("INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')")
        )
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_member_sees_only_their_own_membership() -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("INSERT INTO household_member (user_id) VALUES (:id)"), {"id": uuid4()}
        )

    with as_role("authenticated", ALICE) as app:
        assert app.execute(text("SELECT user_id FROM household_member")).scalars().all() == [ALICE]


def test_member_can_send_logs_but_not_read_them() -> None:
    with as_role("authenticated", ALICE) as app:
        app.execute(
            text(
                "INSERT INTO client_log_batch (device, entries) "
                """VALUES ('{"platform": "ios"}', '[{"event": "app.start"}]')"""
            )
        )

    with pytest.raises(ProgrammingError) as error, as_role("authenticated", ALICE) as app:
        app.execute(text("SELECT * FROM client_log_batch"))
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_uninvited_account_cannot_send_logs() -> None:
    with pytest.raises(ProgrammingError) as error, as_role("authenticated", STRANGER) as app:
        app.execute(text("INSERT INTO client_log_batch (device, entries) VALUES ('{}', '[]')"))
    assert isinstance(error.value.orig, InsufficientPrivilege)


REQUEST_ONE_MEAL = text(
    "INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')"
)


def test_simultaneous_requests_cannot_exceed_the_limit() -> None:
    with get_engine().begin() as owner:
        for _ in range(2):
            owner.execute(
                text(
                    "INSERT INTO job (kind, params, requested_by) "
                    """VALUES ('generate_meals', '{"count": 1}', :id)"""
                ),
                {"id": ALICE},
            )
    outcome: dict[str, Exception] = {}

    def request_from_second_device() -> None:
        with get_engine().connect() as second, second.begin():
            become(second, "authenticated", ALICE)
            try:
                second.execute(REQUEST_ONE_MEAL)
            except ProgrammingError as error:
                outcome["error"] = error

    with get_engine().connect() as first:
        transaction = first.begin()
        become(first, "authenticated", ALICE)
        first.execute(REQUEST_ONE_MEAL)  # the third request, not committed yet
        second = Thread(target=request_from_second_device)
        second.start()
        second.join(timeout=1)
        assert second.is_alive(), "the second request should wait for the first"
        transaction.commit()
        second.join(timeout=10)

    assert isinstance(outcome["error"].orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO job_meal (job_id, meal_id) VALUES (gen_random_uuid(), gen_random_uuid())",
        "INSERT INTO meal_ingredient (id, meal_id, position, name) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 0, 'x')",
        "INSERT INTO household_member (user_id) VALUES (gen_random_uuid())",
        "UPDATE client_log_batch SET entries = '[]'",
        "DELETE FROM client_log_batch",
    ],
)
def test_member_cannot_write_other_tables(statement: str) -> None:
    with pytest.raises(ProgrammingError) as error, as_role("authenticated", ALICE) as app:
        app.execute(text(statement))
    assert isinstance(error.value.orig, InsufficientPrivilege)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')",
        "INSERT INTO client_log_batch (device, entries) VALUES ('{}', '[]')",
    ],
)
def test_anonymous_cannot_write(statement: str) -> None:
    with pytest.raises(ProgrammingError) as error, as_role("anonymous") as app:
        app.execute(text(statement))
    assert isinstance(error.value.orig, InsufficientPrivilege)
