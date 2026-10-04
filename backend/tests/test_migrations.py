import json
import subprocess
from uuid import uuid4

from sqlalchemy import text

from castle_chef.db import get_engine
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


def test_recipe_steps_become_steps_with_timers_and_back() -> None:
    """A step was its text; it becomes {text, timer_minutes, ingredients}, and
    steps already in that shape are left as they are, both ways."""
    engine = get_engine()
    new = {"text": "Roast.", "timer_minutes": 15, "ingredients": [0]}

    def steps() -> dict[str, object]:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT name, steps FROM meal WHERE name LIKE 'mig-%'"))
            return {name: value for name, value in rows}

    alembic("downgrade", "1bab82dadb82")
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM meal WHERE name LIKE 'mig-%'"))
            for name, value in [("mig-old", ["Heat.", "Roast."]), ("mig-mixed", ["Heat.", new])]:
                connection.execute(
                    text(
                        "INSERT INTO meal (id, name, servings, steps)"
                        " VALUES (:id, :name, 4, :steps)"
                    ),
                    {"id": uuid4(), "name": name, "steps": json.dumps(value)},
                )

        alembic("upgrade", "head")
        heat = {"text": "Heat.", "timer_minutes": None, "ingredients": []}
        roast = {"text": "Roast.", "timer_minutes": None, "ingredients": []}
        assert steps() == {"mig-old": [heat, roast], "mig-mixed": [heat, new]}

        alembic("downgrade", "1bab82dadb82")
        assert steps() == {"mig-old": ["Heat.", "Roast."], "mig-mixed": ["Heat.", "Roast."]}
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM meal WHERE name LIKE 'mig-%'"))
        alembic("upgrade", "head")
