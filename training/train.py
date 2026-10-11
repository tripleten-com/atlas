"""Train the 30-day readmission model and write the evidence a reviewer can check.

    python training/train.py                              # local run, defaults below
    python3 train.py --data readmission.csv --out run     # inside the Codio lab

Inputs: the CSV written by export_training_set.py, and (when it sits next to the CSV) the
readmission.columns.json file the export writes with it.
Outputs, all in <out>:
    readmission.joblib    the model the API serves; copy it to models/readmission.joblib
    training_run.json     what was trained, on what, with what result (see the spec's field list)
    train.log             a copy of everything this script printed

The split is grouped by patient_id: every encounter of one patient lands on the same side, so the
test score is not inflated by the model recognising a patient it already saw.

This script only needs the four pinned packages in requirements-train.txt and finishes in well
under two minutes on one CPU core, which is what a t2.micro in the lab has.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

QUESTION = "readmission_30d"
LABEL = "readmit_30d"
GROUP = "patient_id"
# columns that describe the row but are not features
BOOKKEEPING = ["patient_id", "encounter_id", "discharge_date", LABEL]
# used only when no readmission.columns.json sits next to the CSV; must match backend/app/ml.py
CATEGORICAL = ["sex", "admission_type", "admit_source", "discharge_disposition"]
TEST_FRACTION = 0.2
RANDOM_STATE = 42
CALIBRATION_BINS = 10

IMDS = "http://169.254.169.254/latest"


class Tee:
    """Write to the terminal and to train.log at the same time."""

    def __init__(self, path: Path):
        self.terminal = sys.stdout
        self.log = open(path, "w", encoding="utf-8")

    def write(self, text: str) -> None:
        self.terminal.write(text)
        self.log.write(text)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect_ec2() -> tuple[str, str | None]:
    """'ec2' plus the instance id when the EC2 metadata service answers within a second,
    otherwise 'local'. The service only exists inside an EC2 instance.

    Amazon Linux 2023 instances launched from the console require IMDSv2: a plain GET gets 401.
    So ask for a session token first (a PUT), then send it with the GET. The token-less GET is
    kept as a fallback for instances that still allow IMDSv1."""
    try:
        token_request = urllib.request.Request(
            f"{IMDS}/api/token", method="PUT", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}
        )
        with urllib.request.urlopen(token_request, timeout=1) as response:
            token = response.read().decode().strip()
        id_request = urllib.request.Request(f"{IMDS}/meta-data/instance-id", headers={"X-aws-ec2-metadata-token": token})
        with urllib.request.urlopen(id_request, timeout=1) as response:
            return "ec2", response.read().decode().strip()
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"{IMDS}/meta-data/instance-id", timeout=1) as response:
            return "ec2", response.read().decode().strip()
    except Exception:
        return "local", None


def load_columns(data_path: Path) -> list[str]:
    """The categorical columns, from the sidecar the export wrote when it is there."""
    sidecar = data_path.with_name("readmission.columns.json")
    if sidecar.exists():
        return list(json.loads(sidecar.read_text(encoding="utf-8"))["categorical_columns"])
    print(f"no {sidecar.name} next to the CSV; using the built-in categorical list")
    return CATEGORICAL


def calibration_table(y_true: np.ndarray, y_prob: np.ndarray, bins: int = CALIBRATION_BINS) -> list[dict]:
    """Ten equal-width probability bins: how many test rows fell in each, what the model said on
    average, and what actually happened. A well-calibrated model has the last two columns close."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    table = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        in_bin = (y_prob >= lower) & ((y_prob < upper) if upper < 1.0 else (y_prob <= upper))
        count = int(in_bin.sum())
        table.append({
            "lower": round(float(lower), 1),
            "upper": round(float(upper), 1),
            "count": count,
            "mean_predicted": round(float(y_prob[in_bin].mean()), 4) if count else None,
            "observed": round(float(y_true[in_bin].mean()), 4) if count else None,
        })
    return table


