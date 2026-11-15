import json
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import text

from bite_planner.db import get_engine
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

    household_id = uuid4()
    alembic("downgrade", "1bab82dadb82")
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM meal WHERE name LIKE 'mig-%'"))
            connection.execute(
                text("INSERT INTO household (id, name) VALUES (:id, 'Migration')"),
                {"id": household_id},
            )
            for name, value in [("mig-old", ["Heat.", "Roast."]), ("mig-mixed", ["Heat.", new])]:
                connection.execute(
                    text(
                        "INSERT INTO meal (id, name, servings, steps)"
                        " VALUES (:id, :name, 4, :steps)"
                    ),
                    {"id": uuid4(), "name": name, "steps": json.dumps(value)},
                )

            connection.execute(
                text(
                    "INSERT INTO meal_preference (household_id, meal_id, rating) "
                    "SELECT :house, id, 'up' FROM meal WHERE name LIKE 'mig-%'"
                ),
                {"house": household_id},
            )
        alembic("upgrade", "head")
        heat: dict[str, object] = {"text": "Heat.", "timer_minutes": None, "ingredients": []}
        roast: dict[str, object] = {"text": "Roast.", "timer_minutes": None, "ingredients": []}
        assert steps() == {"mig-old": [heat, roast], "mig-mixed": [heat, new]}

        alembic("downgrade", "1bab82dadb82")
        assert steps() == {"mig-old": ["Heat.", "Roast."], "mig-mixed": ["Heat.", "Roast."]}
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM meal_preference WHERE household_id=:id"), {"id": household_id}
            )
            connection.execute(text("DELETE FROM meal WHERE name LIKE 'mig-%'"))
            connection.execute(text("DELETE FROM household WHERE id=:id"), {"id": household_id})
        alembic("upgrade", "head")


@pytest.mark.parametrize(
    ("step_text", "valid"),
    [
        ("Heat ¾ can beans. Reserve ¼ lemon.", True),
        ("\n Heat ¾ can beans. Reserve ¼ lemon. \t", True),
        (" \n\t", False),
        ("", False),
    ],
)
def test_prepared_recipe_backfill_is_literal_one_time_and_constraint_is_strict(
    step_text: str, valid: bool
) -> None:
    from sqlalchemy.exc import IntegrityError

    engine = get_engine()
    household_id, order_id, batch_id, meal_id, build_id = [uuid4() for _ in range(5)]
    owner = uuid4()
    alembic("downgrade", "c6e4a291b502")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO household (id, name) VALUES (:id, 'Migration')"),
                {"id": household_id},
            )
            connection.execute(
                text(
                    "INSERT INTO grocery_order (id, household_id, created_by, status) "
                    "VALUES (:id, :house, :owner, 'draft')"
                ),
                {"id": order_id, "house": household_id, "owner": owner},
            )
            connection.execute(
                text(
                    "INSERT INTO job (id, grocery_order_id, requested_by, kind) "
                    "VALUES (:id, :order, :owner, 'generate_meals')"
                ),
                {"id": batch_id, "order": order_id, "owner": owner},
            )
            connection.execute(
                text(
                    "INSERT INTO meal (id, job_id, name, servings, steps) "
                    "VALUES (:id, :job, 'Legacy beans', 2, CAST(:steps AS jsonb))"
                ),
                {
                    "id": meal_id,
                    "job": batch_id,
                    "steps": json.dumps(
                        [
                            {
                                "text": step_text,
                                "timer_minutes": 7,
                                "ingredients": [0, 1],
                            }
                        ]
                    ),
                },
            )
            for position, name, quantity, unit in [
                (0, "beans", 0.75, "can"),
                (1, "lemon", 0.25, None),
                (2, "tiny", 0.001, "tsp"),
            ]:
                connection.execute(
                    text(
                        "INSERT INTO meal_ingredient (id, meal_id, position, name, quantity, unit) "
                        "VALUES (:id, :meal, :position, :name, :quantity, :unit)"
                    ),
                    {
                        "id": uuid4(),
                        "meal": meal_id,
                        "position": position,
                        "name": name,
                        "quantity": quantity,
                        "unit": unit,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO job (id, grocery_order_id, requested_by, kind, "
                    "status, list_source) "
                    "VALUES (:id, :order, :owner, 'build_list', 'succeeded', "
                    "CAST(:source AS jsonb))"
                ),
                {
                    "id": build_id,
                    "order": order_id,
                    "owner": owner,
                    "source": json.dumps(
                        {"picks": [{"meal_id": str(meal_id), "servings": 5, "revision": 0}]}
                    ),
                },
            )
        if not valid:
            failed = subprocess.run(
                ["alembic", "upgrade", "head"],
                cwd=BACKEND_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
            assert failed.returncode != 0
            assert str(build_id) in failed.stderr and str(meal_id) in failed.stderr
            assert "cooking steps must contain non-whitespace text" in failed.stderr
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "c6e4a291b502"
                )
                assert not connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='job' AND column_name='prepared_recipes')"
                    )
                ).scalar_one()
            return
        alembic("upgrade", "head")
        from sqlmodel import Session

        from bite_planner.models import Job

        with Session(engine) as session:
            build = session.get_one(Job, build_id)
            snapshot = build.prepared_recipes
            assert snapshot is not None and snapshot.legacy_scaled
            assert snapshot.recipes[0].quantities == [1.88, 0.62, 0]
            assert snapshot.recipes[0].steps == [step_text]
        for sql in [
            "UPDATE job SET prepared_recipes = NULL WHERE id = :id",
            "UPDATE job SET status = 'running' WHERE id = :id",
            "UPDATE job SET prepared_recipes = '{}'::jsonb WHERE id = :batch",
        ]:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(sql), {"id": build_id, "batch": batch_id})
        # Re-running upgrade leaves the persisted recipe untouched.
        alembic("upgrade", "head")
        with Session(engine) as session:
            assert session.get_one(Job, build_id).prepared_recipes == snapshot
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM grocery_order WHERE id=:id"), {"id": order_id})
            connection.execute(text("DELETE FROM meal WHERE id=:id"), {"id": meal_id})
            connection.execute(text("DELETE FROM household WHERE id=:id"), {"id": household_id})
        alembic("upgrade", "head")


