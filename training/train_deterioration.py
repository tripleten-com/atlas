"""Train the early-warning (deterioration) model and write the evidence a reviewer can check.

    python training/train_deterioration.py --device cuda --time-one-epoch        # the reference run
    python training/train_deterioration.py --stride 4 --epochs 3                 # a quick rehearsal
    python3 train_deterioration.py --data-dir . --out run --device cuda --time-one-epoch   # in the lab

Inputs (from export_vitals_hourly.py, all in --data-dir):
    vitals_hourly.csv, encounters_static.csv, deterioration.columns.json
Outputs, all in --out:
    deterioration.onnx    the model the API serves (opset 17, dynamic batch); copy it to models/
    deterioration.json    everything the serving side needs next to the graph: onset rule,
                          standardisation statistics, feature order, alert threshold; copy it too
    training_run.json     what was trained, on what, with what result (the spec's field list)
    train.log             a copy of everything this script printed
    onnx_check.npz        ten test windows with the torch probabilities, for check_onnx.py

The unit of prediction is one (encounter, as-of hour) window: the last 24 hours of the six
vitals on an hourly grid plus a mask channel, and four static features. The label is 1 when the
deterioration onset (the rule in deterioration.py) falls inside the next 12 hours. Windows after
the onset are dropped: the event has happened, there is nothing left to forecast.

Three leakage rules are built in and worth repeating in REPORT.md: only measurements before the
as-of hour enter a window; the split is grouped by patient_id; the standardisation statistics
come from the training rows only. The mask channel also encodes nursing cadence, which rises
after the simulated onset begins and before the threshold is crossed, so part of what the model
learns may be "someone is worried" rather than physiology. `--no-mask` measures how much.

The ONNX graph outputs a PROBABILITY (sigmoid applied inside the graph), named "probability",
shape [N]. Inputs: "sequence" [N, 24, 12] float32 and "static" [N, 4] float32, both already
standardised with the statistics in deterioration.json.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
import urllib.request
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import torch
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `deterioration` imports from any cwd

from deterioration import (  # noqa: E402
    FIRST_AS_OF_HOUR,
    HORIZON_HOURS,
    SEQUENCE_FEATURES,
    STATIC_FEATURES,
    VITALS,
    WINDOW_HOURS,
    build_gru_net,
    fit_standardiser,
    forward_fill,
    onset_rule_as_dict,
    standardise_sequence,
    standardise_static,
    static_features,
)

MODEL = "deterioration_12h"
GROUP = "patient_id"
TEST_FRACTION = 0.2
VALIDATION_FRACTION = 0.15
BATCH_SIZE = 512
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
PATIENCE = 6
ALERT_THRESHOLD = 0.20
ALERT_TABLE_THRESHOLDS = (0.20, 0.35, 0.50, 0.70, 0.80, 0.90, 0.95)
BAND_LOW, BAND_HIGH = 0.15, 0.35  # the readmission bands, reused so the two cards read alike
CALIBRATION_BINS = 10
ONNX_OPSET = 17
CHECK_WINDOWS = 10

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
    otherwise 'local'. Amazon Linux 2023 requires IMDSv2: ask for a token (a PUT) first, then
    send it with the GET. The token-less GET stays as a fallback for IMDSv1 instances."""
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


def print_nvidia_smi() -> None:
    """The GPU evidence the automated check looks for in train.log."""
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=20)
        print(result.stdout.strip() or result.stderr.strip())
    except Exception as exc:  # the binary is missing on a CPU-only machine
        print(f"nvidia-smi not available: {exc}")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


# ---------------------------------------------------------------- windows

