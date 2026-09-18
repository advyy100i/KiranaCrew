"""Integration tests need a live Postgres. They use a dedicated `kirana_test` database, created on demand.
Set TEST_DATABASE_URL to override; tests are skipped when the server is unreachable."""
import os
import pathlib

import pytest
from sqlalchemy import create_engine, text

from app.config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL", settings.database_url.rsplit("/", 1)[0] + "/kirana_test")
CATALOG = pathlib.Path(__file__).resolve().parents[2] / "eval" / "seed_catalog.json"


def _ensure_test_db() -> bool:
    admin = create_engine(TEST_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 3})
    try:
        with admin.connect() as c:
            if not c.execute(text("SELECT 1 FROM pg_database WHERE datname='kirana_test'")).scalar():
                c.execute(text("CREATE DATABASE kirana_test"))
        return True
    except Exception:
        return False
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine():
    if not _ensure_test_db():
        pytest.skip("Postgres not reachable")
    os.environ["DATABASE_URL"] = TEST_URL
    settings.database_url = TEST_URL
    from app.db import get_engine
    get_engine.cache_clear()
    eng = get_engine(TEST_URL)
    from app.cli.migrate import migrate
    migrate(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def seeded(engine):
    """Fresh seed with a short history; returns shop_id. Registers telegram user 4242 as OWNER."""
    from app.cli.seed_demo import seed
    out = seed(engine, CATALOG, reset=True, telegram_user_id=4242, history_weeks=1)
    return out["shop_id"]


@pytest.fixture
def conn(engine):
    with engine.connect() as c:
        yield c
