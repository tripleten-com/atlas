"""Caseload numbers across one facility or the whole organisation.

Time windows ("last 24 hours") are measured back from `as_of`, the newest data point in scope,
rather than from the wall clock. For live facilities the two are the same; for the static Test
Facility this keeps the dashboard meaningful however long ago it was seeded.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_all, fetch_one, get_db
from ..reference import LAB_LABELS
from ..schemas import CountItem, DailyPoint, DashboardBreakdown, DashboardSummary
from ..tenancy import check_facility, facility_clause

router = APIRouter(tags=["dashboard"])


def _as_of(conn: Connection, tenant_id: int, facility_id: int | None) -> dict:
    clause = facility_clause("e", facility_id)
    row = fetch_one(
        conn,
        f"""SELECT greatest(
                 (SELECT max(e.admit_date) FROM encounters e WHERE e.tenant_id = %(t)s {clause}),
                 (SELECT max(e.discharge_date) FROM encounters e WHERE e.tenant_id = %(t)s {clause})
               ) AS as_of""",
        {"t": tenant_id, "facility_id": facility_id},
    )
    return row


@router.get("/api/dashboard/summary", response_model=DashboardSummary)
def summary(facility_id: int | None = None, user: CurrentUser = Depends(get_current_user),
            conn: Connection = Depends(get_db)) -> DashboardSummary:
    check_facility(conn, user, facility_id)
    as_of = _as_of(conn, user.tenant_id, facility_id)["as_of"]
    p = {"t": user.tenant_id, "facility_id": facility_id, "as_of": as_of}
    fe, fl, fv, fp = (facility_clause(a, facility_id) for a in ("e", "l", "v", "p"))
    row = fetch_one(
        conn,
        f"""SELECT
          (SELECT count(*) FROM patients p WHERE p.tenant_id = %(t)s {fp}) AS patients_total,
          (SELECT count(*) FILTER (WHERE admission_type = 'inpatient') FROM encounters e WHERE e.tenant_id = %(t)s AND e.discharge_date IS NULL {fe}) AS c_inpatient,
          (SELECT count(*) FILTER (WHERE admission_type = 'observation') FROM encounters e WHERE e.tenant_id = %(t)s AND e.discharge_date IS NULL {fe}) AS c_observation,
          (SELECT count(*) FILTER (WHERE admission_type = 'emergency') FROM encounters e WHERE e.tenant_id = %(t)s AND e.discharge_date IS NULL {fe}) AS c_emergency,
          (SELECT count(*) FILTER (WHERE admission_type = 'outpatient') FROM encounters e WHERE e.tenant_id = %(t)s AND e.discharge_date IS NULL {fe}) AS c_outpatient,
          (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe}
             AND e.admission_type IN ('inpatient', 'observation') AND e.admit_date > %(as_of)s - interval '24 hours') AS admissions_24h,
          (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe}
             AND e.admission_type IN ('inpatient', 'observation') AND e.discharge_date > %(as_of)s - interval '24 hours') AS discharges_24h,
          (SELECT count(*) FROM labs l WHERE l.tenant_id = %(t)s {fl} AND l.flag <> 'N' AND l.resulted_at > %(as_of)s - interval '24 hours') AS abnormal_labs_24h,
          (SELECT count(*) FROM vitals v WHERE v.tenant_id = %(t)s {fv} AND v.recorded_at > %(as_of)s - interval '24 hours') AS vitals_24h,
          (SELECT round(avg(extract(epoch FROM e.discharge_date - e.admit_date) / 86400)::numeric, 2)::float
             FROM encounters e WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'inpatient'
             AND e.discharge_date > %(as_of)s - interval '30 days') AS avg_length_of_stay_days_30d,
          (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.age) FROM patients p WHERE p.tenant_id = %(t)s {fp}) AS median_age
        """,
        p,
    )
    readmit = fetch_one(
        conn,
        f"""WITH idx AS (
              SELECT e.id, e.patient_id, e.discharge_date FROM encounters e
              WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'inpatient'
                AND e.discharge_date BETWEEN %(as_of)s - interval '60 days' AND %(as_of)s - interval '30 days'
                AND coalesce(e.discharge_disposition, '') <> 'expired')
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE EXISTS (
                     SELECT 1 FROM encounters r WHERE r.patient_id = idx.patient_id AND r.admission_type = 'inpatient'
                       AND r.admit_date > idx.discharge_date AND r.admit_date <= idx.discharge_date + interval '30 days')) AS readmitted
            FROM idx""",
        p,
    )
    return DashboardSummary(
        as_of=as_of or datetime.now(timezone.utc),
        patients_total=row["patients_total"],
        census={"inpatient": row["c_inpatient"] or 0, "observation": row["c_observation"] or 0,
                "emergency": row["c_emergency"] or 0, "outpatient": row["c_outpatient"] or 0},
        admissions_24h=row["admissions_24h"], discharges_24h=row["discharges_24h"],
        abnormal_labs_24h=row["abnormal_labs_24h"], vitals_24h=row["vitals_24h"],
        avg_length_of_stay_days_30d=row["avg_length_of_stay_days_30d"],
        readmission_rate_30d=round(readmit["readmitted"] / readmit["n"], 3) if readmit["n"] else None,
        median_age=row["median_age"],
    )


@router.get("/api/dashboard/daily", response_model=list[DailyPoint])
def daily(facility_id: int | None = None, days: int = Query(30, ge=1, le=365),
          user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> list[DailyPoint]:
    """One row per day: admissions, discharges, ED and outpatient visits."""
    check_facility(conn, user, facility_id)
    as_of = _as_of(conn, user.tenant_id, facility_id)["as_of"]
    if as_of is None:
        return []
    fe = facility_clause("e", facility_id)
    rows = fetch_all(
        conn,
        f"""WITH days AS (
              SELECT generate_series(date_trunc('day', %(as_of)s::timestamptz) - (%(days)s - 1) * interval '1 day',
                                     date_trunc('day', %(as_of)s::timestamptz), interval '1 day') AS day)
            SELECT d.day::date AS day,
              (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe} AND e.admission_type IN ('inpatient','observation')
                 AND e.admit_date >= d.day AND e.admit_date < d.day + interval '1 day') AS admissions,
              (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe} AND e.admission_type IN ('inpatient','observation')
                 AND e.discharge_date >= d.day AND e.discharge_date < d.day + interval '1 day') AS discharges,
              (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'emergency'
                 AND e.admit_date >= d.day AND e.admit_date < d.day + interval '1 day') AS emergency_visits,
              (SELECT count(*) FROM encounters e WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'outpatient'
                 AND e.admit_date >= d.day AND e.admit_date < d.day + interval '1 day') AS outpatient_visits
            FROM days d ORDER BY d.day""",
        {"t": user.tenant_id, "facility_id": facility_id, "as_of": as_of, "days": days},
    )
    return [DailyPoint(**r) for r in rows]


def _labelled(rows: list[dict], labels: dict | None = None) -> list[CountItem]:
    out = []
    for r in rows:
        key = str(r["key"]) if r["key"] is not None else "unknown"
        label = (labels or {}).get(key) or key.replace("_", " ").capitalize()
        out.append(CountItem(key=key, label=label, count=r["count"]))
    return out


@router.get("/api/dashboard/breakdown", response_model=DashboardBreakdown)
def breakdown(facility_id: int | None = None, user: CurrentUser = Depends(get_current_user),
              conn: Connection = Depends(get_db)) -> DashboardBreakdown:
    check_facility(conn, user, facility_id)
    as_of = _as_of(conn, user.tenant_id, facility_id)["as_of"]
    p = {"t": user.tenant_id, "facility_id": facility_id, "as_of": as_of}
    fe, fl, fp = (facility_clause(a, facility_id) for a in ("e", "l", "p"))
    types = fetch_all(conn, f"""SELECT admission_type AS key, count(*) FROM encounters e
                               WHERE e.tenant_id = %(t)s {fe} AND e.admit_date > %(as_of)s - interval '90 days'
                               GROUP BY 1 ORDER BY 2 DESC""", p)
    sources = fetch_all(conn, f"""SELECT admit_source AS key, count(*) FROM encounters e
                                 WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'inpatient'
                                 AND e.admit_date > %(as_of)s - interval '90 days' GROUP BY 1 ORDER BY 2 DESC""", p)
    ages = fetch_all(conn, f"""SELECT CASE WHEN age < 30 THEN '18-29' WHEN age < 45 THEN '30-44' WHEN age < 65 THEN '45-64'
                                          WHEN age < 80 THEN '65-79' ELSE '80+' END AS key, count(*)
                              FROM patients p WHERE p.tenant_id = %(t)s {fp} GROUP BY 1 ORDER BY 1""", p)
    tests = fetch_all(conn, f"""SELECT test_code AS key, count(*) FROM labs l
                               WHERE l.tenant_id = %(t)s {fl} AND l.flag <> 'N' AND l.resulted_at > %(as_of)s - interval '7 days'
                               GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", p)
    dispositions = fetch_all(conn, f"""SELECT discharge_disposition AS key, count(*) FROM encounters e
                                      WHERE e.tenant_id = %(t)s {fe} AND e.admission_type = 'inpatient'
                                      AND e.discharge_date > %(as_of)s - interval '30 days' GROUP BY 1 ORDER BY 2 DESC""", p)
    return DashboardBreakdown(
        admission_types=_labelled(types),
        admit_sources=_labelled(sources, {"emergency_department": "Emergency department"}),
        age_bands=[CountItem(key=r["key"], label=r["key"], count=r["count"]) for r in ages],
        abnormal_tests_7d=_labelled(tests, LAB_LABELS),
        dispositions_30d=_labelled(dispositions),
    )