def build_windows(hourly: pd.DataFrame, static: pd.DataFrame, stride: int) -> dict:
    """Every (encounter, as-of hour) window in the data, as flat numpy arrays.

    For each encounter the as-of hours run from FIRST_AS_OF_HOUR to the last whole hour of the
    stay in steps of `stride`, and stop at the onset hour: a window whose as-of hour is the onset
    hour is kept (its 24 hours end before the onset measurement), later ones are dropped."""
    values, masks, statics, labels, encounter_ids, as_of_hours, onsets = [], [], [], [], [], [], []
    static = static.set_index("encounter_id")
    vitals_columns = list(VITALS)
    for encounter_id, rows in hourly.groupby("encounter_id", sort=False):
        grid = rows.sort_values("hour_index")[vitals_columns].to_numpy(dtype=np.float32)
        info = static.loc[encounter_id]
        onset = None if pd.isna(info["onset_hour"]) else int(info["onset_hour"])
        last_as_of = int(np.floor(info["hours_total"]))
        if onset is not None:
            last_as_of = min(last_as_of, onset)
        as_of = np.arange(FIRST_AS_OF_HOUR, last_as_of + 1, stride)
        if len(as_of) == 0:
            continue
        # pad 24 empty hours in front so a window that starts before admission is just NaN
        padded = np.concatenate([np.full((WINDOW_HOURS, len(VITALS)), np.nan, dtype=np.float32), grid])
        index = as_of[:, None] + np.arange(WINDOW_HOURS)[None, :]   # padded index of hours [as_of-24, as_of)
        window = padded[index]                                        # [n, 24, 6]
        values.append(window)
        masks.append((~np.isnan(window)).astype(np.float32))
        statics.append(np.stack([
            static_features(info["age"], info["sex"], info["admission_type"], h) for h in as_of
        ]))
        if onset is None:
            labels.append(np.zeros(len(as_of), dtype=np.float32))
        else:
            labels.append(((onset - as_of >= 0) & (onset - as_of < HORIZON_HOURS)).astype(np.float32))
        encounter_ids.append(np.full(len(as_of), encounter_id))
        as_of_hours.append(as_of)
        onsets.append(np.full(len(as_of), -1 if onset is None else onset))
    return {
        "values": forward_fill(np.concatenate(values)),  # inside each window, never across windows
        "mask": np.concatenate(masks),
        "static": np.concatenate(statics),
        "label": np.concatenate(labels),
        "encounter_id": np.concatenate(encounter_ids),
        "as_of_hour": np.concatenate(as_of_hours),
        "onset_hour": np.concatenate(onsets),
    }


def take(windows: dict, index: np.ndarray) -> dict:
    return {key: array[index] for key, array in windows.items()}


def grouped_split(windows: dict, patient_of: dict, seed: int) -> tuple[dict, dict, dict]:
    """Test patients first (20%), then 15% of the remaining patients for validation. Every window
    of one patient lands on one side, so the model never scores a patient it trained on."""
    groups = np.array([patient_of[e] for e in windows["encounter_id"]])
    outer = GroupShuffleSplit(n_splits=1, test_size=TEST_FRACTION, random_state=seed)
    train_idx, test_idx = next(outer.split(windows["label"], groups=groups))
    inner = GroupShuffleSplit(n_splits=1, test_size=VALIDATION_FRACTION, random_state=seed)
    fit_idx, val_idx = next(inner.split(train_idx, groups=groups[train_idx]))
    return take(windows, train_idx[fit_idx]), take(windows, train_idx[val_idx]), take(windows, test_idx)


def to_tensors(split: dict, stats: dict, use_mask: bool, device: torch.device) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    sequence = standardise_sequence(split["values"], split["mask"], stats, use_mask)
    static = standardise_static(split["static"], stats)
    return (
        torch.from_numpy(sequence).to(device),
        torch.from_numpy(static).to(device),
        torch.from_numpy(split["label"]).to(device),
    )


# ---------------------------------------------------------------- training

def run_epoch(model: nn.Module, optimizer, scaler, loss_fn, sequence, static, label, device: torch.device) -> float:
    """One pass over the training windows in shuffled batches. Returns the mean loss."""
    model.train()
    order = torch.randperm(len(label), device=device)
    total = 0.0
    use_amp = device.type == "cuda"
    for start in range(0, len(order), BATCH_SIZE):
        batch = order[start:start + BATCH_SIZE]
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=use_amp):
            logits = model(sequence[batch], static[batch])
            loss = loss_fn(logits.float(), label[batch])
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        total += float(loss.detach()) * len(batch)
    return total / len(label)


