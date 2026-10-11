"""Seed the demo organisations, their users, and two years of history for the Test Facility.

    python -m app.seed            # safe to run every start: skips whatever already exists
    python -m app.seed --reset    # wipe and rebuild the Test Facility's clinical history

What gets created:

  Riverside Health (tenant)
    Test Facility               static history, seeded here, never changes
    Riverside General Hospital  live, streamed in by the Meridian feed
    Northgate Clinic            live, streamed in by the Meridian feed (outpatient only)
  Harborview Medical Group (tenant)
    Harborview Medical Center   live, streamed in by the Meridian feed

The Test Facility history is produced by the same simulator the feed uses, so seeded and live
data share one shape. The random seed is fixed: every reseed gives the same patients, visits
and measurements, shifted so the history always ends at the moment you seeded.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg

from clinsim import FacilityProfile, Simulator, age_on

from .auth import hash_password
from .config import settings
from .migrate import migrate

DEMO_FACILITY = "DEMO"
SEED_RANDOM = 20260901
SEED_DAYS = 730

TENANTS = [
    {"slug": "riverside", "name": "Riverside Health"},
    {"slug": "harborview", "name": "Harborview Medical Group"},
]

FACILITIES = [
    {"tenant": "riverside", "code": DEMO_FACILITY, "name": "Test Facility", "kind": "demo", "source": "seed", "tz": "America/New_York"},
    {"tenant": "riverside", "code": "RGH", "name": "Riverside General Hospital", "kind": "hospital", "source": "meridian", "tz": "America/New_York"},
    {"tenant": "riverside", "code": "RNC", "name": "Northgate Clinic", "kind": "clinic", "source": "meridian", "tz": "America/New_York"},
    {"tenant": "harborview", "code": "HMC", "name": "Harborview Medical Center", "kind": "hospital", "source": "meridian", "tz": "America/Chicago"},
]

USERS = [
    {"tenant": "riverside", "email": "clinician@riverside.example", "name": "Dr. Maya Chen", "role": "clinician", "title": "Hospitalist"},
    {"tenant": "riverside", "email": "admin@riverside.example", "name": "Jordan Blake", "role": "admin", "title": "Clinical Informatics Lead"},
    {"tenant": "harborview", "email": "clinician@harborview.example", "name": "Dr. Samuel Okoro", "role": "clinician", "title": "Emergency Physician"},
    {"tenant": "harborview", "email": "admin@harborview.example", "name": "Priya Nair", "role": "admin", "title": "IT Director"},
]

DEMO_PROFILE = FacilityProfile(
    code=DEMO_FACILITY, name="Test Facility", pool_size=1250,
    outpatient_per_year=1.8, ed_per_year=0.35, direct_admit_per_year=0.34,
)


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def ensure_org(conn: psycopg.Connection) -> dict[str, int]:
    """Tenants, facilities, users and feed connections. Returns facility code -> id."""
    tenant_ids = {}
    for t in TENANTS:
        row = conn.execute(
            "INSERT INTO tenants (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (t["slug"], t["name"]),
        ).fetchone()
        tenant_ids[t["slug"]] = row[0]
    facility_ids = {}
    for f in FACILITIES:
        row = conn.execute(
            """INSERT INTO facilities (tenant_id, code, name, kind, timezone, data_source)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
            (tenant_ids[f["tenant"]], f["code"], f["name"], f["kind"], f["tz"], f["source"]),
        ).fetchone()
        facility_ids[f["code"]] = row[0]
        if f["source"] == "meridian":
            conn.execute(
                """INSERT INTO integration_connections (tenant_id, facility_id, webhook_secret)
                   VALUES (%s, %s, %s) ON CONFLICT (facility_id) DO NOTHING""",
                (tenant_ids[f["tenant"]], row[0], secrets.token_urlsafe(32)),
            )
    for u in USERS:
        exists = conn.execute("SELECT 1 FROM users WHERE email = %s", (u["email"],)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (tenant_id, email, name, role, title, password_hash) VALUES (%s, %s, %s, %s, %s, %s)",
                (tenant_ids[u["tenant"]], u["email"], u["name"], u["role"], u["title"], hash_password(settings.demo_password)),
            )
    return facility_ids


