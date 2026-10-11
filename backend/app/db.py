"""Database access. Atlas talks to Postgres with plain, parameterised SQL through psycopg 3.

Two ways in:
  * `get_db` is a FastAPI dependency that lends a request one pooled connection.
  * `connection()` is a context manager for scripts and the ingestion worker.

Rows come back as dicts. Every query that touches clinical data must filter on tenant_id;
see `docs/ARCHITECTURE.md` for why.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool = ConnectionPool(
    settings.database_url,
    min_size=1,
    max_size=10,
    kwargs={"row_factory": dict_row, "autocommit": False},
    open=False,
)


def open_pool() -> None:
    pool.open(wait=True, timeout=30)


def close_pool() -> None:
    pool.close()


@contextmanager
def connection() -> Iterator[Connection]:
    with pool.connection() as conn:
        yield conn


def get_db() -> Iterator[Connection]:
    """FastAPI dependency: one connection per request, committed if the handler returns."""
    with pool.connection() as conn:
        yield conn


def fetch_all(conn: Connection, sql: str, params: dict[str, Any] | tuple | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: Connection, sql: str, params: dict[str, Any] | tuple | None = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn: Connection, sql: str, params: dict[str, Any] | tuple | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
