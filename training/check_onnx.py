"""Check that the exported ONNX graph gives the same probabilities as the torch model did.

    python training/check_onnx.py training/runs/<date>-deterioration
    python training/check_onnx.py training/runs/<date>-deterioration --onnx models/deterioration.onnx

train_deterioration.py saves ten test windows and the probabilities torch computed for them in
<run>/onnx_check.npz. This script runs the same windows through the ONNX graph with onnxruntime
(the library the API uses; it needs no torch) and prints the largest gap. Anything under 1e-4
means the graph is the model. Run it from the backend venv, which has onnxruntime.

Which graph: --onnx when you pass it, otherwise <run>/deterioration.onnx when the run folder
still holds one (straight after training), otherwise models/deterioration.onnx, the committed
copy. Artifacts under training/runs/ are gitignored, so on a fresh clone the models/ copy is the
one checked.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

TOLERANCE = 1e-4
COMMITTED_ONNX = Path(__file__).resolve().parent.parent / "models" / "deterioration.onnx"


def main() -> None:
    parser = argparse.ArgumentParser(description="replay a run's saved test windows through the ONNX graph")
    parser.add_argument("run_dir", type=Path, help="the run folder that holds onnx_check.npz")
    parser.add_argument("--onnx", type=Path, default=None,
                        help="the graph to check; default <run>/deterioration.onnx, else models/deterioration.onnx")
    args = parser.parse_args()
    run_dir = args.run_dir
    onnx_path = args.onnx or (run_dir / "deterioration.onnx" if (run_dir / "deterioration.onnx").exists() else COMMITTED_ONNX)
    if not (run_dir / "onnx_check.npz").exists():
        sys.exit(f"no onnx_check.npz in {run_dir.as_posix()}")
    if not onnx_path.exists():
        sys.exit(f"no ONNX graph at {onnx_path.as_posix()}")
    saved = np.load(run_dir / "onnx_check.npz")
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    inputs = [(i.name, i.shape) for i in session.get_inputs()]
    outputs = [(o.name, o.shape) for o in session.get_outputs()]
    onnx_prob = session.run(["probability"], {"sequence": saved["sequence"], "static": saved["static"]})[0]
    gap = float(np.max(np.abs(onnx_prob - saved["torch_probability"])))
    print(f"checking {onnx_path.as_posix()} against {(run_dir / 'onnx_check.npz').as_posix()}")
    print(f"onnxruntime {ort.__version__}; inputs {inputs}; outputs {outputs}")
    for torch_p, onnx_p in zip(saved["torch_probability"], onnx_prob):
        print(f"  torch {torch_p:.6f}  onnx {onnx_p:.6f}")
    print(f"max abs gap over {len(onnx_prob)} windows: {gap:.2e} -> {'OK' if gap < TOLERANCE else 'TOO LARGE'}")
    sys.exit(0 if gap < TOLERANCE else 1)


if __name__ == "__main__":
    main()
