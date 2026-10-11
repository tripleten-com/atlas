"""Meridian's own storage: an append-only event log per facility, subscriptions, and a delivery log.

SQLite is plenty for a mock vendor. There is one shared connection, so every read and write
takes the same lock.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    facility TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (facility, sequence)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    facility TEXT NOT NULL,
    callback_url TEXT NOT NULL,
    secret TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    cursor INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    last_delivered_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (facility, callback_url)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    first_sequence INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL,
    events INTEGER NOT NULL,
    status_code INTEGER,
    error TEXT,
    duration_ms INTEGER,
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deliveries_sub_idx ON deliveries (subscription_id, attempted_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: str):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(data_dir) / "meridian.db"
        self.lock = threading.RLock()  # one connection, shared: every read and write holds the lock
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT value FROM meta WHERE key = 'epoch'").fetchone()
        if row is None:
            self.epoch = uuid.uuid4().hex[:12]
            self.db.execute("INSERT INTO meta (key, value) VALUES ('epoch', ?)", (self.epoch,))
        else:
            self.epoch = row["value"]

    # ------------------------------------------------------------ events
    def append(self, facility: str, events: list[dict]) -> int:
        """Assign sequences and store. Returns the new latest sequence."""
        with self.lock:
            latest = self.latest_sequence(facility)
            self.db.execute("BEGIN")
            try:
                for ev in events:
                    latest += 1
                    ev["sequence"] = latest
                    self.db.execute(
                        "INSERT INTO events (facility, sequence, id, type, occurred_at, body) VALUES (?, ?, ?, ?, ?, ?)",
                        (facility, latest, ev["id"], ev["type"], ev["occurred_at"], json.dumps(ev)),
                    )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return latest

    def latest_sequence(self, facility: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT max(sequence) AS s FROM events WHERE facility = ?", (facility,)).fetchone()
        return row["s"] or 0

    def events_after(self, facility: str, after: int, limit: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT body FROM events WHERE facility = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                (facility, after, limit),
            ).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def counts(self, facility: str) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT type, count(*) AS n FROM events WHERE facility = ? GROUP BY type", (facility,)).fetchall()
        return {r["type"]: r["n"] for r in rows}

    # ------------------------------------------------------------ subscriptions
    def upsert_subscription(self, facility: str, callback_url: str, secret: str | None, after: int) -> dict:
        with self.lock:
            existing = self.db.execute(
                "SELECT id FROM subscriptions WHERE facility = ? AND callback_url = ?", (facility, callback_url)
            ).fetchone()
            if existing:
                self.db.execute(
                    """UPDATE subscriptions SET secret = coalesce(?, secret), cursor = ?, status = 'active', failures = 0,
                       next_attempt_at = 0, last_error = NULL WHERE id = ?""",
                    (secret, after, existing["id"]),
                )
                sub_id = existing["id"]
            else:
                sub_id = "sub_" + secrets.token_hex(8)
                self.db.execute(
                    "INSERT INTO subscriptions (id, facility, callback_url, secret, cursor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (sub_id, facility, callback_url, secret or secrets.token_urlsafe(32), after, now_iso()),
                )
        return self.subscription(sub_id)

    def subscription(self, sub_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
        return dict(row) if row else None

    def subscriptions(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM subscriptions ORDER BY created_at").fetchall()]

    def update_subscription(self, sub_id: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE subscriptions SET {sets} WHERE id = ?", (*fields.values(), sub_id))

    def delete_subscription(self, sub_id: str) -> bool:
        with self.lock:
            return self.db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,)).rowcount > 0

    # ------------------------------------------------------------ delivery log
    def log_delivery(self, sub_id: str, first: int, last: int, n: int, status_code: int | None, error: str | None, ms: int) -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO deliveries (id, subscription_id, first_sequence, last_sequence, events, status_code, error,
                                           duration_ms, attempted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("dlv_" + secrets.token_hex(8), sub_id, first, last, n, status_code, error, ms, now_iso()),
            )
            self.db.execute(
                """DELETE FROM deliveries WHERE subscription_id = ? AND id NOT IN
                   (SELECT id FROM deliveries WHERE subscription_id = ? ORDER BY attempted_at DESC LIMIT 500)""",
                (sub_id, sub_id),
            )

    def deliveries(self, sub_id: str, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM deliveries WHERE subscription_id = ? ORDER BY attempted_at DESC LIMIT ?", (sub_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
