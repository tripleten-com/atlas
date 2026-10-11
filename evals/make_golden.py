"""Build evals/golden.jsonl: ten Test Facility patients and the facts the summary may use.

    python evals/make_golden.py                 # writes evals/golden.jsonl
    python evals/make_golden.py --count 10 --out evals/golden.jsonl

Reads Postgres directly (DATABASE_URL, default the Compose port on localhost) and reuses the
route's own `fetch_grounding()`, so a case holds exactly the rows the API will send to the model:
the latest encounter, its last vital per measurement, up to twenty labs and the stay's orders.
The Test Facility never changes, so running this again gives the same file.

Each line:
    {"patient_id", "encounter_id", "medications": [...names],
     "lab_values": [...numbers], "grounding_numbers": [...every number in the grounding text]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# The route's grounding code lives in the backend package; make it importable from here, which
# is why this one import comes after the sys.path line.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.summary import fetch_grounding  # noqa: E402

DEFAULT_URL = "postgresql://atlas:atlas@localhost:5433/atlas"
# No minus sign: clinical values here are never negative, and "2026-09-11" must read as 2026, 9, 11.
NUMBER = re.compile(r"\d+(?:\.\d+)?")

# Patients whose latest visit is an inpatient stay with labs and orders, so every evaluator has
# something to check. Spread across the id range so the set is not ten neighbours.
PICK_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (e.patient_id) e.patient_id, e.id AS encounter_id, e.tenant_id
    FROM encounters e JOIN facilities f ON f.id = e.facility_id
    WHERE f.code = 'DEMO'
    ORDER BY e.patient_id, e.admit_date DESC, e.id DESC
)
SELECT l.patient_id, l.encounter_id, l.tenant_id
FROM latest l JOIN encounters e ON e.id = l.encounter_id
WHERE e.admission_type = 'inpatient' AND e.discharge_date IS NOT NULL
  AND EXISTS (SELECT 1 FROM labs x WHERE x.encounter_id = e.id)
  AND EXISTS (SELECT 1 FROM medications m WHERE m.encounter_id = e.id)
ORDER BY l.patient_id
"""


def numbers_in(text: str) -> list[float]:
    """Every number in a piece of text, as floats, in order of appearance."""
    return [float(n) for n in NUMBER.findall(text)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--out", default=str(ROOT / "evals" / "golden.jsonl"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_URL))
    args = parser.parse_args()

    with psycopg.connect(args.database_url, row_factory=dict_row) as conn:
        candidates = conn.execute(PICK_SQL).fetchall()
        if len(candidates) < args.count:
            raise SystemExit(f"only {len(candidates)} eligible patients, wanted {args.count}")
        step = len(candidates) // args.count  # every k-th candidate, spread over the whole list
        chosen = [candidates[i * step] for i in range(args.count)]

        cases = []
        for row in chosen:
            grounding = fetch_grounding(conn, row["tenant_id"], row["patient_id"])
            assert grounding is not None and grounding.encounter["id"] == row["encounter_id"]
            text = "\n".join(grounding.lines())
            cases.append({
                "patient_id": row["patient_id"],
                "encounter_id": row["encounter_id"],
                "medications": sorted({m["drug_name"] for m in grounding.medications}),
                "lab_values": sorted({float(lab["value"]) for lab in grounding.labs}),
                "grounding_numbers": sorted(set(numbers_in(text))),
                "rows": {"vitals": len(grounding.vitals), "labs": len(grounding.labs),
                         "medications": len(grounding.medications)},
            })

    out = Path(args.out)
    out.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    print(f"wrote {len(cases)} cases to {out}")
    for c in cases:
        print(f"  patient {c['patient_id']:>5}  encounter {c['encounter_id']:>6}  "
              f"vitals {c['rows']['vitals']}  labs {c['rows']['labs']}  meds {c['rows']['medications']}")


if __name__ == "__main__":
    main()
