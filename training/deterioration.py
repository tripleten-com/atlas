"""Shared definitions for the early-warning (deterioration) model.

One copy of every rule the model depends on lives here and is used three times:

  * `training/export_vitals_hourly.py` uses the hourly grid and the onset rule to label the
    Test Facility encounters;
  * `training/train_deterioration.py` uses the window builder, the standardiser and `GRUNet`;
  * `backend/app/ml_deterioration.py` repeats the grid, the onset rule and the window builder
    (the API image does not ship this folder) and reads the rule, the feature order and the
    standardisation from deterioration.json; `backend/tests/test_early_warning.py` checks that
    the json this side writes matches the constants that side keeps, so the row the API scores
    is built exactly like the rows the model saw.

The question: for an inpatient who is admitted right now, how likely is it that their vitals
cross the deterioration threshold within the next 12 hours?

Everything in this file except `GRUNet` works with the standard library plus numpy. torch is
imported inside `build_gru_net()` only, so the API (which never installs torch) can import the
onset rule and the window builder without it.

Hours are counted from `admit_date`: `hour_index` 0 is the first hour of the stay, and a
measurement at 12h30m after admission sits in hour 12. "After hour 12" therefore means
`hour_index >= 12`: twelve full hours have passed.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

# The six vital signs the simulator and the feed record, in the order every array uses.
# Must match VITALS in backend/app/ml.py.
VITALS = ("heart_rate", "systolic_bp", "diastolic_bp", "respiratory_rate", "temperature", "spo2")

# The 24 x 12 sequence: six standardised values, then six mask channels (1 = measured that hour).
MASK_FEATURES = tuple(f"{v}_mask" for v in VITALS)
SEQUENCE_FEATURES = VITALS + MASK_FEATURES

# The four static features, in input order. sex and admission_type are small integer codes.
STATIC_FEATURES = ("age", "sex", "admission_type", "hours_since_admission")
SEX_CODES = {"female": 0, "male": 1}
ADMISSION_TYPE_CODES = {"inpatient": 0, "observation": 1, "emergency": 2, "outpatient": 3}

WINDOW_HOURS = 24      # the model sees the last 24 hours
HORIZON_HOURS = 12     # and predicts the next 12
FIRST_AS_OF_HOUR = 12  # no forecast before twelve hours of the stay have passed

# The reference onset rule: the first vitals set after hour 12 of the stay with at least two of
# these crossings. Students may change the thresholds in PROPOSAL.md; the serving side reads the
# rule it was trained with from deterioration.json, so change it here and retrain.
ONSET_THRESHOLDS = {
    "heart_rate": (">", 120.0),
    "respiratory_rate": (">", 24.0),
    "spo2": ("<", 90.0),
    "systolic_bp": ("<", 90.0),
    "temperature": (">", 38.5),
}
ONSET_MIN_CROSSINGS = 2
ONSET_EARLIEST_HOUR = 12


def onset_rule_as_dict() -> dict:
    """The rule in a form that fits in deterioration.json and training_run.json."""
    return {
        "earliest_hour": ONSET_EARLIEST_HOUR,
        "min_crossings": ONSET_MIN_CROSSINGS,
        "thresholds": {name: {"op": op, "value": value} for name, (op, value) in ONSET_THRESHOLDS.items()},
    }


def hour_index(recorded_at: datetime, admit_date: datetime) -> int:
    """Whole hours since admission for one measurement (floor, so 12h30 -> 12)."""
    return math.floor((recorded_at - admit_date).total_seconds() / 3600.0)


def hourly_grid(rows, admit_date: datetime, hours_total: float) -> np.ndarray:
    """One encounter's vitals on an hourly grid: shape [hours, 6], NaN where nothing was measured.

    `rows` holds (recorded_at, measurement, value) for one encounter, in any order. Several
    measurements of one vital in the same hour are averaged. Measurements outside the stay are
    ignored. `hours_total` is the length of the stay (so far) in hours."""
    hours = max(int(math.ceil(hours_total)), 1)
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


def count_crossings(values: np.ndarray) -> int:
    """How many of the five onset criteria one hour's vitals meet. NaN never counts."""
    crossings = 0
    for name, (op, threshold) in ONSET_THRESHOLDS.items():
        v = values[VITALS.index(name)]
        if np.isnan(v):
            continue
        if (op == ">" and v > threshold) or (op == "<" and v < threshold):
            crossings += 1
    return crossings


def onset_hour(grid: np.ndarray) -> int | None:
    """The first hour at or after ONSET_EARLIEST_HOUR whose vitals meet at least two criteria,
    or None when the stay never crossed the line."""
    for h in range(ONSET_EARLIEST_HOUR, grid.shape[0]):
        if count_crossings(grid[h]) >= ONSET_MIN_CROSSINGS:
            return h
    return None


def window_bounds(as_of_hour: int) -> tuple[int, int]:
    """The grid hours a window covers: [start, as_of). Only measurements before the as-of hour
    enter the window, which is the first leakage rule."""
    return as_of_hour - WINDOW_HOURS, as_of_hour


