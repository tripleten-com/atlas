"""The readmission model: the feature frame it reads, how it is loaded, and how a probability
becomes a band.

One definition of the features lives here and is used twice:

  * `training/export_training_set.py` imports `encounter_features()` to build the training CSV
    from the Test Facility.
  * `routers/risk.py` calls `build_features_for_patient()` to build the same columns for one
    patient at request time.

If the two ever drifted apart, the model would quietly read columns it was never trained on, so
both sides share the SQL below.

The question (clinical question A in the course): will a patient discharged from an inpatient or
observation stay come back within 30 days? "Come back" means another inpatient, observation or
emergency encounter (READMISSION_TYPES below): an emergency visit with no admission counts as a
positive, so the probability the API returns is "readmission or emergency visit within 30 days",
and the UI says so. The history feature `prior_admissions_365d` deliberately counts only stays
(inpatient and observation), because that is what "prior admissions" means to a clinician.
Every feature is something the record holds at the moment of discharge, so nothing from after
the stay leaks in.

Two things to know before trusting a score:

  * The Test Facility has two years of history, so `prior_admissions_365d` is often above zero
    there. The live facilities start with a fourteen-day backfill, so for them the feature is
    almost always zero and the model reads every patient as a first-time admission. Scores on
    live patients show the plumbing, not the accuracy; `training/README.md` has the numbers.
  * The export cannot see history from before the data starts, so rows discharged in the first
    year of the window have a truncated history count (left-censoring). It is documented, not
    corrected, in the reference.

Bands, used by the UI and the Usage page:

    low     probability below 0.15
    medium  0.15 up to 0.35  (the UI adds a low-confidence note here)
    high    0.35 and above
"""
from __future__ import annotations

import hashlib
import logging
import os

import joblib
import pandas as pd
from psycopg import Connection

from .auth import CurrentUser
from .config import settings
from .db import fetch_all, fetch_one

log = logging.getLogger("atlas.ml")

QUESTION = "readmission_30d"

# Encounters the model is about. Emergency and outpatient visits are not stays.
ELIGIBLE_TYPES = ("inpatient", "observation")

# Encounters that count as "came back" for the label: a stay, or an emergency visit.
READMISSION_TYPES = ("inpatient", "observation", "emergency")

# How long after discharge the label stays open.
FOLLOW_UP_DAYS = 30

# The six vital signs the simulator and the feed record.
VITALS = ("heart_rate", "systolic_bp", "diastolic_bp", "respiratory_rate", "temperature", "spo2")

CATEGORICAL_COLUMNS = ["sex", "admission_type", "admit_source", "discharge_disposition"]

NUMERIC_COLUMNS = ["age", "length_of_stay_hours", "prior_admissions_365d", "abnormal_lab_count", "drug_class_count"]
for _v in VITALS:
    NUMERIC_COLUMNS += [f"{_v}_count", f"{_v}_last", f"{_v}_min", f"{_v}_max"]

# Every column the model reads, in the order the training frame has them.
FEATURE_COLUMNS = CATEGORICAL_COLUMNS + NUMERIC_COLUMNS

BAND_LOW = 0.15
BAND_HIGH = 0.35


# ---------------------------------------------------------------- the feature SQL

def _vital_columns() -> str:
    """One count / last / min / max for each vital, from the final 24 hours of the stay."""
    parts = []
    for v in VITALS:
        parts.append(f"count(*) FILTER (WHERE v.measurement = '{v}') AS {v}_count")
        parts.append(f"(array_agg(v.value ORDER BY v.recorded_at DESC) FILTER (WHERE v.measurement = '{v}'))[1] AS {v}_last")
        parts.append(f"min(v.value) FILTER (WHERE v.measurement = '{v}') AS {v}_min")
        parts.append(f"max(v.value) FILTER (WHERE v.measurement = '{v}') AS {v}_max")
    return ",\n           ".join(parts)


