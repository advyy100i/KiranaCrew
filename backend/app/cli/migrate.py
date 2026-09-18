"""Apply backend/migrations/*.sql in order, once each, inside one transaction per file.
   python -m app.cli.migrate [--url postgresql+psycopg://...]"""
import argparse
import pathlib

from sqlalchemy import text

from app.db import get_engine

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "migrations"


def migrate(engine=None) -> list[str]:
    engine = engine or get_engine()
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations "
                          "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with engine.begin() as conn:
            conn.execute(text("LOCK TABLE schema_migrations IN EXCLUSIVE MODE"))
            done = conn.execute(text("SELECT 1 FROM schema_migrations WHERE filename=:f"),
                                {"f": path.name}).scalar()
            if done:
                continue
            conn.exec_driver_sql(path.read_text(encoding="utf-8"))
            conn.execute(text("INSERT INTO schema_migrations (filename) VALUES (:f)"), {"f": path.name})
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    a = ap.parse_args()
    done = migrate(get_engine(a.url) if a.url else None)
    print("applied:", done or "nothing (up to date)")
