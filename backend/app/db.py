"""One engine per process. Every write goes through `with engine.begin() as conn:` so a failure rolls back."""
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import settings


@lru_cache(maxsize=None)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5, future=True)


def db_ok(engine: Engine | None = None) -> bool:
    try:
        with (engine or get_engine()).connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        return False
