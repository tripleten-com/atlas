"""The early-warning model: how the ONNX artifact is loaded, how one patient's last 24 hours of
vitals become the model's input, and the onset check behind the "deteriorating" state.

The question: for an inpatient who is admitted right now, how likely is it that their vitals
cross the deterioration line within the next 12 hours?

The model was trained by `training/train_deterioration.py` with the definitions in
`training/deterioration.py` (hourly grid, 24-hour window, forward fill, standardisation, onset
rule). The API container does not ship the training folder, so the small amount of code the
serving side needs is repeated here, and `tests/test_early_warning.py` checks that the feature
order and the onset rule in the artifact's json (written by the training side) match the
constants below. If you change a rule, change it in both files and retrain.

Everything data-dependent is read from `deterioration.json` next to the .onnx: the onset rule
the model was trained with, the standardisation means and stds, the feature order, the alert
threshold. That way a student who retrains with a different rule only has to copy two files.

The clinical clock. The Test Facility is a frozen snapshot and the live feed may run faster
than real time (FEED_CADENCE_SCALE), so "now" on the wall is not the right clock for the
window. The as-of hour is the hour after the latest vitals measurement of the stay (never ahead
of the wall clock), and the response says which hour that is.

Bands reuse the readmission thresholds (`ml.band`) so the two cards read alike. `alert` is
"probability at or above the alert threshold in the json" (0.20 in the reference).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from psycopg import Connection

from .auth import CurrentUser
from .config import settings
from .db import fetch_all, fetch_one

log = logging.getLogger("atlas.ml_deterioration")

QUESTION = "deterioration_12h"
FEATURE = "early-warning"

# The six vitals in the order every array uses; must match training/deterioration.py and ml.VITALS.
VITALS = ("heart_rate", "systolic_bp", "diastolic_bp", "respiratory_rate", "temperature", "spo2")
MASK_FEATURES = tuple(f"{v}_mask" for v in VITALS)
SEQUENCE_FEATURES = VITALS + MASK_FEATURES                     # the 12 channels of the 24 x 12 input
STATIC_FEATURES = ("age", "sex", "admission_type", "hours_since_admission")
SEX_CODES = {"female": 0, "male": 1}
ADMISSION_TYPE_CODES = {"inpatient": 0, "observation": 1, "emergency": 2, "outpatient": 3}

WINDOW_HOURS = 24      # the model sees the last 24 hours
HORIZON_HOURS = 12     # and predicts the next 12
FIRST_AS_OF_HOUR = 12  # no forecast before twelve hours of the stay have passed

# The reference onset rule, kept in step with training/deterioration.py. At request time the rule
# from deterioration.json is used, so the state and the label the model learned agree.
ONSET_RULE = {
    "earliest_hour": 12,
    "min_crossings": 2,
    "thresholds": {
        "heart_rate": {"op": ">", "value": 120.0},
        "respiratory_rate": {"op": ">", "value": 24.0},
        "spo2": {"op": "<", "value": 90.0},
        "systolic_bp": {"op": "<", "value": 90.0},
        "temperature": {"op": ">", "value": 38.5},
    },
}

# The three states a 200 answer can carry.
SCORED = "scored"
INSUFFICIENT_DATA = "insufficient_data"
DETERIORATING = "deteriorating"


# ---------------------------------------------------------------- the model

_cache: dict[str, dict] = {}


def _model_file() -> str:
    return settings.model_deterioration_path


def _expected_sha256() -> str:
    """The hash the .onnx must have, or "" when nothing is pinned (MODEL_DETERIORATION_SHA256 unset)."""
    return settings.model_deterioration_sha256


def _card_file(onnx_path: str) -> str:
    """deterioration.json sits next to deterioration.onnx with the same stem."""
    return os.path.splitext(onnx_path)[0] + ".json"


def load_model() -> dict | None:
    """The served model: `session` (an onnxruntime InferenceSession on CPU), `card` (the json the
    training script wrote: onset rule, standardisation, feature order, alert threshold) and the
    provenance fields the response repeats. Loaded once, then kept in memory.

    `main.py` calls this at startup so the load happens inside the Kubernetes startup probe's
    window; the route calls it again and gets the cached dict. Returns None when the .onnx or
    its json is missing, or when MODEL_DETERIORATION_SHA256 is set and does not match, so the
    route can answer 503 instead of crashing."""
    path = _model_file()
    if path in _cache:
        return _cache[path]
    started = time.monotonic()
    card_path = _card_file(path)
    if not os.path.exists(path) or not os.path.exists(card_path):
        log.warning("no early-warning model at %s (with %s); the route will answer 503", path, os.path.basename(card_path))
        return None
    expected = _expected_sha256()
    if expected:
        with open(path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest()
        if actual != expected:
            log.warning("model file at %s does not match MODEL_DETERIORATION_SHA256; refusing to load it", path)
            return None
    with open(card_path, encoding="utf-8") as handle:
        card = json.load(handle)

    import onnxruntime as ort  # imported here so a missing wheel shows up as a 503, not a crash at import

    session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    model = {
        "session": session,
        "card": card,
        "model_version": card.get("model_version", "deterioration-unknown"),
        "trained_on": card.get("trained_on", "unknown"),
        "trained_at": card.get("trained_at"),
        "alert_threshold": float(card.get("alert_threshold", 0.2)),
        "onset_rule": card.get("onset_rule", ONSET_RULE),
        "use_mask": bool(card.get("mask_channel", True)),
    }
    _cache[path] = model
    log.info(
        "early-warning model %s loaded from %s in %d ms (onnxruntime %s, CPU)",
        model["model_version"], path, int((time.monotonic() - started) * 1000), ort.__version__,
    )
    return model


def predict_probability(model: dict, sequence: np.ndarray, static: np.ndarray) -> float:
    """One window in, one probability out. The graph applies the sigmoid itself (output
    `probability`), so nothing is applied here."""
    outputs = model["session"].run(
        ["probability"],
        {"sequence": sequence[None, :, :].astype(np.float32), "static": static[None, :].astype(np.float32)},
    )
    return float(outputs[0][0])


# ---------------------------------------------------------------- the patient's data

def open_inpatient_encounter(conn: Connection, user: CurrentUser, patient_id: int) -> dict | None:
    """The patient's open inpatient stay (discharge_date NULL), or None: the "not applicable"
    case. Filters on the caller's tenant. The newest is taken if the record ever held two."""
    return fetch_one(
        conn,
        """SELECT e.id AS encounter_id, e.admit_date, e.admission_type, p.age, p.sex
           FROM encounters e JOIN patients p ON p.id = e.patient_id
           WHERE e.patient_id = %(patient_id)s AND e.tenant_id = %(tenant_id)s
             AND e.admission_type = 'inpatient' AND e.discharge_date IS NULL
           ORDER BY e.admit_date DESC, e.id DESC LIMIT 1""",
        {"patient_id": patient_id, "tenant_id": user.tenant_id},
    )