def _feature_sql(cohort_where: str, include_label: bool) -> str:
    """The whole query. `cohort_where` narrows the encounters; everything else is fixed."""
    readmission_types = ", ".join(f"'{t}'" for t in READMISSION_TYPES)
    label_cte = f"""
    label AS (
        -- another inpatient, observation or emergency encounter within 30 days of discharge
        SELECT c.encounter_id,
               EXISTS (
                   SELECT 1 FROM encounters r
                   WHERE r.patient_id = c.patient_id
                     AND r.admission_type IN ({readmission_types})
                     AND r.admit_date > c.discharge_date
                     AND r.admit_date <= c.discharge_date + interval '{FOLLOW_UP_DAYS} days'
               )::int AS readmit_30d
        FROM cohort c
    ),"""
    label_select = "label.readmit_30d," if include_label else ""
    label_join = "JOIN label ON label.encounter_id = c.encounter_id" if include_label else ""
    vital_select = ", ".join(f"ldv.{column}" for column in NUMERIC_COLUMNS if column.startswith(VITALS))
    return f"""
    WITH cohort AS (
        -- the encounters we predict for: discharged stays, one row each
        SELECT e.id AS encounter_id, e.patient_id, e.tenant_id, e.admission_type, e.admit_source,
               e.admit_date, e.discharge_date, e.discharge_disposition,
               round((extract(epoch FROM e.discharge_date - e.admit_date) / 3600)::numeric, 1)::float AS length_of_stay_hours
        FROM encounters e
        WHERE e.discharge_date IS NOT NULL
          AND e.admission_type IN ('inpatient', 'observation')
          AND coalesce(e.discharge_disposition, '') <> 'expired'
          {cohort_where}
    ),
    demographics AS (
        SELECT c.encounter_id, p.sex,
               extract(year FROM age(c.discharge_date::date, p.date_of_birth))::int AS age
        FROM cohort c JOIN patients p ON p.id = c.patient_id
    ),
    history AS (
        -- stays that started in the year before this admission
        SELECT c.encounter_id,
               (SELECT count(*) FROM encounters h
                WHERE h.patient_id = c.patient_id
                  AND h.admission_type IN ('inpatient', 'observation')
                  AND h.admit_date < c.admit_date
                  AND h.admit_date >= c.admit_date - interval '365 days') AS prior_admissions_365d
        FROM cohort c
    ),
    last_day_vitals AS (
        -- vital signs from the final 24 hours of the stay
        SELECT c.encounter_id,
           {_vital_columns()}
        FROM cohort c
        LEFT JOIN vitals v ON v.encounter_id = c.encounter_id
             AND v.recorded_at > c.discharge_date - interval '24 hours'
             AND v.recorded_at <= c.discharge_date
        GROUP BY c.encounter_id
    ),
    lab_flags AS (
        SELECT c.encounter_id,
               count(l.id) FILTER (WHERE l.flag <> 'N') AS abnormal_lab_count
        FROM cohort c
        LEFT JOIN labs l ON l.encounter_id = c.encounter_id AND l.resulted_at <= c.discharge_date
        GROUP BY c.encounter_id
    ),
    drug_classes AS (
        SELECT c.encounter_id,
               count(DISTINCT m.drug_class) AS drug_class_count
        FROM cohort c
        LEFT JOIN medications m ON m.encounter_id = c.encounter_id AND m.ordered_at <= c.discharge_date
        GROUP BY c.encounter_id
    ),{label_cte if include_label else ""}
    features AS (
        SELECT c.patient_id, c.encounter_id, c.discharge_date, {label_select}
               d.sex, c.admission_type, c.admit_source, c.discharge_disposition,
               d.age, c.length_of_stay_hours, h.prior_admissions_365d,
               lf.abnormal_lab_count, dc.drug_class_count,
               {vital_select}
        FROM cohort c
        JOIN demographics d ON d.encounter_id = c.encounter_id
        JOIN history h ON h.encounter_id = c.encounter_id
        JOIN last_day_vitals ldv ON ldv.encounter_id = c.encounter_id
        JOIN lab_flags lf ON lf.encounter_id = c.encounter_id
        JOIN drug_classes dc ON dc.encounter_id = c.encounter_id
        {label_join}
    )
    SELECT * FROM features ORDER BY discharge_date, encounter_id
    """


def encounter_features(
    conn: Connection,
    *,
    facility_id: int | None = None,
    patient_id: int | None = None,
    tenant_id: int | None = None,
    discharged_before=None,
    include_label: bool = False,
) -> list[dict]:
    """One dict per eligible discharged encounter, with every feature column plus
    `patient_id`, `encounter_id`, `discharge_date` and (when asked) `readmit_30d`.

    The keyword arguments narrow the cohort; leave them out for the whole table.
    `discharged_before` drops encounters whose 30-day window has not closed yet.
    """
    where = ""
    params: dict = {}
    if facility_id is not None:
        where += " AND e.facility_id = %(facility_id)s"
        params["facility_id"] = facility_id
    if patient_id is not None:
        where += " AND e.patient_id = %(patient_id)s"
        params["patient_id"] = patient_id
    if tenant_id is not None:
        where += " AND e.tenant_id = %(tenant_id)s"
        params["tenant_id"] = tenant_id
    if discharged_before is not None:
        where += " AND e.discharge_date <= %(discharged_before)s"
        params["discharged_before"] = discharged_before
    return fetch_all(conn, _feature_sql(where, include_label), params)


