from functools import lru_cache

from sqlalchemy import Connection, Engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine
from sqlmodel.sql.expression import SelectOfScalar

from bite_planner.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    # Neon hands out `postgresql://` URLs; use the psycopg 3 driver for them.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    # No client-side pool: the API runs on Lambda and the worker is one short-lived
    # process per job, and Neon's pooler (PgBouncer, transaction mode) pools for us.
    # Its transaction mode also can't keep server-side prepared statements.
    return create_engine(
        url, poolclass=NullPool, connect_args={"prepare_threshold": None, "connect_timeout": 10}
    )


def get_session() -> Session:
    return Session(get_engine())


def _limit_waits(_session: Session, _transaction: object, connection: Connection) -> None:
    # SET LOCAL: per transaction. A plain SET would leak to other clients of
    # the pooler's shared server connections.
    connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'; SET LOCAL lock_timeout = '5s'")


def get_request_session() -> Session:
    """A session for API requests: every transaction gives up on a slow statement
    or lock well within the Lambda's time limit."""
    session = get_session()
    event.listen(session, "after_begin", _limit_waits)
    return session


def for_update[T](statement: SelectOfScalar[T]) -> SelectOfScalar[T]:
    """The statement, locking the rows it reads until the transaction ends. Rows
    the session already holds are read again: a lock taken after an unlocked read
    would otherwise check that read's values, which may have changed since."""
    return statement.with_for_update().execution_options(populate_existing=True)