def vitals_rows(conn: Connection, user: CurrentUser, encounter_id: int) -> list[tuple]:
    """Every vitals measurement of one encounter as (recorded_at, measurement, value)."""
    rows = fetch_all(
        conn,
        """SELECT recorded_at, measurement, value FROM vitals
           WHERE encounter_id = %(encounter_id)s AND tenant_id = %(tenant_id)s
           ORDER BY recorded_at""",
        {"encounter_id": encounter_id, "tenant_id": user.tenant_id},
    )
    return [(r["recorded_at"], r["measurement"], r["value"]) for r in rows]


# ---------------------------------------------------------------- the hourly grid (as in training)

def hour_index(recorded_at: datetime, admit_date: datetime) -> int:
    """Whole hours since admission for one measurement (floor, so 12h30 -> 12)."""
    return math.floor((recorded_at - admit_date).total_seconds() / 3600.0)


def hourly_grid(rows: list[tuple], admit_date: datetime, hours: int) -> np.ndarray:
    """One stay's vitals on an hourly grid [hours, 6], NaN where nothing was measured. Several
    measurements of one vital in the same hour are averaged. Zero hours (a stay younger than one
    hour) gives an empty grid, so hours_of_vitals can never exceed hours_admitted."""
    hours = max(int(hours), 0)
    total = np.zeros((hours, len(VITALS)), dtype=np.float64)
    count = np.zeros((hours, len(VITALS)), dtype=np.float64)
    column = {name: i for i, name in enumerate(VITALS)}
    for recorded_at, measurement, value in rows:
        if measurement not in column or value is None:
            continue
        h = hour_index(recorded_at, admit_date)
        if h < 0 or h >= hours:
            continue
        total[h, column[measurement]] += float(value)
        count[h, column[measurement]] += 1.0
    with np.errstate(invalid="ignore"):
        grid = total / count
    grid[count == 0] = np.nan
    return grid


