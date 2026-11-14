from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

import bite_planner.models  # noqa: F401  (registers the tables on SQLModel.metadata)
from bite_planner.db import get_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations() -> None:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
run_migrations()
