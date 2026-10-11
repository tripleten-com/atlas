"""Patients: the list, one patient's record, and the measurements behind it.

Every query here filters on the caller's tenant_id. A patient outside the tenant is a 404.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_all, fetch_one, get_db
from ..reference import VITAL_LABELS, reference_payload, vital_status
from ..schemas import (
    EncounterOut,
    LabOut,
    LatestVital,
    MedicationOut,
    Page,
    PatientDetail,
    PatientSummary,
    TimelineItem,
    VitalOut,
)
from ..tenancy import audit, check_facility, facility_clause, load_patient

router = APIRouter(tags=["patients"])

ENCOUNTER_COLUMNS = """
    e.id, e.patient_id, e.facility_id, e.external_id, e.admission_type, e.admit_source, e.admit_date,
    e.discharge_date, e.discharge_disposition, (e.discharge_date IS NULL) AS is_active,
    round((extract(epoch FROM coalesce(e.discharge_date, now()) - e.admit_date) / 3600)::numeric, 1)::float AS length_of_stay_hours
"""

SORTS = {
    "name": "p.last_name, p.first_name, p.id",
    "age": "p.age DESC, p.id",
    "recent": "stats.last_encounter_at DESC NULLS LAST, p.id",
    "mrn": "p.mrn",
}

PATIENT_SELECT = """
SELECT p.id, p.mrn, p.name, p.first_name, p.last_name, p.sex, p.age, p.date_of_birth, p.facility_id,
       f.name AS facility_name,
       cur.id AS current_encounter_id,
       coalesce(cur.admission_type, 'not_admitted') AS status,
       stats.last_encounter_at,
       coalesce(stats.encounter_count, 0) AS encounter_count
FROM patients p
JOIN facilities f ON f.id = p.facility_id
LEFT JOIN LATERAL (
    SELECT e.id, e.admission_type FROM encounters e
    WHERE e.patient_id = p.id AND e.discharge_date IS NULL
    ORDER BY e.admit_date DESC LIMIT 1
) cur ON true
LEFT JOIN LATERAL (
    SELECT max(e.admit_date) AS last_encounter_at, count(*) AS encounter_count
    FROM encounters e WHERE e.patient_id = p.id
) stats ON true
"""


@router.get("/api/reference", tags=["reference"])
def reference(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Labels, units and normal ranges for vitals, labs and medications."""
    return reference_payload()


