import subprocess

from tests.conftest import BACKEND_DIR


def test_migrations_match_models() -> None:
    result = subprocess.run(
        ["alembic", "check"], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(
            ["alembic", *command], cwd=BACKEND_DIR, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