def count_crossings(values: np.ndarray, rule: dict) -> int:
    """How many of the onset criteria one hour's vitals meet. NaN never counts."""
    crossings = 0
    for name, threshold in rule["thresholds"].items():
        if name not in VITALS:
            continue
        v = values[VITALS.index(name)]
        if np.isnan(v):
            continue
        if (threshold["op"] == ">" and v > threshold["value"]) or (threshold["op"] == "<" and v < threshold["value"]):
            crossings += 1
    return crossings


def onset_hour(grid: np.ndarray, rule: dict) -> int | None:
    """The first hour at or after the rule's earliest hour whose vitals meet enough criteria,
    or None when the stay has not crossed the line."""
    for h in range(int(rule["earliest_hour"]), grid.shape[0]):
        if count_crossings(grid[h], rule) >= int(rule["min_crossings"]):
            return h
    return None


def forward_fill(values: np.ndarray) -> np.ndarray:
    """Carry the last measurement forward within the window, hour by hour."""
    filled = values.copy()
    for h in range(1, filled.shape[0]):
        gap = np.isnan(filled[h])
        filled[h][gap] = filled[h - 1][gap]
    return filled


def build_window(grid: np.ndarray, as_of_hour: int) -> tuple[np.ndarray, np.ndarray]:
    """The raw 24-hour window [as_of-24, as_of) as (values, mask), both [24, 6]. Only hours
    before the as-of hour enter it; hours before admission are empty."""
    start = as_of_hour - WINDOW_HOURS
    values = np.full((WINDOW_HOURS, len(VITALS)), np.nan, dtype=np.float64)
    first, last = max(start, 0), min(as_of_hour, grid.shape[0])
    if last > first:
        values[first - start:last - start] = grid[first:last]
    mask = (~np.isnan(values)).astype(np.float32)
    return forward_fill(values), mask


def standardise(values: np.ndarray, mask: np.ndarray, static: np.ndarray, card: dict, use_mask: bool) -> tuple[np.ndarray, np.ndarray]:
    """Raw window -> the model's [24, 12] sequence and [4] static inputs, using the training-set
    means and stds from the json. NaN (no measurement yet) becomes 0, the training mean."""
    stats = card["standardisation"]
    seq = (values.astype(np.float32) - np.asarray(stats["sequence_means"], dtype=np.float32)) / np.asarray(stats["sequence_stds"], dtype=np.float32)
    seq = np.nan_to_num(seq, nan=0.0)
    mask_part = mask.astype(np.float32) if use_mask else np.zeros_like(mask, dtype=np.float32)
    sequence = np.concatenate([seq, mask_part], axis=-1)
    stat = (static.astype(np.float32) - np.asarray(stats["static_means"], dtype=np.float32)) / np.asarray(stats["static_stds"], dtype=np.float32)
    return sequence, stat.astype(np.float32)


