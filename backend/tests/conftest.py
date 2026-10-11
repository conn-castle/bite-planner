import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.pool import NullPool
from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tests only ever run on TEST_DATABASE_URL's Postgres (the local compose one), never
# the DATABASE_URL in backend/.env. Its database is only where the test databases
# are made from: tests run in their own (below).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise pytest.UsageError(
        "Set TEST_DATABASE_URL, e.g. start `docker compose up -d` in backend/ and use "
        "postgresql://postgres:postgres@localhost:54329/castle_chef"
    )
# Tests run in parallel (pytest-xdist, `-n auto` in pyproject.toml): each worker
# has a database of its own, made from TEST_DATABASE_URL's name and the worker's
# (castle_chef_gw0, …; castle_chef_main without workers).
_BASE_URL = make_url(TEST_DATABASE_URL).set(drivername="postgresql+psycopg")
DATABASE_NAME = f"{_BASE_URL.database}_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
os.environ["DATABASE_URL"] = (
    make_url(TEST_DATABASE_URL).set(database=DATABASE_NAME).render_as_string(hide_password=False)
)

from castle_chef_agent.address import Address  # noqa: E402
from castle_chef_agent.instacart import DeliveryAddress  # noqa: E402

from castle_chef.db import get_engine  # noqa: E402
from castle_chef.dispatch import get_dispatcher  # noqa: E402
from castle_chef.models import GroceryOrder, Household, Job, JobKind  # noqa: E402
from castle_chef.settings import get_settings  # noqa: E402

TEST_ADDRESSES: list[DeliveryAddress] = [
    DeliveryAddress.model_validate(address)
    for address in json.loads(
        (BACKEND_DIR / "tests/fixtures/instacart/addresses.json").read_text()
    )["structuredContent"]["artifacts"]["addresses"]
]
TEST_ADDRESS: Address = Address.model_validate(
    TEST_ADDRESSES[0].model_dump(include=set(Address.model_fields))
)
OTHER_ADDRESS: Address = Address.model_validate(
    TEST_ADDRESSES[1].model_dump(include=set(Address.model_fields))
)


# Held while a worker makes its database: the stubs create cluster-wide roles,
# which workers creating them at once would collide on.
_SETUP_LOCK = 4_271_001
# Held by a test run for as long as it runs: the test databases are remade at the
# start of every run, so a second run at once would drop the first one's.
_RUN_LOCK = 4_271_002
_run: Connection | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuses a second test run on the same Postgres while one is going. Taken
    by the run itself (not its workers) before any worker starts; Postgres lets
    go of it when the run ends, however it ends."""
    global _run
    if hasattr(session.config, "workerinput"):
        return
    _run = create_engine(_BASE_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool).connect()
    if not _run.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _RUN_LOCK}).scalar():
        pytest.exit(
            "Another test run is using this Postgres (TEST_DATABASE_URL); wait for it to finish.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Make this worker's database afresh, with Neon's stubs, and migrate it,
    once per test run."""
    admin = create_engine(_BASE_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _SETUP_LOCK})
        try:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{DATABASE_NAME}"'))
            with get_engine().begin() as database:
                database.execute(text((BACKEND_DIR / "tests" / "neon_stubs.sql").read_text()))
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SETUP_LOCK})
    admin.dispose()
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
                "TRUNCATE push_device, shopping_list_item, meal_preference, grocery_order_meal, "
                'meal_ingredient, meal, job, grocery_order, app_user, household, neon_auth."user" '
                "CASCADE"
            )
        )


@pytest.fixture
def session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


@pytest.fixture
def household_id(session: Session) -> UUID:
    """A household for orders whose household doesn't matter to the test."""
    household = Household(
        name="Test Castle",
        delivery_address=TEST_ADDRESS,
    )
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


FAKE_CODEX = Path(__file__).parent / "fake_codex.py"


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Instacart through a stand-in `codex app-server` (fake_codex.py) that replays
    recorded replies; configure it with `fake.json` in the returned directory."""
    from castle_chef import instacart

    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CASTLE_CHEF_CODEX_HOME", str(home))
    monkeypatch.setenv("CASTLE_CHEF_CODEX_LOCK_FILE", str(home / "codex.lock"))
    monkeypatch.setenv("CASTLE_CHEF_CODEX_COMMAND", f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.setattr(instacart, "POLL_SECONDS", 0)
    get_settings.cache_clear()
    return home


CHECKOUT_PAGES = Path(__file__).parent / "fixtures" / "checkout"
# How often checkout's tools check a page for having stopped changing: the
# stand-in pages are static (but for one test's), so they settle at once.
SETTLE_CHECK_MS = 50


STAND_IN_PAGES = {
    "/store/wegmans/storefront": "storefront.html",
    "/store/checkout_v4": "checkout.html",
    "/store/sign-in": "sign-in.html",
    "/store/orders/confirmation": "confirmation.html",
    "/store/settling": "settling.html",
}


class _StandIns(SimpleHTTPRequestHandler):
    """Instacart's pages, as stand-ins: the store's front page and checkout."""

    def translate_path(self, path: str) -> str:
        return str(CHECKOUT_PAGES / STAND_IN_PAGES.get(path.split("?")[0], "missing"))

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def instacart_site(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Stand-ins for Instacart's store front and checkout pages, served here; the
    worker's settings point at them."""
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_StandIns, directory=str(CHECKOUT_PAGES))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("CASTLE_CHEF_INSTACART_SITE", site)
    monkeypatch.setenv("CASTLE_CHEF_CHECKOUT_SETTLE_CHECK_MS", str(SETTLE_CHECK_MS))
    get_settings.cache_clear()
    yield site
    server.shutdown()
