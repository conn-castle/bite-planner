import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

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
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    yield
    get_settings.cache_clear()
    get_dispatcher.cache_clear()
    with get_engine().begin() as connection:
        connection.execute(text('TRUNCATE job, meal, household_member, neon_auth."user" CASCADE'))


@pytest.fixture
def session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
