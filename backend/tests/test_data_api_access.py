"""The Data API's roles can't reach the data: the app goes through the API,
which connects as the owner. (Neon still creates `authenticated` and
`anonymous` while the Data API or Neon Auth is enabled.)"""

from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlmodel import SQLModel

import bite_planner.models  # noqa: F401  (registers the tables on SQLModel.metadata)
from bite_planner.db import get_engine

# Every table, so a new one is covered too, with a column to try updating.
COLUMN = {name: next(iter(table.columns)).name for name, table in SQLModel.metadata.tables.items()}
TABLES = sorted(COLUMN)


# A well-formed insert into every table (so each fails on privileges, not syntax).
INSERTS = {
    "delivery_decision": (
        "INSERT INTO delivery_decision (id, grocery_order_id, household_id, kind, occurred_at) "
        f"VALUES ('{uuid4()}', '{uuid4()}', '{uuid4()}', 'selected', now())"
    ),
    "household": f"INSERT INTO household (id, name) VALUES ('{uuid4()}', 'Castle')",
    "app_user": (f"INSERT INTO app_user (user_id, household_id) VALUES ('{uuid4()}', '{uuid4()}')"),
    "grocery_order": (
        "INSERT INTO grocery_order (id, household_id, created_by, status) "
        f"VALUES ('{uuid4()}', '{uuid4()}', '{uuid4()}', 'draft')"
    ),
    "grocery_order_meal": (
        "INSERT INTO grocery_order_meal (grocery_order_id, meal_id) "
        f"VALUES ('{uuid4()}', '{uuid4()}')"
    ),
    # What the Data API could insert before (job requests).
    "job": (
        "INSERT INTO job (id, grocery_order_id, kind, requested_by) "
        f"VALUES ('{uuid4()}', '{uuid4()}', 'generate_meals', '{uuid4()}')"
    ),
    "meal": (
        f"INSERT INTO meal (id, name, servings, steps) VALUES ('{uuid4()}', 'Tacos', 4, '[]')"
    ),
    "meal_ingredient": (
        "INSERT INTO meal_ingredient (id, meal_id, position, name) "
        f"VALUES ('{uuid4()}', '{uuid4()}', 0, 'beef')"
    ),
    "shopping_list_item": (
        "INSERT INTO shopping_list_item (id, job_id, position, name, aisle, meal_ids, on_hand) "
        f"VALUES ('{uuid4()}', '{uuid4()}', 0, 'tortillas', 'pantry', '{{}}', false)"
    ),
    "push_device": (
        f"INSERT INTO push_device (token, user_id) VALUES ('{'ab' * 32}', '{uuid4()}')"
    ),
    "household_invite": (
        "INSERT INTO household_invite (id, household_id, email, name) "
        f"VALUES ('{uuid4()}', '{uuid4()}', 'sam@example.com', 'Sam')"
    ),
    "meal_preference": (
        "INSERT INTO meal_preference (household_id, meal_id, rating) "
        f"VALUES ('{uuid4()}', '{uuid4()}', 'up')"
    ),
}


def test_every_table_has_an_insert_to_try() -> None:
    assert sorted(INSERTS) == TABLES


@pytest.mark.parametrize("role", ["authenticated", "anonymous"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_roles_cannot_read_or_write(role: str, table: str) -> None:
    statements = [
        f"SELECT * FROM {table}",
        f"UPDATE {table} SET {COLUMN[table]} = {COLUMN[table]}",
        f"DELETE FROM {table}",
        INSERTS[table],
    ]
    for statement in statements:
        with (
            get_engine().connect() as connection,
            connection.begin(),
            pytest.raises(ProgrammingError) as error,
        ):
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            connection.execute(text(statement))
        assert isinstance(error.value.orig, InsufficientPrivilege)
