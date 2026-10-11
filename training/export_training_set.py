"""Export the readmission training set from the Test Facility.

    python training/export_training_set.py
    DATABASE_URL=postgresql://atlas:atlas@localhost:5433/atlas python training/export_training_set.py

Reads Postgres, keeps one row per discharged inpatient or observation encounter at the Test
Facility (code DEMO), and writes training/data/readmission.csv. The columns are built by
`backend/app/ml.py`, the same code the API uses at request time, so training and serving can
never disagree about what a feature means. Next to the CSV it writes readmission.columns.json,
which names the categorical and feature columns so train.py (which runs in the lab without
ml.py) encodes exactly these.

Two kinds of censoring, both worth a sentence in your report:

  * Right-censoring. The label `readmit_30d` needs 30 days of follow-up, so encounters
    discharged in the last 30 days of the data are left out: for them the answer is not known.
  * Left-censoring. `prior_admissions_365d` counts stays in the year before admission, but the
    data starts two years before the seed day, so rows discharged in the first year see a
    truncated history. The reference keeps those rows and says so; excluding them, or adding a
    "days of history available" feature, are both reasonable improvements.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.rows import dict_row

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))  # so `app.ml` imports from here without installing it

from app.ml import (  # noqa: E402
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    FOLLOW_UP_DAYS,
    encounter_features,
)

DEFAULT_URL = "postgresql://atlas:atlas@localhost:5433/atlas"
OUT = REPO / "training" / "data" / "readmission.csv"
COLUMNS_OUT = OUT.with_name("readmission.columns.json")


def main() -> None:
    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        facility = conn.execute("SELECT id FROM facilities WHERE code = 'DEMO'").fetchone()
        if facility is None:
            sys.exit("no facility with code DEMO; is the database seeded?")
        # the newest discharge is the end of the data; anything within 30 days of it is unlabelled
        data_end = conn.execute(
            "SELECT max(discharge_date) AS data_end FROM encounters WHERE facility_id = %s", (facility["id"],)
        ).fetchone()["data_end"]
        cutoff = data_end - pd.Timedelta(days=FOLLOW_UP_DAYS)
        rows = encounter_features(conn, facility_id=facility["id"], discharged_before=cutoff, include_label=True)

    frame = pd.DataFrame(rows)
    # grouping and bookkeeping columns first, then the features in the model's order, then the label
    frame = frame[["patient_id", "encounter_id", "discharge_date"] + FEATURE_COLUMNS + ["readmit_30d"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False)
    COLUMNS_OUT.write_text(
        json.dumps({"categorical_columns": CATEGORICAL_COLUMNS, "feature_columns": FEATURE_COLUMNS}, indent=2) + "\n",
        encoding="utf-8",
    )

    positives = int(frame["readmit_30d"].sum())
    print(f"data ends {data_end:%Y-%m-%d}; encounters discharged after {cutoff:%Y-%m-%d} were excluded")
    print(f"rows: {len(frame)}")
    print(f"positives: {positives}")
    print(f"positive rate: {positives / len(frame):.3f}")
    print(f"wrote {OUT.as_posix()} ({OUT.stat().st_size / 1024:.0f} KB) and {COLUMNS_OUT.name}")


if __name__ == "__main__":
    main()