@torch.no_grad()
def predict(model: nn.Module, sequence: torch.Tensor, static: torch.Tensor) -> np.ndarray:
    model.eval()
    out = []
    for start in range(0, len(sequence), 4096):
        out.append(torch.sigmoid(model(sequence[start:start + 4096], static[start:start + 4096]).float()).cpu())
    return torch.cat(out).numpy()


def make_model_and_optimizer(pos_weight: float, device: torch.device):
    GRUNet, _ = build_gru_net()
    model = GRUNet().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight, device=device))
    return model, optimizer, scaler, loss_fn


def time_one_epoch(device_name: str, train: dict, stats: dict, use_mask: bool, pos_weight: float, seed: int) -> float:
    """Seconds for one full epoch on the named device with a fresh model: the CPU-versus-GPU
    number the course asks for, measured on the same data with the same batch size."""
    device = torch.device(device_name)
    set_seed(seed)
    sequence, static, label = to_tensors(train, stats, use_mask, device)
    model, optimizer, scaler, loss_fn = make_model_and_optimizer(pos_weight, device)
    if device.type == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    run_epoch(model, optimizer, scaler, loss_fn, sequence, static, label, device)
    if device.type == "cuda":
        torch.cuda.synchronize()
    return time.perf_counter() - started


# ---------------------------------------------------------------- metrics

def calibration_table(y_true: np.ndarray, y_prob: np.ndarray, bins: int = CALIBRATION_BINS) -> list[dict]:
    """Ten equal-width probability bins: how many test windows fell in each, what the model said
    on average, and what actually happened. A well-calibrated model has the last two close."""
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


def alert_metrics(split: dict, y_prob: np.ndarray, stride: int, threshold: float) -> dict:
    """What a ward would feel: how often the card alerts, how many deteriorations it catches
    and how early.

    Patient-days are the hours the windows stand for (one window = `stride` hours) over 24. A
    deterioration is "caught" when a window of that stay alerts inside the 12-hour horizon the
    model was trained for (onset minus as-of hour under HORIZON_HOURS, that is a positive
    window); lead time is onset minus the first such alerting as-of hour, so it is at most 12.
    An alert earlier in the stay is a false alarm by the model's own definition, so it does
    not count as catching the onset; `caught_encounters_any_hour` and
    `median_lead_time_hours_any_hour` keep the looser count (any alert before the onset) for
    comparison, because a random scorer with the same alert rate "catches" most stays by it."""
    alerts = y_prob >= threshold
    patient_days = len(y_prob) * stride / 24.0
    positives = split["label"] == 1
    frame = pd.DataFrame({
        "encounter_id": split["encounter_id"],
        "as_of_hour": split["as_of_hour"],
        "onset_hour": split["onset_hour"],
        "alert": alerts,
        "alert_in_horizon": alerts & positives,
    })
    onset_stays = frame[frame["onset_hour"] >= 0]
    onset_of = onset_stays.groupby("encounter_id")["onset_hour"].first()
    caught = onset_stays[onset_stays["alert_in_horizon"]].groupby("encounter_id")["as_of_hour"].min()
    caught_any = onset_stays[onset_stays["alert"]].groupby("encounter_id")["as_of_hour"].min()
    lead_times = (onset_of.loc[caught.index] - caught).to_numpy(dtype=float)
    lead_times_any = (onset_of.loc[caught_any.index] - caught_any).to_numpy(dtype=float)
    return {
        "alert_threshold": threshold,
        "alerts": int(alerts.sum()),
        "patient_days": round(patient_days, 1),
        "alerts_per_100_patient_days": round(float(alerts.sum() / patient_days * 100), 2),
        "onset_encounters": int(onset_of.shape[0]),
        "caught_encounters": int(caught.shape[0]),
        "sensitivity_at_threshold": round(float(caught.shape[0] / onset_of.shape[0]), 4) if onset_of.shape[0] else None,
        "window_recall_at_threshold": round(float(alerts[positives].mean()), 4) if positives.any() else None,
        "window_precision_at_threshold": round(float(positives[alerts].mean()), 4) if alerts.any() else None,
        "median_lead_time_hours": round(float(np.median(lead_times)), 1) if len(lead_times) else None,
        "caught_encounters_any_hour": int(caught_any.shape[0]),
        "median_lead_time_hours_any_hour": round(float(np.median(lead_times_any)), 1) if len(lead_times_any) else None,
    }


