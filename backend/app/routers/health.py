"""Liveness for load balancers and the smoke check. Public on purpose: it returns no data."""
from fastapi import APIRouter, Depends
from psycopg import Connection

from .. import ai_store
from ..db import fetch_one, get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(conn: Connection = Depends(get_db)) -> dict:
    row = fetch_one(conn, "SELECT count(*) AS n FROM schema_migrations")
    # The document store is reported, not required: the API serves the clinical record without
    # it, so "unavailable" here leaves status "ok" and the readiness probe passing.
    return {"status": "ok", "database": "ok", "migrations": row["n"], "ai_store": ai_store.status()}


@router.get("/health/live")
def live() -> dict:
    # Liveness only: "is the process up?" It never touches the database, so a database outage
    # does not make Kubernetes restart healthy API pods. Readiness still uses /health above.
    return {"status": "alive"}
