"""Check that the exported ONNX graph gives the same probabilities as the torch model did.

    python training/check_onnx.py training/runs/<date>-deterioration

train_deterioration.py saves ten test windows and the probabilities torch computed for them in
<run>/onnx_check.npz. This script runs the same windows through <run>/deterioration.onnx with
onnxruntime (the library the API uses; it needs no torch) and prints the largest gap. Anything
under 1e-4 means the graph is the model. Run it from the backend venv, which has onnxruntime.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

TOLERANCE = 1e-4


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python training/check_onnx.py <run folder>")
    run_dir = Path(sys.argv[1])
    saved = np.load(run_dir / "onnx_check.npz")
    session = ort.InferenceSession(str(run_dir / "deterioration.onnx"), providers=["CPUExecutionProvider"])
    inputs = [(i.name, i.shape) for i in session.get_inputs()]
    outputs = [(o.name, o.shape) for o in session.get_outputs()]
    onnx_prob = session.run(["probability"], {"sequence": saved["sequence"], "static": saved["static"]})[0]
    gap = float(np.max(np.abs(onnx_prob - saved["torch_probability"])))
    print(f"onnxruntime {ort.__version__}; inputs {inputs}; outputs {outputs}")
    for torch_p, onnx_p in zip(saved["torch_probability"], onnx_prob):
        print(f"  torch {torch_p:.6f}  onnx {onnx_p:.6f}")
    print(f"max abs gap over {len(onnx_prob)} windows: {gap:.2e} -> {'OK' if gap < TOLERANCE else 'TOO LARGE'}")
    sys.exit(0 if gap < TOLERANCE else 1)


if __name__ == "__main__":
    main()