@router.get("/api/patients", response_model=Page[PatientSummary])
def list_patients(
    q: str | None = Query(None, description="Search by name or MRN"),
    facility_id: int | None = None,
    status: Literal["any", "admitted", "inpatient", "observation", "emergency", "outpatient", "not_admitted"] = "any",
    sex: Literal["female", "male"] | None = None,
    sort: Literal["name", "age", "recent", "mrn"] = "recent",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> Page[PatientSummary]:
    check_facility(conn, user, facility_id)
    params: dict = {"tenant_id": user.tenant_id, "facility_id": facility_id,
                    "limit": page_size, "offset": (page - 1) * page_size}
    where = "WHERE p.tenant_id = %(tenant_id)s" + facility_clause("p", facility_id)
    if q:
        where += " AND (p.name ILIKE %(q)s OR p.mrn ILIKE %(q)s)"
        params["q"] = f"%{q.strip()}%"
    if sex:
        where += " AND p.sex = %(sex)s"
        params["sex"] = sex
    if status == "admitted":
        where += " AND cur.id IS NOT NULL"
    elif status == "not_admitted":
        where += " AND cur.id IS NULL"
    elif status != "any":
        where += " AND cur.admission_type = %(status)s"
        params["status"] = status
    rows = fetch_all(
        conn,
        f"SELECT *, count(*) OVER () AS total FROM ({PATIENT_SELECT} {where}) x "
        f"ORDER BY {SORTS[sort].replace('p.', 'x.').replace('stats.', 'x.')} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    )
    total = rows[0]["total"] if rows else 0
    return Page(items=[PatientSummary(**r) for r in rows], total=total, page=page, page_size=page_size)


def _patient_summary(conn: Connection, user: CurrentUser, patient_id: int) -> dict:
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant
    return fetch_one(conn, PATIENT_SELECT + " WHERE p.id = %s AND p.tenant_id = %s", (patient_id, user.tenant_id))


@router.get("/api/patients/{patient_id}", response_model=PatientDetail)
def get_patient(patient_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> PatientDetail:
    summary = _patient_summary(conn, user, patient_id)
    current = None
    if summary["current_encounter_id"]:
        current = fetch_one(conn, f"SELECT {ENCOUNTER_COLUMNS} FROM encounters e WHERE e.id = %s", (summary["current_encounter_id"],))
    latest = fetch_all(
        conn,
        """SELECT DISTINCT ON (measurement) id, encounter_id, measurement, value, unit, recorded_at, previous_value
           FROM (SELECT v.*, lead(v.value) OVER (PARTITION BY v.measurement ORDER BY v.recorded_at DESC) AS previous_value
                 FROM vitals v WHERE v.patient_id = %s AND v.tenant_id = %s) t
           ORDER BY measurement, recorded_at DESC""",
        (patient_id, user.tenant_id),
    )
    counts = fetch_one(
        conn,
        """SELECT
             (SELECT count(*) FROM medications m WHERE m.patient_id = %(p)s AND m.tenant_id = %(t)s AND m.status = 'active') AS active_medications,
             (SELECT count(*) FROM labs l WHERE l.patient_id = %(p)s AND l.tenant_id = %(t)s AND l.flag <> 'N'
                AND l.resulted_at > (SELECT max(resulted_at) FROM labs WHERE patient_id = %(p)s) - interval '7 days') AS abnormal_labs_7d""",
        {"p": patient_id, "t": user.tenant_id},
    )
    audit(conn, user, "patient.view", f"patient:{patient_id}")
    order = list(VITAL_LABELS)
    latest.sort(key=lambda r: order.index(r["measurement"]) if r["measurement"] in order else 99)
    return PatientDetail(
        **summary,
        current_encounter=EncounterOut(**current) if current else None,
        latest_vitals=[
            LatestVital(**r, label=VITAL_LABELS.get(r["measurement"], r["measurement"]),
                        status=vital_status(r["measurement"], r["value"]))
            for r in latest
        ],
        **counts,
    )


@router.get("/api/patients/{patient_id}/encounters", response_model=list[EncounterOut])
def patient_encounters(patient_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> list[EncounterOut]:
    load_patient(conn, user, patient_id)
    rows = fetch_all(
        conn,
        f"SELECT {ENCOUNTER_COLUMNS} FROM encounters e WHERE e.patient_id = %s AND e.tenant_id = %s ORDER BY e.admit_date DESC",
        (patient_id, user.tenant_id),
    )
    return [EncounterOut(**r) for r in rows]


@router.get("/api/patients/{patient_id}/vitals", response_model=list[VitalOut])
def patient_vitals(
    patient_id: int,
    measurement: str | None = None,
    encounter_id: int | None = None,
    since: datetime | None = Query(None, description="Only readings at or after this time"),
    limit: int = Query(1000, ge=1, le=10000),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> list[VitalOut]:
    """Readings oldest first, ready to chart. Without `since`, returns the most recent `limit`."""
    load_patient(conn, user, patient_id)
    where = "v.patient_id = %(p)s AND v.tenant_id = %(t)s"
    params = {"p": patient_id, "t": user.tenant_id, "limit": limit}
    if measurement:
        where += " AND v.measurement = %(m)s"
        params["m"] = measurement
    if encounter_id:
        where += " AND v.encounter_id = %(e)s"
        params["e"] = encounter_id
    if since:
        where += " AND v.recorded_at >= %(since)s"
        params["since"] = since
    rows = fetch_all(
        conn,
        f"""SELECT * FROM (SELECT v.id, v.encounter_id, v.measurement, v.value, v.unit, v.recorded_at
                           FROM vitals v WHERE {where} ORDER BY v.recorded_at DESC LIMIT %(limit)s) r
            ORDER BY recorded_at""",
        params,
    )
    return [VitalOut(**r, label=VITAL_LABELS.get(r["measurement"], r["measurement"])) for r in rows]


@router.get("/api/patients/{patient_id}/labs", response_model=list[LabOut])
def patient_labs(
    patient_id: int,
    test_code: str | None = None,
    panel: str | None = None,
    abnormal: bool = False,
    encounter_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> list[LabOut]:
    """Results newest first."""
    load_patient(conn, user, patient_id)
    where = "l.patient_id = %(p)s AND l.tenant_id = %(t)s"
    params = {"p": patient_id, "t": user.tenant_id, "limit": limit}
    if test_code:
        where += " AND l.test_code = %(code)s"
        params["code"] = test_code
    if panel:
        where += " AND l.panel = %(panel)s"
        params["panel"] = panel
    if abnormal:
        where += " AND l.flag <> 'N'"
    if encounter_id:
        where += " AND l.encounter_id = %(e)s"
        params["e"] = encounter_id
    rows = fetch_all(
        conn,
        f"""SELECT l.id, l.encounter_id, l.test_code, l.test_name, l.loinc, l.panel, l.value, l.unit,
                   l.ref_low, l.ref_high, l.flag, l.collected_at, l.resulted_at
            FROM labs l WHERE {where} ORDER BY l.collected_at DESC, l.id LIMIT %(limit)s""",
        params,
    )
    return [LabOut(**r) for r in rows]


@router.get("/api/patients/{patient_id}/medications", response_model=list[MedicationOut])
def patient_medications(
    patient_id: int,
    status: Literal["any", "active", "discontinued"] = "any",
    encounter_id: int | None = None,
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> list[MedicationOut]:
    load_patient(conn, user, patient_id)
    where = "m.patient_id = %(p)s AND m.tenant_id = %(t)s"
    params = {"p": patient_id, "t": user.tenant_id}
    if status != "any":
        where += " AND m.status = %(s)s"
        params["s"] = status
    if encounter_id:
        where += " AND m.encounter_id = %(e)s"
        params["e"] = encounter_id
    rows = fetch_all(
        conn,
        f"""SELECT m.id, m.encounter_id, m.external_id, m.drug_code, m.drug_name, m.drug_class, m.dose, m.dose_unit,
                   m.route, m.frequency, m.status, m.ordered_at, m.ended_at,
                   count(a.id) AS administrations, max(a.administered_at) AS last_administered_at
            FROM medications m
            LEFT JOIN medication_administrations a ON a.medication_id = m.id
            WHERE {where}
            GROUP BY m.id
            ORDER BY (m.status = 'active') DESC, m.ordered_at DESC""",
        params,
    )
    return [MedicationOut(**r) for r in rows]


@router.get("/api/patients/{patient_id}/timeline", response_model=list[TimelineItem])
def patient_timeline(
    patient_id: int,
    encounter_id: int | None = None,
    limit: int = Query(80, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> list[TimelineItem]:
    """Everything that happened to one patient, newest first: visits, vital sets, lab panels, medications."""
    load_patient(conn, user, patient_id)
    enc = " AND encounter_id = %(e)s" if encounter_id else ""
    enc_e = " AND e.id = %(e)s" if encounter_id else ""
    rows = fetch_all(
        conn,
        f"""
        SELECT * FROM (
          SELECT e.admit_date AS at, 'admission' AS kind,
                 initcap(e.admission_type) || ' visit started' AS title,
                 'Source: ' || replace(e.admit_source, '_', ' ') AS detail, e.id AS encounter_id, false AS abnormal
          FROM encounters e WHERE e.patient_id = %(p)s AND e.tenant_id = %(t)s {enc_e}
          UNION ALL
          SELECT e.discharge_date, 'discharge', initcap(e.admission_type) || ' visit ended',
                 'Disposition: ' || replace(coalesce(e.discharge_disposition, 'unknown'), '_', ' '), e.id,
                 e.discharge_disposition IN ('expired', 'left_against_advice')
          FROM encounters e WHERE e.patient_id = %(p)s AND e.tenant_id = %(t)s AND e.discharge_date IS NOT NULL {enc_e}
          UNION ALL
          SELECT recorded_at, 'vitals', 'Vital signs recorded',
                 string_agg(measurement || ' ' || value, ', ' ORDER BY measurement), max(encounter_id), false
          FROM vitals WHERE patient_id = %(p)s AND tenant_id = %(t)s {enc}
          GROUP BY recorded_at
          UNION ALL
          SELECT resulted_at, 'lab', max(panel) || ' resulted',
                 coalesce(string_agg(test_name || ' ' || value || ' ' || unit || ' (' || flag || ')', ', ') FILTER (WHERE flag <> 'N'), 'All results in range'),
                 max(encounter_id), bool_or(flag <> 'N')
          FROM labs WHERE patient_id = %(p)s AND tenant_id = %(t)s {enc}
          GROUP BY resulted_at, panel
          UNION ALL
          SELECT ordered_at, 'medication_order', drug_name || ' ordered',
                 dose || ' ' || dose_unit || ' ' || route || ' ' || frequency, encounter_id, false
          FROM medications WHERE patient_id = %(p)s AND tenant_id = %(t)s {enc}
        ) t ORDER BY at DESC LIMIT %(limit)s
        """,
        {"p": patient_id, "t": user.tenant_id, "e": encounter_id, "limit": limit},
    )
    return [TimelineItem(**r) for r in rows]