@pytest.mark.parametrize(
    "evidence", ["job", "rating", "agreeing", "unknown", "job-conflict", "rating-conflict"]
)
def test_durable_meal_owner_backfill_requires_unambiguous_evidence(evidence: str) -> None:
    engine = get_engine()
    house, other, order, job, meal = [uuid4() for _ in range(5)]
    alembic("downgrade", "e7f3c9012a64")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO household (id, name) VALUES (:id, 'Migration'), (:other, 'Other')"
                ),
                {"id": house, "other": other},
            )
            connection.execute(
                text(
                    "INSERT INTO grocery_order (id, household_id, created_by, status) "
                    "VALUES (:id, :house, :owner, 'draft')"
                ),
                {"id": order, "house": house, "owner": uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO job (id, grocery_order_id, requested_by, kind) "
                    "VALUES (:id, :order, :owner, 'generate_meals')"
                ),
                {"id": job, "order": order, "owner": uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO meal (id, job_id, name, servings, steps) "
                    "VALUES (:id, :job, 'Historical meal', 2, '[]'::jsonb)"
                ),
                {
                    "id": meal,
                    "job": job if evidence in ("job", "agreeing", "job-conflict") else None,
                },
            )
            if evidence in ("rating", "agreeing", "rating-conflict"):
                connection.execute(
                    text(
                        "INSERT INTO meal_preference (household_id, meal_id, rating) "
                        "VALUES (:house, :meal, 'up')"
                    ),
                    {"house": house, "meal": meal},
                )
            if evidence in ("job-conflict", "rating-conflict"):
                connection.execute(
                    text(
                        "INSERT INTO meal_preference (household_id, meal_id, rating) "
                        "VALUES (:house, :meal, 'down')"
                    ),
                    {"house": other, "meal": meal},
                )
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        with engine.connect() as connection:
            if evidence in ("job", "rating", "agreeing"):
                assert result.returncode == 0, result.stderr
                assert (
                    connection.execute(
                        text("SELECT household_id FROM meal WHERE id=:id"), {"id": meal}
                    ).scalar_one()
                    == house
                )
            else:
                assert result.returncode != 0
                assert "unknown/conflicting meals" in result.stderr and str(meal) in result.stderr
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "e7f3c9012a64"
                )
                assert not connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='meal' AND column_name='household_id')"
                    )
                ).scalar_one()
                assert (
                    connection.execute(
                        text("SELECT name FROM meal WHERE id=:id"), {"id": meal}
                    ).scalar_one()
                    == "Historical meal"
                )
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM meal_preference WHERE meal_id=:id"), {"id": meal})
            connection.execute(text("DELETE FROM grocery_order WHERE id=:id"), {"id": order})
            connection.execute(text("DELETE FROM meal WHERE id=:id"), {"id": meal})
            connection.execute(
                text("DELETE FROM household WHERE id IN (:house, :other)"),
                {"house": house, "other": other},
            )
        alembic("upgrade", "head")