def metrics_for(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    return {
        "auroc": round(float(roc_auc_score(y_true, y_prob)), 4),
        "average_precision": round(float(average_precision_score(y_true, y_prob)), 4),
        "brier": round(float(brier_score_loss(y_true, y_prob)), 4),
    }


def build_pipeline(categorical: list[str], numeric: list[str]) -> Pipeline:
    """One-hot the categoricals (unknown values at serving time become all zeros), fill missing
    numbers with the training median, then a gradient-boosted tree ensemble.

    With under two thousand rows, 300 unchecked boosting rounds memorise the training set
    (train AUROC 1.0, test AUROC 0.77 in the first reference run). sklearn's default
    early_stopping='auto' only switches on above 10,000 rows, so it did nothing here. Turning it
    on explicitly holds out a fifth of the training rows and stops when their loss stops
    improving; max_depth and min_samples_leaf keep each tree from describing a handful of
    patients. On the reference set this moved train AUROC from 1.00 to 0.83 and test AUROC from
    0.73 to 0.76, and made the low band honest (observed rate below 0.15)."""
    preprocess = ColumnTransformer([
        ("categorical", OneHotEncoder(handle_unknown="ignore"), categorical),
        ("numeric", SimpleImputer(strategy="median"), numeric),
    ])
    model = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        early_stopping=True,
        validation_fraction=0.2,
        n_iter_no_change=20,
        max_depth=3,
        min_samples_leaf=40,
        random_state=RANDOM_STATE,
    )
    return Pipeline([("preprocess", preprocess), ("model", model)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="training/data/readmission.csv", help="the exported CSV")
    parser.add_argument("--out", default=f"training/runs/{date.today():%Y-%m-%d}",
                        help="where readmission.joblib, training_run.json and train.log go")
    args = parser.parse_args()

    data_path = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(out_dir / "train.log")

    started = time.monotonic()
    trained_at = datetime.now(timezone.utc)
    trained_on, instance_hint = detect_ec2()
    print(f"training started {trained_at:%Y-%m-%d %H:%M:%S} UTC on {trained_on}" + (f" ({instance_hint})" if instance_hint else ""))
    print(f"python {platform.python_version()}, numpy {np.__version__}, pandas {pd.__version__}, "
          f"scikit-learn {sklearn.__version__}, joblib {joblib.__version__}")

    # ---- data
    data_sha = sha256_of(data_path)
    frame = pd.read_csv(data_path)
    categorical_columns = load_columns(data_path)
    feature_columns = [c for c in frame.columns if c not in BOOKKEEPING]
    numeric_columns = [c for c in feature_columns if c not in categorical_columns]
    X = frame[feature_columns]
    y = frame[LABEL].to_numpy()
    groups = frame[GROUP].to_numpy()
    print(f"data {data_path.as_posix()} sha256 {data_sha[:12]}...: {len(frame)} rows, {int(y.sum())} positives "
          f"({y.mean():.1%}), {len(feature_columns)} features, {frame[GROUP].nunique()} patients")

    # ---- grouped split
    splitter = GroupShuffleSplit(n_splits=1, test_size=TEST_FRACTION, random_state=RANDOM_STATE)
    train_idx, test_idx = next(splitter.split(X, y, groups))
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    print(f"split grouped by {GROUP}: train {len(train_idx)} rows ({y_train.mean():.1%} positive), "
          f"test {len(test_idx)} rows ({y_test.mean():.1%} positive)")

    # ---- fit
    pipeline = build_pipeline(categorical_columns, numeric_columns)
    fit_started = time.monotonic()
    pipeline.fit(X_train, y_train)
    rounds = int(pipeline.named_steps["model"].n_iter_)
    print(f"fit took {time.monotonic() - fit_started:.1f}s, stopped after {rounds} boosting rounds")

    # ---- evaluate: the training rows show how much was memorised, the held-out patients count
    train_metrics = metrics_for(y_train, pipeline.predict_proba(X_train)[:, 1])
    y_prob = pipeline.predict_proba(X_test)[:, 1]
    metrics = metrics_for(y_test, y_prob)
    calibration = calibration_table(y_test, y_prob)
    print(f"train metrics: AUROC {train_metrics['auroc']}, average precision {train_metrics['average_precision']}, "
          f"Brier {train_metrics['brier']} (a big gap to the test row below means overfitting)")
    print(f"test metrics: AUROC {metrics['auroc']}, average precision {metrics['average_precision']}, "
          f"Brier {metrics['brier']} (always-predict-the-base-rate Brier would be {float(np.var(y_test)):.4f})")
    print("calibration (bin, count, mean predicted, observed):")
    for row in calibration:
        predicted = "-" if row["mean_predicted"] is None else f"{row['mean_predicted']:.3f}"
        observed = "-" if row["observed"] is None else f"{row['observed']:.3f}"
        print(f"  {row['lower']:.1f}-{row['upper']:.1f}  {row['count']:4d}  {predicted}  {observed}")

    # ---- save the artifact
    model_version = f"readmission-{trained_at:%Y%m%d}-{data_sha[:8]}"
    artifact_path = out_dir / "readmission.joblib"
    joblib.dump({
        "pipeline": pipeline,
        "feature_columns": feature_columns,
        "categorical_columns": categorical_columns,
        "model_version": model_version,
        "question": QUESTION,
        # provenance the API shows next to a prediction
        "trained_at": trained_at.isoformat(),
        "trained_on": trained_on,
    }, artifact_path)
    artifact_sha = sha256_of(artifact_path)
    print(f"saved {artifact_path.as_posix()} ({artifact_path.stat().st_size / 1024:.0f} KB) sha256 {artifact_sha[:12]}...")

    # ---- the evidence file
    run = {
        "question": QUESTION,
        "model_version": model_version,
        "trained_at": trained_at.isoformat(),
        "trained_on": trained_on,
        "instance_hint": instance_hint,
        "python": platform.python_version(),
        "packages": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit-learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "data_file": data_path.name,
        "data_sha256": data_sha,
        "rows": int(len(frame)),
        "positives": int(y.sum()),
        "split": {"method": "grouped_by_patient", "group": GROUP, "test_fraction": TEST_FRACTION,
                  "random_state": RANDOM_STATE, "train_rows": int(len(train_idx)), "test_rows": int(len(test_idx))},
        "boosting_rounds": rounds,
        "train_metrics": train_metrics,
        "metrics": metrics,
        "calibration": calibration,
        "artifact_sha256": artifact_sha,
    }
    (out_dir / "training_run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {(out_dir / 'training_run.json').as_posix()} and {(out_dir / 'train.log').as_posix()}")
    print(f"model_version {model_version}; total {time.monotonic() - started:.1f}s")
    print(f"to serve it: copy {artifact_path.as_posix()} to models/readmission.joblib and restart the backend")
    if trained_on == "local":
        print("NOTE: trained_on is 'local'. The course requires the committed artifact to come from the EC2 lab; "
              "if you are on an EC2 instance and see this, the metadata service did not answer.")


if __name__ == "__main__":
    main()
