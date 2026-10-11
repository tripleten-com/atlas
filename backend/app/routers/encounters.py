"""Encounters across the organisation: who is here now, and who came and went."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_all, fetch_one, get_db
from ..schemas import EncounterListItem, Page
from ..tenancy import check_facility, facility_clause, not_found
from .patients import ENCOUNTER_COLUMNS

router = APIRouter(tags=["encounters"])

LIST_SQL = f"""
SELECT {ENCOUNTER_COLUMNS}, p.name AS patient_name, p.mrn AS patient_mrn, f.name AS facility_name
FROM encounters e
JOIN patients p ON p.id = e.patient_id
JOIN facilities f ON f.id = e.facility_id
"""


@router.get("/api/encounters", response_model=Page[EncounterListItem])
def list_encounters(
    facility_id: int | None = None,
    state: Literal["any", "active", "discharged"] = "active",
    admission_type: Literal["inpatient", "observation", "emergency", "outpatient"] | None = None,
    q: str | None = Query(None, description="Search by patient name, MRN or visit id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> Page[EncounterListItem]:
    check_facility(conn, user, facility_id)
    params: dict = {"t": user.tenant_id, "facility_id": facility_id, "limit": page_size, "offset": (page - 1) * page_size}
    where = "WHERE e.tenant_id = %(t)s" + facility_clause("e", facility_id)
    if state == "active":
        where += " AND e.discharge_date IS NULL"
    elif state == "discharged":
        where += " AND e.discharge_date IS NOT NULL"
    if admission_type:
        where += " AND e.admission_type = %(type)s"
        params["type"] = admission_type
    if q:
        where += " AND (p.name ILIKE %(q)s OR p.mrn ILIKE %(q)s OR e.external_id ILIKE %(q)s)"
        params["q"] = f"%{q.strip()}%"
    order = "e.admit_date DESC" if state != "discharged" else "e.discharge_date DESC"
    rows = fetch_all(
        conn,
        f"SELECT *, count(*) OVER () AS total FROM ({LIST_SQL} {where}) x ORDER BY {order.replace('e.', 'x.')} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    return Page(items=[EncounterListItem(**r) for r in rows], total=rows[0]["total"] if rows else 0,
                page=page, page_size=page_size)


@router.get("/api/encounters/{encounter_id}", response_model=EncounterListItem)
def get_encounter(encounter_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> EncounterListItem:
    row = fetch_one(conn, LIST_SQL + " WHERE e.id = %s AND e.tenant_id = %s", (encounter_id, user.tenant_id))
    if row is None:
        raise not_found("Encounter not found")
    return EncounterListItem(**row)