def alert_table(split: dict, y_prob: np.ndarray, stride: int) -> list[dict]:
    """The alert-rate and lead-time table for REPORT.md: the same numbers at several thresholds,
    so the trade-off between alert burden and caught deteriorations is visible. pos_weight
    inflates every probability, which is why the reference threshold sits where it does and why
    students are asked to pick their own with this table in hand."""
    return [alert_metrics(split, y_prob, stride, t) for t in ALERT_TABLE_THRESHOLDS]


def print_metrics(name: str, metrics: dict) -> None:
    print(f"{name} metrics: AUROC {metrics['auroc']}, average precision {metrics['average_precision']}, Brier {metrics['brier']}")


# ---------------------------------------------------------------- export

def export_onnx(model: nn.Module, path: Path) -> None:
    """Write the graph the API serves: opset 17, batch dimension dynamic, probability out."""
    _, ProbabilityHead = build_gru_net()
    wrapped = ProbabilityHead(copy.deepcopy(model).cpu().float()).eval()
    example = (torch.zeros(2, WINDOW_HOURS, len(SEQUENCE_FEATURES)), torch.zeros(2, len(STATIC_FEATURES)))
    # torch warns that a GRU exported with batch > 1 may break on *variable-length* sequences;
    # ours is always 24 steps, and check_onnx.py confirms batch 10 matches torch
    warnings.filterwarnings("ignore", message=".*batch_size other than 1.*GRU.*")
    torch.onnx.export(
        wrapped, example, str(path),
        input_names=["sequence", "static"], output_names=["probability"],
        dynamic_axes={"sequence": {0: "batch"}, "static": {0: "batch"}, "probability": {0: "batch"}},
        opset_version=ONNX_OPSET, dynamo=False,
    )


def check_onnx(path: Path, npz_path: Path) -> str:
    """Compare the ONNX graph with torch on the saved windows when onnxruntime is installed here;
    otherwise say how to run check_onnx.py from the backend venv."""
    try:
        import onnxruntime as ort
    except ImportError:
        return f"onnxruntime not installed in this environment; run: python training/check_onnx.py {path.parent.as_posix()}"
    saved = np.load(npz_path)
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    onnx_prob = session.run(["probability"], {"sequence": saved["sequence"], "static": saved["static"]})[0]
    gap = float(np.max(np.abs(onnx_prob - saved["torch_probability"])))
    return f"onnx vs torch on {len(onnx_prob)} test windows: max abs gap {gap:.2e} ({'OK' if gap < 1e-4 else 'TOO LARGE'})"


