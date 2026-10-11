# models/

Trained model files the API serves. The backend sees this folder at `/app/models` (mounted by
Compose, copied into the image for Kubernetes) and reads the file named by `MODEL_PATH`,
default `/app/models/readmission.joblib`.

| File | What it is |
|---|---|
| `readmission.joblib` | The 30-day readmission model: a dict with `pipeline` (a scikit-learn Pipeline), `feature_columns`, `categorical_columns`, `model_version`, `question`, `trained_at`, `trained_on`. Written by `training/train.py` into its `--out` folder and copied here. |

The evidence for how the file was produced lives in `training/runs/<date>/training_run.json`;
its `artifact_sha256` must match the file here:

```bash
sha256sum models/readmission.joblib                        # Git Bash, macOS, Linux
Get-FileHash models\readmission.joblib -Algorithm SHA256   # PowerShell
```

Optionally set `MODEL_SHA256` to that hash in the environment. The API then checks the file
before unpickling it and answers 503 instead of loading something that does not match. A
joblib file is a pickle and runs code when opened, which is why the check comes first.

`.gitignore` ignores everything in this folder except `*.joblib` and this README, so a model you
train is committed, and scratch files are not. Load a model only with the same pinned
scikit-learn and joblib versions it was saved with (`backend/requirements.txt`).
