"""The Data API's roles can't reach the data: the app goes through the API,
which connects as the owner. (Neon still creates `authenticated` and
`anonymous` while the Data API or Neon Auth is enabled.)"""

from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from castle_chef.db import get_engine

# A column of each table, to try updating it.
COLUMN = {
    "meal": "name",
    "meal_ingredient": "name",
    "job": "status",
    "job_meal": "job_id",
    "household_member": "user_id",
}
TABLES = list(COLUMN)


@pytest.mark.parametrize("role", ["authenticated", "anonymous"])
@pytest.mark.parametrize("table", TABLES)
def test_data_api_roles_cannot_read_or_write(role: str, table: str) -> None:
    writes = {
        # What the Data API could insert before (job requests), and more.
        "job": "INSERT INTO job (kind, params) VALUES ('generate_meals', '{\"count\": 1}')",
        "household_member": f"INSERT INTO household_member (user_id) VALUES ('{uuid4()}')",
    }
    statements = [f"SELECT * FROM {table}", f"UPDATE {table} SET {COLUMN[table]} = {COLUMN[table]}"]
    for statement in [*statements, *([writes[table]] if table in writes else [])]:
        with (
            get_engine().connect() as connection,
            connection.begin(),
            pytest.raises(ProgrammingError) as error,
        ):
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            connection.execute(text(statement))
        assert isinstance(error.value.orig, InsufficientPrivilege)