def static_features(age: float, sex: str, admission_type: str, hours_since_admission: float) -> np.ndarray:
    """The four static inputs as numbers, in STATIC_FEATURES order (same codes as training)."""
    return np.array([
        float(age), float(SEX_CODES.get(sex, 0)), float(ADMISSION_TYPE_CODES.get(admission_type, 0)), float(hours_since_admission),
    ], dtype=np.float32)


# ---------------------------------------------------------------- one patient, one answer

def assess(encounter: dict, rows: list[tuple], model: dict, now: datetime | None = None) -> dict:
    """Decide the state for one open stay and, when it is scorable, compute the probability.

    Returns a dict with `state` and every field the response needs: as_of, as_of_hour,
    hours_admitted, hours_of_vitals, window_start, window_end, probability, alert, inputs (the
    window summary the document store keeps). The order of the rules:

      1. fewer than 12 hours of the stay have passed on the clinical clock, or no vitals at all
         -> insufficient_data (the model never saw an as-of hour before 12);
      2. the onset rule already fired at some hour of the stay -> deteriorating, as_of is the
         hour it fired (the event has happened; a forecast would be meaningless);
      3. otherwise -> scored.
    """
    now = now or datetime.now(timezone.utc)
    admit = encounter["admit_date"]
    wall_hour = max(hour_index(now, admit), 0)
    # the clinical clock: the hour after the latest measurement, never ahead of the wall clock
    as_of_hour = min(wall_hour, max(hour_index(t, admit) for t, _, _ in rows) + 1) if rows else wall_hour
    grid = hourly_grid(rows, admit, as_of_hour)
    hours_of_vitals = int((~np.isnan(grid)).any(axis=1).sum()) if rows else 0

    answer = {
        "encounter_id": encounter["encounter_id"],
        "as_of_hour": as_of_hour,
        "as_of": admit + timedelta(hours=as_of_hour),
        "hours_admitted": as_of_hour,
        "hours_of_vitals": hours_of_vitals,
        "window_start": admit + timedelta(hours=as_of_hour - WINDOW_HOURS),
        "window_end": admit + timedelta(hours=as_of_hour),
        "probability": None,
        "alert": None,
        "inputs": {"as_of_hour": as_of_hour, "hours_of_vitals": hours_of_vitals},
    }
    if not rows or as_of_hour < FIRST_AS_OF_HOUR:
        answer["state"] = INSUFFICIENT_DATA
        return answer

    onset = onset_hour(grid, model["onset_rule"])
    if onset is not None:
        answer.update(
            state=DETERIORATING,
            as_of=admit + timedelta(hours=onset),
            window_start=admit + timedelta(hours=onset - WINDOW_HOURS),
            window_end=admit + timedelta(hours=onset),
        )
        answer["inputs"].update(onset_hour=onset, last_values=_last_values(grid[:onset + 1]))
        return answer

    values, mask = build_window(grid, as_of_hour)
    static = static_features(encounter["age"], encounter["sex"], encounter["admission_type"], as_of_hour)
    sequence, static_in = standardise(values, mask, static, model["card"], model["use_mask"])
    # Round once, here, so the response, the stored document and the alert decision all see the
    # same number: a raw 0.1996 would otherwise be shown as 0.2 next to "no alert" at a 0.20 line.
    probability = round(predict_probability(model, sequence, static_in), 3)
    answer.update(
        state=SCORED,
        probability=probability,
        alert=probability >= model["alert_threshold"],
    )
    answer["inputs"].update(measured_hours_in_window=int(mask.any(axis=1).sum()), last_values=_last_values(values))
    return answer


def _last_values(window: np.ndarray) -> dict:
    """The most recent value of each vital in a (forward-filled or raw) window, for the store."""
    out = {}
    for i, name in enumerate(VITALS):
        column = window[:, i]
        known = column[~np.isnan(column)]
        out[name] = None if len(known) == 0 else round(float(known[-1]), 1)
    return out
