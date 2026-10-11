"""Helpers that keep every read inside the caller's tenant.

The rule in one sentence: a record the caller's tenant does not own looks exactly like a
record that does not exist. Both are a 404, so a response never confirms that some other
customer's patient 88 is real.
"""
from __future__ import annotations

from fastapi import HTTPException
from psycopg import Connection

from .auth import CurrentUser
from .db import execute, fetch_one


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def load_patient(conn: Connection, user: CurrentUser, patient_id: int) -> dict:
    row = fetch_one(
        conn,
        """SELECT p.*, f.name AS facility_name
           FROM patients p JOIN facilities f ON f.id = p.facility_id
           WHERE p.id = %s AND p.tenant_id = %s""",
        (patient_id, user.tenant_id),
    )
    if row is None:
        raise not_found("Patient not found")
    return row


def check_facility(conn: Connection, user: CurrentUser, facility_id: int | None) -> int | None:
    """Validate an optional facility filter. None means every facility in the tenant."""
    if facility_id is None:
        return None
    row = fetch_one(conn, "SELECT id FROM facilities WHERE id = %s AND tenant_id = %s", (facility_id, user.tenant_id))
    if row is None:
        raise not_found("Facility not found")
    return facility_id


def facility_clause(alias: str, facility_id: int | None) -> str:
    """SQL fragment for an optional facility filter. Pair it with a `facility_id` parameter."""
    return f" AND {alias}.facility_id = %(facility_id)s" if facility_id is not None else ""


def audit(conn: Connection, user: CurrentUser, action: str, target: str | None = None, detail: dict | None = None) -> None:
    from psycopg.types.json import Jsonb

    execute(
        conn,
        "INSERT INTO audit_log (tenant_id, user_id, action, target, detail) VALUES (%s, %s, %s, %s, %s)",
        (user.tenant_id, user.id, action, target, Jsonb(detail) if detail is not None else None),
    )