def build_features_for_patient(conn: Connection, user: CurrentUser, patient_id: int) -> dict | None:
    """The feature row for the patient's most recent discharged stay, or None when the patient
    has never had one (the "not applicable" case). Filters on the caller's tenant."""
    rows = encounter_features(conn, patient_id=patient_id, tenant_id=user.tenant_id)
    if not rows:
        return None
    return rows[-1]  # rows come back oldest first


def frame_from_rows(rows: list[dict], feature_columns: list[str]) -> pd.DataFrame:
    """A DataFrame with exactly the model's columns, in the model's order."""
    frame = pd.DataFrame(rows)
    for column in feature_columns:
        if column not in frame.columns:
            frame[column] = None  # a column the query did not return: let the imputer handle it
    frame = frame[feature_columns]
    # A missing category must be None, not NaN. pandas turns a lone None into float NaN, and
    # the one-hot encoder then crashes on a float among strings. psycopg gives us None, a CSV
    # or a hand-built row may give NaN; make both the same.
    categorical = [c for c in CATEGORICAL_COLUMNS if c in frame.columns]
    frame[categorical] = frame[categorical].astype(object).where(frame[categorical].notna(), None)
    return frame


def stay_outcome(conn: Connection, tenant_id: int, patient_id: int, discharge_date) -> dict:
    """What the record already knows about the 30 days after `discharge_date`.

    The route scores the patient's latest finished stay, but that stay may be months old: the
    30-day window has closed, or the patient has already come back. Presenting a probability
    for an outcome the record already holds would be dishonest, so the card says which it is.

        outcome             "pending" (window open, nothing yet), "readmitted", "not_readmitted"
        readmitted_at       when the first return encounter in the window began, else None
        currently_admitted  True when a stay that began after this discharge is still open
        window_closes_at    discharge_date + 30 days
    """
    row = fetch_one(
        conn,
        """SELECT
               (SELECT min(r.admit_date) FROM encounters r
                WHERE r.patient_id = %(p)s AND r.tenant_id = %(t)s
                  AND r.admission_type = ANY(%(types)s)
                  AND r.admit_date > %(d)s
                  AND r.admit_date <= %(d)s + make_interval(days => %(days)s)) AS readmitted_at,
               EXISTS (SELECT 1 FROM encounters o
                       WHERE o.patient_id = %(p)s AND o.tenant_id = %(t)s
                         AND o.admission_type = ANY(%(types)s)
                         AND o.admit_date > %(d)s AND o.discharge_date IS NULL) AS currently_admitted,
               %(d)s + make_interval(days => %(days)s) AS window_closes_at,
               now() AS now""",
        {"p": patient_id, "t": tenant_id, "d": discharge_date, "types": list(READMISSION_TYPES), "days": FOLLOW_UP_DAYS},
    )
    if row["readmitted_at"] is not None:
        outcome = "readmitted"
    elif row["window_closes_at"] < row["now"]:
        outcome = "not_readmitted"
    else:
        outcome = "pending"
    return {
        "outcome": outcome,
        "readmitted_at": row["readmitted_at"],
        "currently_admitted": bool(row["currently_admitted"]),
        "window_closes_at": row["window_closes_at"],
    }


# ---------------------------------------------------------------- the model

_cache: dict[str, dict] = {}


def _model_file() -> str:
    return settings.model_path


def _expected_sha256() -> str:
    """The hash the artifact must have, or "" when nothing is pinned (MODEL_SHA256 unset)."""
    return settings.model_sha256


def load_model() -> dict | None:
    """The saved artifact: a dict with `pipeline`, `feature_columns`, `categorical_columns`,
    `model_version`, `question` and the training provenance. Loaded once, then kept in memory.

    `main.py` calls this at startup so the load happens before the first request (and inside
    the Kubernetes startup probe's window); the route calls it again and gets the cached dict.
    Returns None when the file is missing, or when MODEL_SHA256 is set and does not match, so
    the route can answer 503 instead of crashing. A joblib file is a pickle: it runs code when
    opened, which is why the hash is checked before the load, not after."""
    path = _model_file()
    if path in _cache:
        return _cache[path]
    if not os.path.exists(path):
        log.warning("no model file at %s; the risk route will answer 503", path)
        return None
    expected = _expected_sha256()
    if expected:
        with open(path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest()
        if actual != expected:
            log.warning("model file at %s does not match MODEL_SHA256; refusing to load it", path)
            return None
    model = joblib.load(path)
    _cache[path] = model
    log.info("model %s loaded from %s", model.get("model_version", "?"), path)
    return model


def predict_probability(model: dict, row: dict) -> float:
    frame = frame_from_rows([row], model["feature_columns"])
    return float(model["pipeline"].predict_proba(frame)[0][1])


def band(probability: float) -> str:
    """low below 0.15, medium below 0.35, high from 0.35 up."""
    if probability < BAND_LOW:
        return "low"
    if probability < BAND_HIGH:
        return "medium"
    return "high"