# ---------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="training/data", help="folder with vitals_hourly.csv and encounters_static.csv")
    parser.add_argument("--out", default=f"training/runs/{date.today():%Y-%m-%d}-deterioration",
                        help="where deterioration.onnx, deterioration.json, training_run.json and train.log go")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--stride", type=int, default=1, help="hours between as-of windows (1 = reference, 4 = quick)")
    parser.add_argument("--epochs", type=int, default=40, help="upper bound; early stopping usually ends sooner")
    parser.add_argument("--no-mask", action="store_true", help="zero the mask channel (the cadence ablation)")
    parser.add_argument("--time-one-epoch", action="store_true", help="time one epoch on cpu and on cuda before training")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-minutes", type=float, default=14.0, help="safety stop for the one-hour lab session")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(out_dir / "train.log")

    started = time.monotonic()
    trained_at = datetime.now(timezone.utc)
    trained_on, instance_hint = detect_ec2()
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        sys.exit("--device cuda but torch.cuda.is_available() is False; check nvidia-smi and the torch build")
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    print(f"training started {trained_at:%Y-%m-%d %H:%M:%S} UTC on {trained_on}" + (f" ({instance_hint})" if instance_hint else ""))
    print(f"python {platform.python_version()}, torch {torch.__version__} (cuda {torch.version.cuda}), numpy {np.__version__}, "
          f"pandas {pd.__version__}, scikit-learn {sklearn.__version__}")
    print(f"device {device}, gpu {gpu_name or 'none'}")
    if torch.cuda.is_available():
        print_nvidia_smi()
    set_seed(args.seed)

    # ---- data
    vitals_path, static_path = data_dir / "vitals_hourly.csv", data_dir / "encounters_static.csv"
    data_sha = sha256_of(vitals_path)
    hourly = pd.read_csv(vitals_path)
    static = pd.read_csv(static_path)
    patient_of = dict(zip(static["encounter_id"], static["patient_id"]))
    onset_encounters = int(static["onset_hour"].notna().sum())
    print(f"data {vitals_path.as_posix()} sha256 {data_sha[:12]}...: {len(hourly)} encounter-hours, {len(static)} encounters, "
          f"{static[GROUP].nunique()} patients, {onset_encounters} with an onset")

    build_started = time.monotonic()
    windows = build_windows(hourly, static, args.stride)
    n_windows, n_positive = len(windows["label"]), int(windows["label"].sum())
    print(f"windows at stride {args.stride}: {n_windows} ({n_positive} positive, prevalence {n_positive / n_windows:.2%}) "
          f"from {len(np.unique(windows['encounter_id']))} encounters; built in {time.monotonic() - build_started:.1f}s")

    # ---- grouped split, standardiser from the training rows only
    train, val, test = grouped_split(windows, patient_of, args.seed)
    stats = fit_standardiser(train["values"], train["static"])
    use_mask = not args.no_mask
    for name, split in (("train", train), ("validation", val), ("test", test)):
        print(f"  {name}: {len(split['label'])} windows, {int(split['label'].sum())} positive, "
              f"{len(np.unique([patient_of[e] for e in split['encounter_id']]))} patients")
    print(f"mask channel: {'on' if use_mask else 'off (--no-mask ablation)'}")
    positives = train["label"].sum()
    pos_weight = float((len(train["label"]) - positives) / max(positives, 1))
    print(f"pos_weight {pos_weight:.1f} (negatives per positive in the training windows)")

    # ---- optional: one epoch on each device, same data, same batch size
    epoch_seconds = None
    if args.time_one_epoch:
        epoch_seconds = {"cpu": round(time_one_epoch("cpu", train, stats, use_mask, pos_weight, args.seed), 2)}
        print(f"one epoch on cpu: {epoch_seconds['cpu']}s ({torch.get_num_threads()} threads)")
        if torch.cuda.is_available():
            time_one_epoch("cuda", train, stats, use_mask, pos_weight, args.seed)  # warm-up: cudnn picks kernels
            epoch_seconds["cuda"] = round(time_one_epoch("cuda", train, stats, use_mask, pos_weight, args.seed), 2)
            print(f"one epoch on cuda: {epoch_seconds['cuda']}s ({gpu_name})")
        else:
            print("no cuda device: only the cpu epoch was timed")

    # ---- train with early stopping on validation average precision
    set_seed(args.seed)
    seq_train, st_train, y_train = to_tensors(train, stats, use_mask, device)
    seq_val, st_val, _ = to_tensors(val, stats, use_mask, device)
    model, optimizer, scaler, loss_fn = make_model_and_optimizer(pos_weight, device)
    best_ap, best_epoch, best_state, epochs_run, stopped_by = -1.0, 0, None, 0, "patience"
    fit_started = time.monotonic()
    for epoch in range(1, args.epochs + 1):
        epoch_started = time.monotonic()
        loss = run_epoch(model, optimizer, scaler, loss_fn, seq_train, st_train, y_train, device)
        val_ap = float(average_precision_score(val["label"], predict(model, seq_val, st_val)))
        epochs_run = epoch
        marker = ""
        if val_ap > best_ap:
            best_ap, best_epoch, best_state, marker = val_ap, epoch, copy.deepcopy(model.state_dict()), "  *"
        print(f"epoch {epoch:2d}  loss {loss:.4f}  validation AP {val_ap:.4f}  {time.monotonic() - epoch_started:.1f}s{marker}")
        if epoch - best_epoch >= PATIENCE:
            break
        if (time.monotonic() - started) / 60 > args.max_minutes:
            stopped_by = "time_limit"
            print(f"stopping: --max-minutes {args.max_minutes} reached")
            break
    else:
        stopped_by = "max_epochs"
    model.load_state_dict(best_state)
    print(f"fit took {time.monotonic() - fit_started:.1f}s; {epochs_run} epochs, best epoch {best_epoch} "
          f"(validation AP {best_ap:.4f}), stopped by {stopped_by}")

    # ---- evaluate: training rows show what was memorised, held-out patients count
    train_prob = predict(model, seq_train, st_train)
    seq_test, st_test, _ = to_tensors(test, stats, use_mask, device)
    test_prob = predict(model, seq_test, st_test)
    train_metrics = metrics_for(train["label"], train_prob)
    metrics = metrics_for(test["label"], test_prob)
    calibration = calibration_table(test["label"], test_prob)
    alerts = alert_metrics(test, test_prob, args.stride, ALERT_THRESHOLD)
    print_metrics("train", train_metrics)
    print_metrics("test ", metrics)
    print(f"  (a big gap between the two AUROC values means overfitting; always-predict-the-base-rate Brier "
          f"would be {float(np.var(test['label'])):.4f})")
    print("calibration (bin, count, mean predicted, observed):")
    for row in calibration:
        predicted = "-" if row["mean_predicted"] is None else f"{row['mean_predicted']:.3f}"
        observed = "-" if row["observed"] is None else f"{row['observed']:.3f}"
        print(f"  {row['lower']:.1f}-{row['upper']:.1f}  {row['count']:6d}  {predicted}  {observed}")
    print(f"alerts at threshold {ALERT_THRESHOLD}: {alerts['alerts']} over {alerts['patient_days']} patient-days = "
          f"{alerts['alerts_per_100_patient_days']} per 100 patient-days; "
          f"caught {alerts['caught_encounters']} of {alerts['onset_encounters']} deteriorations inside the 12-hour horizon "
          f"(sensitivity {alerts['sensitivity_at_threshold']}), median lead time {alerts['median_lead_time_hours']} h "
          f"({alerts['caught_encounters_any_hour']} caught counting any earlier alert, median lead {alerts['median_lead_time_hours_any_hour']} h); "
          f"window recall {alerts['window_recall_at_threshold']}, window precision {alerts['window_precision_at_threshold']}")
    table = alert_table(test, test_prob, args.stride)
    print("alert-rate and lead-time table (threshold, alerts per 100 patient-days, caught/onsets inside the horizon, "
          "median lead h, window precision, caught/onsets counting any earlier alert):")
    for row in table:
        print(f"  {row['alert_threshold']:.2f}  {row['alerts_per_100_patient_days']:7.2f}  {row['caught_encounters']:3d}/{row['onset_encounters']:<3d}  "
              f"{row['median_lead_time_hours']!s:>5}  {row['window_precision_at_threshold']}  "
              f"{row['caught_encounters_any_hour']:3d}/{row['onset_encounters']:<3d}")

    # ---- artifacts
    model_version = f"deterioration-{trained_at:%Y%m%d}-{data_sha[:8]}"
    onnx_path, json_path = out_dir / "deterioration.onnx", out_dir / "deterioration.json"
    export_onnx(model, onnx_path)
    # ten test windows spread over the probability range, so the check covers more than near-zero scores
    pick = np.argsort(test_prob)[np.linspace(0, len(test_prob) - 1, min(CHECK_WINDOWS, len(test_prob))).astype(int)]
    np.savez(out_dir / "onnx_check.npz",
             sequence=seq_test[pick].cpu().numpy(), static=st_test[pick].cpu().numpy(), torch_probability=test_prob[pick])
    print(check_onnx(onnx_path, out_dir / "onnx_check.npz"))
    artifact_sha = sha256_of(onnx_path)
    artifact = {
        "model": MODEL,
        "model_version": model_version,
        "question": "Will this admitted inpatient's vitals cross the deterioration line within the next 12 hours?",
        "trained_at": trained_at.isoformat(),
        "trained_on": trained_on,
        "onset_rule": onset_rule_as_dict(),
        "window_hours": WINDOW_HOURS,
        "horizon_hours": HORIZON_HOURS,
        "first_as_of_hour": FIRST_AS_OF_HOUR,
        "sequence_features": list(SEQUENCE_FEATURES),
        "static_features": list(STATIC_FEATURES),
        "sex_codes": {"female": 0, "male": 1},
        "admission_type_codes": {"inpatient": 0, "observation": 1, "emergency": 2, "outpatient": 3},
        "standardisation": stats,
        "mask_channel": use_mask,
        "alert_threshold": ALERT_THRESHOLD,
        "bands": {"low_below": BAND_LOW, "high_from": BAND_HIGH},
        "onnx": {"file": onnx_path.name, "opset": ONNX_OPSET, "inputs": {"sequence": ["batch", WINDOW_HOURS, len(SEQUENCE_FEATURES)],
                 "static": ["batch", len(STATIC_FEATURES)]}, "output": {"probability": ["batch"]}, "sha256": artifact_sha},
        "metrics": metrics,
        "alerts": alerts,
    }
    json_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"saved {onnx_path.as_posix()} ({onnx_path.stat().st_size / 1024:.0f} KB) sha256 {artifact_sha[:12]}... and {json_path.name}")

    # ---- the evidence file
    run = {
        "model": MODEL,
        "question": MODEL,
        "model_version": model_version,
        "trained_at": trained_at.isoformat(),
        "trained_on": trained_on,
        "instance_hint": instance_hint,
        "device": device.type,
        "gpu_name": gpu_name,
        "python": platform.python_version(),
        "packages": {"torch": torch.__version__, "cuda": torch.version.cuda, "numpy": np.__version__,
                     "pandas": pd.__version__, "scikit-learn": sklearn.__version__, "onnx": _onnx_version()},
        "data_file": vitals_path.name,
        "data_sha256": data_sha,
        "static_file": static_path.name,
        "static_sha256": sha256_of(static_path),
        "stride": args.stride,
        "windows": n_windows,
        "positive_windows": n_positive,
        "prevalence": round(n_positive / n_windows, 4),
        "encounters": int(len(static)),
        "onset_encounters": onset_encounters,
        "split": {"method": "grouped_by_patient", "group": GROUP, "test_fraction": TEST_FRACTION,
                  "validation_fraction_of_train": VALIDATION_FRACTION, "random_state": args.seed,
                  "train_windows": int(len(train["label"])), "validation_windows": int(len(val["label"])),
                  "test_windows": int(len(test["label"]))},
        "hyperparameters": {"hidden": 64, "head": 32, "batch_size": BATCH_SIZE, "lr": LEARNING_RATE,
                            "weight_decay": WEIGHT_DECAY, "max_epochs": args.epochs, "patience": PATIENCE,
                            "pos_weight": round(pos_weight, 2), "autocast": device.type == "cuda"},
        "mask_channel": use_mask,
        "epochs_run": epochs_run,
        "best_epoch": best_epoch,
        "stopped_by": stopped_by,
        "epoch_seconds": epoch_seconds,
        "train_metrics": train_metrics,
        "metrics": metrics,
        "calibration": calibration,
        "alert_threshold": ALERT_THRESHOLD,
        "alerts_per_100_patient_days": alerts["alerts_per_100_patient_days"],
        "sensitivity_at_threshold": alerts["sensitivity_at_threshold"],
        "median_lead_time_hours": alerts["median_lead_time_hours"],
        "alert_detail": alerts,
        "alert_table": table,
        "artifact_file": onnx_path.name,
        "artifact_sha256": artifact_sha,
        "artifact_json_file": json_path.name,
        "artifact_json_sha256": sha256_of(json_path),
    }
    (out_dir / "training_run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {(out_dir / 'training_run.json').as_posix()} and {(out_dir / 'train.log').as_posix()}")
    print(f"model_version {model_version}; total {time.monotonic() - started:.1f}s")
    print(f"to serve it: copy {onnx_path.name} and {json_path.name} to models/ and restart the backend")
    if trained_on == "local":
        print("NOTE: trained_on is 'local'. The course requires the committed artifact to come from the GPU lab; "
              "if you are on an EC2 instance and see this, the metadata service did not answer.")


def _onnx_version() -> str | None:
    try:
        import onnx
        return onnx.__version__
    except ImportError:
        return None


if __name__ == "__main__":
    main()