def wipe_demo(conn: psycopg.Connection, facility_id: int) -> None:
    log("removing the Test Facility's clinical history")
    pts = "SELECT id FROM patients WHERE facility_id = %(f)s"
    for table in ("medication_administrations", "medications", "labs", "vitals"):
        conn.execute(f"DELETE FROM {table} WHERE patient_id IN ({pts})", {"f": facility_id})
    conn.execute("DELETE FROM encounters WHERE facility_id = %(f)s", {"f": facility_id})
    conn.execute("DELETE FROM patients WHERE facility_id = %(f)s", {"f": facility_id})


def _ids(conn: psycopg.Connection, sequence: str, n: int) -> list[int]:
    if n == 0:
        return []
    return [r[0] for r in conn.execute("SELECT nextval(%s) FROM generate_series(1, %s)", (sequence, n))]


def seed_demo_history(conn: psycopg.Connection, tenant_id: int, facility_id: int) -> dict[str, int]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=SEED_DAYS)
    sim = Simulator(DEMO_PROFILE, seed=SEED_RANDOM, start=start)
    log(f"simulating {SEED_DAYS} days of activity ({start:%Y-%m-%d} to {end:%Y-%m-%d})")

    patients: dict[str, dict] = {}
    encounters: dict[str, dict] = {}
    meds: dict[str, dict] = {}
    vitals: list[tuple] = []
    labs: list[tuple] = []
    admins: list[tuple] = []

    for _ in range(SEED_DAYS * 24):
        for ev in sim.advance():
            kind, d = ev["type"], ev["data"]
            mrn = ev["patient"]["mrn"]
            visit = ev["encounter"]["visit_id"] if ev["encounter"] else None
            at = ev["occurred_at"]
            if kind == "patient.registered":
                dob = datetime.fromisoformat(d["date_of_birth"]).date()
                patients[mrn] = {"mrn": mrn, "first": d["first_name"], "last": d["last_name"], "sex": d["sex"],
                                 "dob": dob, "age": age_on(dob, at.date()), "created": at}
            elif kind == "encounter.admitted":
                encounters[visit] = {"mrn": mrn, "type": d["admission_type"], "source": d["admit_source"],
                                     "admit": at, "discharge": None, "disposition": None}
            elif kind == "encounter.discharged":
                encounters[visit]["discharge"] = at
                encounters[visit]["disposition"] = d["disposition"]
            elif kind == "vital_signs.recorded":
                for m in d["measurements"]:
                    vitals.append((mrn, visit, m["code"], m["value"], m["unit"], at))
            elif kind == "lab.resulted":
                collected = datetime.fromisoformat(d["collected_at"])
                for r in d["results"]:
                    labs.append((mrn, visit, r["code"], r["loinc"], r["label"], d["panel"], r["value"], r["unit"],
                                 r["ref_low"], r["ref_high"], r["flag"], collected, at))
            elif kind == "medication.ordered":
                meds[d["order_id"]] = {"mrn": mrn, "visit": visit, "d": d, "ordered": at, "ended": None}
            elif kind == "medication.discontinued":
                meds[d["order_id"]]["ended"] = at
            elif kind == "medication.administered":
                admins.append((d["order_id"], mrn, visit, at, d["dose"], d["dose_unit"]))

    log(f"writing {len(patients):,} patients, {len(encounters):,} encounters, {len(vitals):,} vitals, "
        f"{len(labs):,} labs, {len(meds):,} medication orders, {len(admins):,} administrations")

    for key, pid in zip(patients, _ids(conn, "patients_id_seq", len(patients))):
        patients[key]["id"] = pid
    for key, eid in zip(encounters, _ids(conn, "encounters_id_seq", len(encounters))):
        encounters[key]["id"] = eid
    for key, mid in zip(meds, _ids(conn, "medications_id_seq", len(meds))):
        meds[key]["id"] = mid

    with conn.cursor() as cur:
        with cur.copy("COPY patients (id, tenant_id, facility_id, mrn, name, first_name, last_name, sex, date_of_birth, age, created_at) FROM STDIN") as cp:
            for p in patients.values():
                cp.write_row((p["id"], tenant_id, facility_id, p["mrn"], f"{p['first']} {p['last']}", p["first"], p["last"],
                              p["sex"], p["dob"], p["age"], p["created"]))
        with cur.copy("COPY encounters (id, tenant_id, facility_id, patient_id, external_id, admission_type, admit_source, admit_date, discharge_date, discharge_disposition) FROM STDIN") as cp:
            for visit, e in encounters.items():
                cp.write_row((e["id"], tenant_id, facility_id, patients[e["mrn"]]["id"], visit, e["type"], e["source"],
                              e["admit"], e["discharge"], e["disposition"]))
        with cur.copy("COPY vitals (tenant_id, facility_id, patient_id, encounter_id, measurement, value, unit, recorded_at) FROM STDIN") as cp:
            for mrn, visit, code, value, unit, at in vitals:
                cp.write_row((tenant_id, facility_id, patients[mrn]["id"], encounters[visit]["id"], code, value, unit, at))
        with cur.copy("COPY labs (tenant_id, facility_id, patient_id, encounter_id, test_code, loinc, test_name, panel, value, unit, ref_low, ref_high, flag, collected_at, resulted_at) FROM STDIN") as cp:
            for mrn, visit, *rest in labs:
                cp.write_row((tenant_id, facility_id, patients[mrn]["id"], encounters[visit]["id"], *rest))
        with cur.copy("COPY medications (id, tenant_id, facility_id, patient_id, encounter_id, external_id, drug_code, drug_name, drug_class, dose, dose_unit, route, frequency, status, ordered_at, ended_at) FROM STDIN") as cp:
            for order_id, m in meds.items():
                d = m["d"]
                cp.write_row((m["id"], tenant_id, facility_id, patients[m["mrn"]]["id"], encounters[m["visit"]]["id"], order_id,
                              d["code"], d["name"], d["drug_class"], d["dose"], d["dose_unit"], d["route"], d["frequency"],
                              "discontinued" if m["ended"] else "active", m["ordered"], m["ended"]))
        with cur.copy("COPY medication_administrations (tenant_id, facility_id, medication_id, patient_id, encounter_id, administered_at, dose, dose_unit) FROM STDIN") as cp:
            for order_id, mrn, visit, at, dose, unit in admins:
                cp.write_row((tenant_id, facility_id, meds[order_id]["id"], patients[mrn]["id"], encounters[visit]["id"], at, dose, unit))
    return {"patients": len(patients), "encounters": len(encounters), "vitals": len(vitals), "labs": len(labs),
            "medications": len(meds), "medication_administrations": len(admins)}


