import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tests only ever run against TEST_DATABASE_URL (the local compose Postgres),
# never the DATABASE_URL in backend/.env.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise pytest.UsageError(
        "Set TEST_DATABASE_URL, e.g. start `docker compose up -d` in backend/ and use "
        "postgresql://postgres:postgres@localhost:54329/castle_chef"
    )
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from castle_chef.db import get_engine  # noqa: E402
from castle_chef.dispatch import get_dispatcher  # noqa: E402
from castle_chef.models import GroceryOrder, Household, Job, JobKind  # noqa: E402
from castle_chef.settings import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Rebuild the schema from the migrations once per test run."""
    with get_engine().begin() as connection:
        connection.execute(text((BACKEND_DIR / "tests" / "neon_stubs.sql").read_text()))
        # Drop the schemas the migrations create, and recreate `public` with the
        # grants a new Postgres database starts with.
        connection.execute(
            text(
                "DROP SCHEMA IF EXISTS app_private CASCADE;"
                "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner;"
                "GRANT USAGE ON SCHEMA public TO PUBLIC;"
            )
        )
    subprocess.run(["alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True)


@pytest.fixture(autouse=True)
def clean_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CASTLE_CHEF_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("CASTLE_CHEF_LOGS_DIR", str(tmp_path / "logs"))
    # Small batches keep fake-agent runs fast; tests of the batch size set their own.
    monkeypatch.setenv("CASTLE_CHEF_MEALS_PER_BATCH", "1")
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    yield
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "TRUNCATE meal_preference, grocery_order_meal, meal_ingredient, meal, job, "
                'grocery_order, app_user, household, neon_auth."user" CASCADE'
            )
        )


@pytest.fixture
def session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


@pytest.fixture
def household_id(session: Session) -> UUID:
    """A household for orders whose household doesn't matter to the test."""
    household = Household(name="Test Castle")
    session.add(household)
    session.commit()
    return household.id


@pytest.fixture
def draft(session: Session, household_id: UUID) -> GroceryOrder:
    """A draft order, for jobs whose order doesn't matter to the test."""
    order = GroceryOrder(household_id=household_id, created_by=uuid4())
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


@pytest.fixture
def job(session: Session, draft: GroceryOrder) -> Job:
    """A pending batch of suggestions for the draft, as the API records it."""
    pending = Job(
        id=uuid4(),
        grocery_order_id=draft.id,
        kind=JobKind.GENERATE_MEALS,
        requested_by=draft.created_by,
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    return pending
