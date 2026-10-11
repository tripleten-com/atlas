"""The results worklist: lab values outside their reference range, newest first."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_all, get_db
from ..schemas import LabResultRow, Page
from ..tenancy import check_facility, facility_clause

router = APIRouter(tags=["results"])


@router.get("/api/results", response_model=Page[LabResultRow])
def results(
    facility_id: int | None = None,
    flag: Literal["abnormal", "H", "L", "all"] = "abnormal",
    test_code: str | None = None,
    hours: int = Query(72, ge=1, le=24 * 60, description="Look back this many hours from the newest result"),
    active_only: bool = Query(False, description="Only patients who are currently admitted"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> Page[LabResultRow]:
    check_facility(conn, user, facility_id)
    params: dict = {"t": user.tenant_id, "facility_id": facility_id, "hours": hours,
                    "limit": page_size, "offset": (page - 1) * page_size}
    fl = facility_clause("l", facility_id)
    where = f"l.tenant_id = %(t)s {fl}"
    if flag == "abnormal":
        where += " AND l.flag <> 'N'"
    elif flag in ("H", "L"):
        where += " AND l.flag = %(flag)s"
        params["flag"] = flag
    if test_code:
        where += " AND l.test_code = %(code)s"
        params["code"] = test_code
    if active_only:
        where += " AND EXISTS (SELECT 1 FROM encounters e WHERE e.id = l.encounter_id AND e.discharge_date IS NULL)"
    rows = fetch_all(
        conn,
        f"""WITH horizon AS (SELECT max(resulted_at) AS latest FROM labs l WHERE l.tenant_id = %(t)s {fl})
            SELECT l.id, l.encounter_id, l.test_code, l.test_name, l.loinc, l.panel, l.value, l.unit, l.ref_low, l.ref_high,
                   l.flag, l.collected_at, l.resulted_at, l.patient_id, p.name AS patient_name, p.mrn AS patient_mrn,
                   f.name AS facility_name, count(*) OVER () AS total
            FROM labs l
            JOIN patients p ON p.id = l.patient_id
            JOIN facilities f ON f.id = l.facility_id
            WHERE {where} AND l.resulted_at > (SELECT latest FROM horizon) - make_interval(hours => %(hours)s)
            ORDER BY l.resulted_at DESC, l.id DESC
            LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    )
    return Page(items=[LabResultRow(**r) for r in rows], total=rows[0]["total"] if rows else 0, page=page, page_size=page_size)
