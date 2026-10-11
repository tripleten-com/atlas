"""Apply the SQL files in app/migrations/ in order, once each.

    python -m app.migrate

Each file runs in its own transaction and is recorded in schema_migrations, so running this
again is safe. To change the schema, add a new numbered file; never edit one that has run.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg

from .config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


def wait_for_db(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(settings.database_url, connect_timeout=3):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


def migrate() -> list[str]:
    wait_for_db()
    applied_now = []
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied_now.append(path.stem)
    return applied_now


if __name__ == "__main__":
    applied = migrate()
    print(f"migrations applied: {', '.join(applied) if applied else 'none (schema is current)'}")
    sys.exit(0)
