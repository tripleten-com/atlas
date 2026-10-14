"""Export the early-warning training set from the Test Facility.

    python training/export_vitals_hourly.py
    DATABASE_URL=postgresql://atlas:atlas@localhost:5433/atlas python training/export_vitals_hourly.py

Reads Postgres, keeps every discharged inpatient encounter at the Test Facility (code DEMO),
and writes three files into training/data/:

    vitals_hourly.csv           one row per encounter-hour: encounter_id, hour_index, the six
                                vitals as recorded in that hour (mean when several, empty when
                                none). Every hour of every stay is present, measured or not.
    encounters_static.csv       one row per encounter: patient_id, age, sex, admission_type,
                                admit_date, discharge_date, hours_total, onset_hour (empty when the
                                stay never crossed the deterioration line)
    deterioration.columns.json  the feature order and the onset rule the label was built with

The onset rule and the hourly grid come from training/deterioration.py, the same rules the API
repeats in backend/app/ml_deterioration.py, so the label here and the "already deteriorating"
state on the card agree.

Why only discharged stays: an open stay has no final length, so its later windows cannot be
labelled yet. Why deaths are kept: a deterioration often precedes one, and the question is
about the vitals crossing the line, not about what happened after. (The readmission model
excludes deaths for its own reason; the two models do not have to agree on this.)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `deterioration` imports from any cwd

from deterioration import (  # noqa: E402
    FIRST_AS_OF_HOUR,
    HORIZON_HOURS,
    SEQUENCE_FEATURES,
    STATIC_FEATURES,
    VITALS,
    WINDOW_HOURS,
    hourly_grid,
    onset_hour,
    onset_rule_as_dict,
)

DEFAULT_URL = "postgresql://atlas:atlas@localhost:5433/atlas"
DATA_DIR = Path(__file__).resolve().parents[1] / "training" / "data"
VITALS_OUT = DATA_DIR / "vitals_hourly.csv"
STATIC_OUT = DATA_DIR / "encounters_static.csv"
COLUMNS_OUT = DATA_DIR / "deterioration.columns.json"

ENCOUNTERS_SQL = """
SELECT e.id AS encounter_id, e.patient_id, p.age, p.sex, e.admission_type, e.admit_date, e.discharge_date,
       EXTRACT(EPOCH FROM (e.discharge_date - e.admit_date)) / 3600.0 AS hours_total
FROM encounters e
JOIN patients p ON p.id = e.patient_id
WHERE e.facility_id = %(facility_id)s
  AND e.admission_type = 'inpatient'
  AND e.discharge_date IS NOT NULL
ORDER BY e.id
"""

VITALS_SQL = """
SELECT v.encounter_id, v.recorded_at, v.measurement, v.value
FROM vitals v
JOIN encounters e ON e.id = v.encounter_id
WHERE e.facility_id = %(facility_id)s
  AND e.admission_type = 'inpatient'
  AND e.discharge_date IS NOT NULL
ORDER BY v.encounter_id, v.recorded_at
"""


def grid_rows(encounter: dict, grid: np.ndarray) -> pd.DataFrame:
    """The hourly grid of one encounter as CSV rows, rounded to one decimal to keep the file small."""
    frame = pd.DataFrame(np.round(grid, 1), columns=list(VITALS))
    frame.insert(0, "hour_index", np.arange(grid.shape[0]))
    frame.insert(0, "encounter_id", encounter["encounter_id"])
    return frame


def main() -> None:
    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        facility = conn.execute("SELECT id FROM facilities WHERE code = 'DEMO'").fetchone()
        if facility is None:
            sys.exit("no facility with code DEMO; is the database seeded?")
        params = {"facility_id": facility["id"]}
        encounters = conn.execute(ENCOUNTERS_SQL, params).fetchall()
        vitals = conn.execute(VITALS_SQL, params).fetchall()

    # group the vitals rows by encounter once, then build each grid
    by_encounter: dict[int, list] = {}
    for row in vitals:
        by_encounter.setdefault(row["encounter_id"], []).append((row["recorded_at"], row["measurement"], row["value"]))

    hourly_frames = []
    static_rows = []
    for encounter in encounters:
        grid = hourly_grid(by_encounter.get(encounter["encounter_id"], []), encounter["admit_date"], encounter["hours_total"])
        onset = onset_hour(grid)
        hourly_frames.append(grid_rows(encounter, grid))
        static_rows.append({
            "encounter_id": encounter["encounter_id"],
            "patient_id": encounter["patient_id"],
            "age": encounter["age"],
            "sex": encounter["sex"],
            "admission_type": encounter["admission_type"],
            "admit_date": encounter["admit_date"].isoformat(),
            "discharge_date": encounter["discharge_date"].isoformat(),
            "hours_total": round(float(encounter["hours_total"]), 2),
            "onset_hour": "" if onset is None else onset,
        })

    hourly = pd.concat(hourly_frames, ignore_index=True)
    static = pd.DataFrame(static_rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(VITALS_OUT, index=False)
    static.to_csv(STATIC_OUT, index=False)
    COLUMNS_OUT.write_text(json.dumps({
        "vitals": list(VITALS),
        "sequence_features": list(SEQUENCE_FEATURES),
        "static_features": list(STATIC_FEATURES),
        "window_hours": WINDOW_HOURS,
        "horizon_hours": HORIZON_HOURS,
        "first_as_of_hour": FIRST_AS_OF_HOUR,
        "onset_rule": onset_rule_as_dict(),
        "hourly_columns": list(hourly.columns),
        "static_columns": list(static.columns),
    }, indent=2) + "\n", encoding="utf-8")

    onset_count = int((static["onset_hour"] != "").sum())
    measured_hours = int(hourly[list(VITALS)].notna().any(axis=1).sum())
    print(f"encounters: {len(static)} discharged inpatient stays, {static['patient_id'].nunique()} patients")
    print(f"onset encounters: {onset_count} ({onset_count / len(static):.1%}) crossed the deterioration line after hour 12")
    print(f"encounter-hours: {len(hourly)} rows, {measured_hours} with at least one measurement ({measured_hours / len(hourly):.1%})")
    print(f"vitals rows read: {len(vitals)}")
    print(f"wrote {VITALS_OUT.as_posix()} ({VITALS_OUT.stat().st_size / 1024 / 1024:.1f} MB), "
          f"{STATIC_OUT.as_posix()} ({STATIC_OUT.stat().st_size / 1024:.0f} KB) and {COLUMNS_OUT.name}")


if __name__ == "__main__":
    main()
