from functools import lru_cache

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from castle_chef.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    # Neon hands out `postgresql://` URLs; use the psycopg 3 driver for them.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return create_engine(url, pool_pre_ping=True)


def get_session() -> Session:
    return Session(get_engine())
