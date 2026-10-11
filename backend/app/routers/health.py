"""Liveness for load balancers and the smoke check. Public on purpose: it returns no data."""
from fastapi import APIRouter, Depends
from psycopg import Connection

from ..db import fetch_one, get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(conn: Connection = Depends(get_db)) -> dict:
    row = fetch_one(conn, "SELECT count(*) AS n FROM schema_migrations")
    return {"status": "ok", "database": "ok", "migrations": row["n"]}
