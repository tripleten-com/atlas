"""Turn one stored Meridian event into rows in the clinical tables.

Each handler either writes its rows or raises:
  * `Deferred` when the event refers to something Atlas has not seen yet (a vital for a visit
    whose admission is still on its way). The worker retries it later.
  * `Invalid` when the event can never be applied. The worker marks it failed.

Handlers must be safe to run exactly once per event. The worker guarantees "once" by marking
the stored event processed in the same transaction as the rows it wrote.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from psycopg import Connection

from ..db import fetch_one


class Deferred(Exception):
    """A referenced patient, visit or order is not in Atlas yet."""


class Invalid(Exception):
    """The event is malformed or contradicts what Atlas holds."""


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _age(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


class Context:
    def __init__(self, conn: Connection, connection: dict, event: dict, ingest_id: int):
        self.conn = conn
        self.tenant_id = connection["tenant_id"]
        self.facility_id = connection["facility_id"]
        self.event = event
        self.data = event.get("data") or {}
        self.ingest_id = ingest_id

    def patient_id(self) -> int:
        mrn = (self.event.get("patient") or {}).get("mrn")
        if not mrn:
            raise Invalid("event has no patient.mrn")
        row = fetch_one(self.conn, "SELECT id FROM patients WHERE facility_id = %s AND mrn = %s", (self.facility_id, mrn))
        if row is None:
            raise Deferred(f"patient {mrn} not registered yet")
        return row["id"]

    def encounter_id(self, required: bool = True) -> int | None:
        ref = self.event.get("encounter") or {}
        visit = ref.get("visit_id")
        if not visit:
            if required:
                raise Invalid("event has no encounter.visit_id")
            return None
        row = fetch_one(self.conn, "SELECT id FROM encounters WHERE facility_id = %s AND external_id = %s", (self.facility_id, visit))
        if row is None:
            raise Deferred(f"visit {visit} not admitted yet")
        return row["id"]


def patient_registered(ctx: Context) -> None:
    d = ctx.data
    dob = date.fromisoformat(d["date_of_birth"])
    occurred = _ts(ctx.event["occurred_at"])
    ctx.conn.execute(
        """INSERT INTO patients (tenant_id, facility_id, mrn, name, first_name, last_name, sex, date_of_birth, age, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (facility_id, mrn) DO UPDATE
             SET first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, name = EXCLUDED.name,
                 sex = EXCLUDED.sex, date_of_birth = EXCLUDED.date_of_birth""",
        (ctx.tenant_id, ctx.facility_id, d["mrn"], f"{d['first_name']} {d['last_name']}", d["first_name"], d["last_name"],
         d["sex"], dob, _age(dob, occurred.date()), occurred),
    )


def encounter_admitted(ctx: Context) -> None:
    d = ctx.data
    patient_id = ctx.patient_id()
    ctx.conn.execute(
        """INSERT INTO encounters (tenant_id, facility_id, patient_id, external_id, admission_type, admit_source, admit_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (facility_id, external_id) DO UPDATE
             SET admission_type = EXCLUDED.admission_type, admit_source = EXCLUDED.admit_source, admit_date = EXCLUDED.admit_date""",
        (ctx.tenant_id, ctx.facility_id, patient_id, d["visit_id"], d["admission_type"], d["admit_source"], _ts(d["admitted_at"])),
    )


def encounter_discharged(ctx: Context) -> None:
    d = ctx.data
    encounter_id = ctx.encounter_id()
    ctx.conn.execute(
        "UPDATE encounters SET discharge_date = %s, discharge_disposition = %s WHERE id = %s",
        (_ts(d["discharged_at"]), d.get("disposition"), encounter_id),
    )


def vital_signs_recorded(ctx: Context) -> None:
    d = ctx.data
    patient_id, encounter_id = ctx.patient_id(), ctx.encounter_id()
    recorded = _ts(d["recorded_at"])
    rows = [
        (ctx.tenant_id, ctx.facility_id, patient_id, encounter_id, m["code"], m["value"], m["unit"], recorded, ctx.ingest_id)
        for m in d.get("measurements", []) if m.get("value") is not None
    ]
    if not rows:
        raise Invalid("vital set has no measurements")
    with ctx.conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO vitals (tenant_id, facility_id, patient_id, encounter_id, measurement, value, unit, recorded_at, source_event_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )


def lab_resulted(ctx: Context) -> None:
    d = ctx.data
    patient_id, encounter_id = ctx.patient_id(), ctx.encounter_id()
    collected, resulted = _ts(d["collected_at"]), _ts(d["resulted_at"])
    rows = [
        (ctx.tenant_id, ctx.facility_id, patient_id, encounter_id, r["code"], r["loinc"], r["label"], d["panel"],
         r["value"], r["unit"], r.get("ref_low"), r.get("ref_high"), r["flag"], collected, resulted, ctx.ingest_id)
        for r in d.get("results", [])
    ]
    if not rows:
        raise Invalid("lab panel has no results")
    with ctx.conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO labs (tenant_id, facility_id, patient_id, encounter_id, test_code, loinc, test_name, panel, value, unit,
                                 ref_low, ref_high, flag, collected_at, resulted_at, source_event_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )


def medication_ordered(ctx: Context) -> None:
    d = ctx.data
    patient_id, encounter_id = ctx.patient_id(), ctx.encounter_id()
    ctx.conn.execute(
        """INSERT INTO medications (tenant_id, facility_id, patient_id, encounter_id, external_id, drug_code, drug_name, drug_class,
                                    dose, dose_unit, route, frequency, status, ordered_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
           ON CONFLICT (tenant_id, external_id) DO NOTHING""",
        (ctx.tenant_id, ctx.facility_id, patient_id, encounter_id, d["order_id"], d["code"], d["name"], d["drug_class"],
         d["dose"], d["dose_unit"], d["route"], d["frequency"], _ts(d["ordered_at"])),
    )


def _medication(ctx: Context) -> dict:
    row = fetch_one(ctx.conn, "SELECT id, patient_id, encounter_id FROM medications WHERE tenant_id = %s AND external_id = %s",
                    (ctx.tenant_id, ctx.data["order_id"]))
    if row is None:
        raise Deferred(f"order {ctx.data['order_id']} not received yet")
    return row


def medication_administered(ctx: Context) -> None:
    d = ctx.data
    med = _medication(ctx)
    ctx.conn.execute(
        """INSERT INTO medication_administrations (tenant_id, facility_id, medication_id, patient_id, encounter_id,
                                                   administered_at, dose, dose_unit, source_event_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (ctx.tenant_id, ctx.facility_id, med["id"], med["patient_id"], med["encounter_id"], _ts(d["administered_at"]),
         d["dose"], d["dose_unit"], ctx.ingest_id),
    )


def medication_discontinued(ctx: Context) -> None:
    med = _medication(ctx)
    ctx.conn.execute(
        "UPDATE medications SET status = 'discontinued', ended_at = %s WHERE id = %s",
        (_ts(ctx.data["discontinued_at"]), med["id"]),
    )


HANDLERS: dict[str, Callable[[Context], None]] = {
    "patient.registered": patient_registered,
    "encounter.admitted": encounter_admitted,
    "encounter.discharged": encounter_discharged,
    "vital_signs.recorded": vital_signs_recorded,
    "lab.resulted": lab_resulted,
    "medication.ordered": medication_ordered,
    "medication.administered": medication_administered,
    "medication.discontinued": medication_discontinued,
}


def apply_event(conn: Connection, connection: dict, event: dict, ingest_id: int) -> bool:
    """Apply one event. Returns False when Atlas has no handler for its type (the event is skipped)."""
    handler = HANDLERS.get(event.get("type"))
    if handler is None:
        return False
    try:
        handler(Context(conn, connection, event, ingest_id))
    except (KeyError, ValueError, TypeError) as exc:
        raise Invalid(f"{exc.__class__.__name__}: {exc}") from exc
    return True


def summarize(event: dict) -> str:
    """One human-readable line for the activity feed and the ingestion log."""
    t, d = event.get("type"), event.get("data") or {}
    mrn = (event.get("patient") or {}).get("mrn", "")
    if t == "patient.registered":
        return f"Registered {d.get('first_name', '')} {d.get('last_name', '')} ({mrn})"
    if t == "encounter.admitted":
        return f"{str(d.get('admission_type', 'visit')).capitalize()} visit started via {str(d.get('admit_source', '')).replace('_', ' ')}"
    if t == "encounter.discharged":
        return f"Visit ended, disposition {str(d.get('disposition', 'unknown')).replace('_', ' ')}"
    if t == "vital_signs.recorded":
        parts = {m["code"]: m["value"] for m in d.get("measurements", [])}
        bits = []
        if "heart_rate" in parts:
            bits.append(f"HR {parts['heart_rate']}")
        if "systolic_bp" in parts and "diastolic_bp" in parts:
            bits.append(f"BP {parts['systolic_bp']}/{parts['diastolic_bp']}")
        if "spo2" in parts:
            bits.append(f"SpO2 {parts['spo2']}%")
        if "temperature" in parts:
            bits.append(f"T {parts['temperature']}°C")
        return "Vitals: " + ", ".join(bits) if bits else "Vitals recorded"
    if t == "lab.resulted":
        abnormal = [f"{r['label']} {r['value']} {r['flag']}" for r in d.get("results", []) if r.get("flag") != "N"]
        return f"{d.get('panel_name', d.get('panel', 'Lab'))} resulted" + (f": {', '.join(abnormal)}" if abnormal else ", all in range")
    if t == "medication.ordered":
        return f"Ordered {d.get('name')} {d.get('dose')} {d.get('dose_unit')} {d.get('route')} {d.get('frequency')}"
    if t == "medication.administered":
        return f"Given {str(d.get('code', '')).replace('_', ' ')} {d.get('dose')} {d.get('dose_unit')}"
    if t == "medication.discontinued":
        return f"Medication order {d.get('order_id')} stopped"
    return t or "unknown event"


def is_abnormal(event: dict) -> bool:
    if event.get("type") == "lab.resulted":
        return any(r.get("flag") != "N" for r in (event.get("data") or {}).get("results", []))
    if event.get("type") == "encounter.discharged":
        return (event.get("data") or {}).get("disposition") in ("expired", "left_against_advice")
    return False