def build_window(grid: np.ndarray, as_of_hour: int) -> tuple[np.ndarray, np.ndarray]:
    """The raw 24-hour window for one as-of hour.

    Returns (values, mask), both shaped [24, 6]. `values` is forward-filled inside the window
    and still holds NaN for hours before the first measurement (the standardiser turns those
    into 0, the training mean). `mask` is 1 in an hour with a measurement, 0 otherwise. Hours
    before admission (as_of_hour < 24) are empty."""
    start, end = window_bounds(as_of_hour)
    values = np.full((WINDOW_HOURS, len(VITALS)), np.nan, dtype=np.float64)
    first = max(start, 0)
    last = min(end, grid.shape[0])
    if last > first:
        values[first - start:last - start] = grid[first:last]
    mask = (~np.isnan(values)).astype(np.float32)
    return forward_fill(values), mask


def forward_fill(values: np.ndarray) -> np.ndarray:
    """Carry the last measurement forward within the window, hour by hour.

    Works on one window [24, 6] or on a stack of them [N, 24, 6]: time is always the
    second-to-last axis, so the same loop serves training and serving."""
    filled = values.copy()
    for h in range(1, filled.shape[-2]):
        current, previous = filled[..., h, :], filled[..., h - 1, :]
        gap = np.isnan(current)
        current[gap] = previous[gap]
    return filled


def static_features(age: float, sex: str, admission_type: str, hours_since_admission: float) -> np.ndarray:
    """The four static inputs as numbers, in STATIC_FEATURES order."""
    return np.array([
        float(age),
        float(SEX_CODES.get(sex, 0)),
        float(ADMISSION_TYPE_CODES.get(admission_type, 0)),
        float(hours_since_admission),
    ], dtype=np.float32)


def fit_standardiser(values: np.ndarray, static: np.ndarray) -> dict:
    """Means and standard deviations from the training rows only (the second leakage rule).

    `values` is [N, 24, 6] with NaN for missing hours, `static` is [N, 4]. A constant column
    (admission_type is always inpatient in the reference) gets std 1 so it standardises to 0
    instead of dividing by zero."""
    seq_mean = np.nanmean(values, axis=(0, 1))
    seq_std = np.nanstd(values, axis=(0, 1))
    static_mean = static.mean(axis=0)
    static_std = static.std(axis=0)
    seq_std = np.where(seq_std > 1e-6, seq_std, 1.0)
    static_std = np.where(static_std > 1e-6, static_std, 1.0)
    return {
        "sequence_means": [round(float(x), 6) for x in seq_mean],
        "sequence_stds": [round(float(x), 6) for x in seq_std],
        "static_means": [round(float(x), 6) for x in static_mean],
        "static_stds": [round(float(x), 6) for x in static_std],
    }


def standardise_sequence(values: np.ndarray, mask: np.ndarray, stats: dict, use_mask: bool = True) -> np.ndarray:
    """Turn raw windows into the model's [.., 24, 12] input: standardised values with NaN -> 0,
    then the six mask channels. With `use_mask` False the mask channels are zeros, so the
    tensor keeps its shape and the ablation needs no second model definition."""
    means = np.asarray(stats["sequence_means"], dtype=np.float32)
    stds = np.asarray(stats["sequence_stds"], dtype=np.float32)
    scaled = (values.astype(np.float32) - means) / stds
    scaled = np.nan_to_num(scaled, nan=0.0)
    mask_part = mask.astype(np.float32) if use_mask else np.zeros_like(mask, dtype=np.float32)
    return np.concatenate([scaled, mask_part], axis=-1)


def standardise_static(static: np.ndarray, stats: dict) -> np.ndarray:
    means = np.asarray(stats["static_means"], dtype=np.float32)
    stds = np.asarray(stats["static_stds"], dtype=np.float32)
    return ((static.astype(np.float32) - means) / stds).astype(np.float32)


def build_gru_net(hidden: int = 64, head: int = 32):
    """GRUNet: one GRU layer over the 24 x 12 sequence, its last hidden state joined with the four
    static features and passed through a small MLP head. The forward pass returns a logit, which
    is what BCEWithLogitsLoss wants; `ProbabilityHead` below adds the sigmoid for export.

    torch is imported here, not at the top, so the serving side can import this module without it."""
    import torch
    from torch import nn

    class GRUNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.gru = nn.GRU(input_size=len(SEQUENCE_FEATURES), hidden_size=hidden, num_layers=1, batch_first=True)
            self.head = nn.Sequential(
                nn.Linear(hidden + len(STATIC_FEATURES), head),
                nn.ReLU(),
                nn.Linear(head, 1),
            )

        def forward(self, sequence: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
            _, last_hidden = self.gru(sequence)           # [1, N, hidden]
            joined = torch.cat([last_hidden[0], static], dim=1)
            return self.head(joined).squeeze(1)           # [N] logits

    class ProbabilityHead(nn.Module):
        """Wraps a trained GRUNet so the exported ONNX graph outputs a probability in [0, 1]."""

        def __init__(self, net: nn.Module) -> None:
            super().__init__()
            self.net = net

        def forward(self, sequence: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
            return torch.sigmoid(self.net(sequence, static))

    return GRUNet, ProbabilityHead
