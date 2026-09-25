import subprocess

from tests.conftest import BACKEND_DIR


def alembic(*args: str) -> None:
    result = subprocess.run(
        ["alembic", *args], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrations_match_models() -> None:
    alembic("check")


def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    alembic("downgrade", "base")
    alembic("upgrade", "head")
