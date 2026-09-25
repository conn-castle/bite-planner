import subprocess
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session

from tests.conftest import BACKEND_DIR

BEFORE_HOUSEHOLDS = "631a70a020ad"
HOUSEHOLDS = "eb98de4a6c91"


def alembic(*args: str) -> None:
    result = subprocess.run(
        ["alembic", *args], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrations_match_models() -> None:
    result = subprocess.run(
        ["alembic", "check"], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    alembic("downgrade", "base")
    alembic("upgrade", "head")


@pytest.fixture
def before_households() -> Iterator[None]:
    """The database as it was before households; migrated to head again afterwards."""
    alembic("downgrade", BEFORE_HOUSEHOLDS)
    try:
        yield
    finally:
        alembic("upgrade", "head")


@pytest.mark.parametrize(("members", "jobs"), [(2, 1), (0, 1), (1, 0)])
def test_existing_members_and_jobs_join_conn_castle(
    before_households: None, session: Session, members: int, jobs: int
) -> None:
    for _ in range(members):
        session.execute(
            text("INSERT INTO household_member (user_id) VALUES (:id)"), {"id": uuid4()}
        )
    for _ in range(jobs):
        session.execute(
            text("INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')")
        )
    session.commit()

    alembic("upgrade", HOUSEHOLDS)

    [(household_id, name)] = session.execute(text("SELECT id, name FROM household")).all()
    assert name == "Conn Castle"
    for table, count in [("household_member", members), ("job", jobs)]:
        assigned = session.execute(text(f"SELECT household_id FROM {table}")).scalars().all()
        assert assigned == [household_id] * count


def test_an_empty_database_gets_no_household(before_households: None, session: Session) -> None:
    alembic("upgrade", HOUSEHOLDS)

    assert session.execute(text("SELECT count(*) FROM household")).scalar_one() == 0