def run(reset: bool = False) -> None:
    started = time.monotonic()
    migrate()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(424242)")  # two containers starting at once must not both seed
        try:
            with conn.transaction():
                facility_ids = ensure_org(conn)
            log("organisations, facilities, users and feed connections are in place")
            if not settings.seed_demo:
                log("SEED_DEMO is off, skipping the Test Facility history")
                return
            demo_id = facility_ids[DEMO_FACILITY]
            tenant_id = conn.execute("SELECT tenant_id FROM facilities WHERE id = %s", (demo_id,)).fetchone()[0]
            with conn.transaction():
                if reset:
                    wipe_demo(conn, demo_id)
                existing = conn.execute("SELECT count(*) FROM patients WHERE facility_id = %s", (demo_id,)).fetchone()[0]
                if existing:
                    log(f"Test Facility already holds {existing:,} patients, leaving it alone (use --reset to rebuild)")
                    return
                counts = seed_demo_history(conn, tenant_id, demo_id)
            conn.execute("ANALYZE")
            log(f"done in {time.monotonic() - started:.0f}s: " + ", ".join(f"{k}={v:,}" for k, v in counts.items()))
        finally:
            conn.execute("SELECT pg_advisory_unlock(424242)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="wipe and rebuild the Test Facility history")
    args = parser.parse_args()
    run(reset=args.reset)
    sys.exit(0)
