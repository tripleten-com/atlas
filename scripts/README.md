# scripts/

Two checks you run from the repository root with the Compose stack up. Both use only the Python
standard library, so the Python you already have is enough (3.12 on the host is the course
setting). Neither needs an AWS login or a GPU, and neither prints a key or a password.

| Script | What it is for |
|---|---|
| `smoke.py` | Is the whole stack up and answering? `python scripts/smoke.py` for Compose, `python scripts/smoke.py --base http://localhost` for the Kubernetes cluster through its Ingress. Exits non-zero on the first failure. |
| `check_submission.py` | The automated check that closes Tasks 2, 3 and 4. The reviewer platform runs the same script, so run it before you submit and paste its output into `docs/REPORT.md`. |

## check_submission.py

```
python scripts/check_submission.py --task 2
python scripts/check_submission.py --task 3
python scripts/check_submission.py --task 4
python scripts/check_submission.py --task 3 --base http://localhost:8000 --repo . --eval-threshold 0.7
```

One line per check, `PASS`, `FAIL` or `WARN` with the reason in brackets, then a count. The exit
code is 0 when nothing failed and 1 otherwise. A `WARN` never fails the run: it marks a cutline
the project allows (a model labelled `trained_on: local`), or a check that could not run on your
machine (no cluster, no LLM key on the server, Docker not available). Read every `WARN` anyway: the
reviewer sees the same line.

Before you run it: `docker compose up -d --build` so the API answers at `--base` (default
`http://localhost:8000`), `docker compose` on PATH (the script runs `pytest` inside the backend
container and, for Task 3, starts a second API container), and for Task 4 `kubectl` and `git`.
Logins use the two demo clinicians (`clinician@riverside.example`, who owns the Test Facility,
and `clinician@harborview.example` in the other organisation) and the demo admin
(`admin@riverside.example`) with the password in the environment variable `DEMO_PASSWORD`
(default `atlas-demo`). The emails can be changed with `--email`, `--other-email` and
`--admin-email` if your fork renamed them.

### Task 2: what it reads

- `training/runs/*/training_run.json`: one record with a `question` (your clinical question) and
  one with `model: deterioration_12h` (the early-warning model). Each must carry `metrics` with
  `auroc`, `average_precision` and `brier`, `train_metrics.auroc`, and a `calibration` list of
  exactly ten bins.
- `models/`: some file there (`.joblib`, `.onnx`) must hash to each record's `artifact_sha256`.
  The file name is yours; the hash is the proof the committed artifact is the one you trained.
- `trained_on`: `ec2` passes; `local` is a `WARN` (the cutline for a failed Codio session, reported
  not failed); anything else fails.
- The GPU run: `gpu_name` must be set and `epoch_seconds.cuda` below `epoch_seconds.cpu` (run
  `train_deterioration.py --time-one-epoch`). Missing values are a `WARN`.
- `docker compose exec backend pytest tests -k "risk or early_warning"` must pass against the
  running stack.
- The two risk routes, `GET /api/patients/{id}/risk/readmission` and
  `GET /api/patients/{id}/risk/deterioration`: 401 without a token; 404 for a Test Facility
  patient when the other organisation's clinician asks, with the same body as a missing patient;
  and for Test Facility patients a 200 with the documented fields (the script tries a page of
  discharged patients for the readmission route and a page of inpatients for the early-warning
  route). Only the documented "not applicable" 404s or a 503 is a `WARN`: the route is honest but
  the model never scored anyone.
- The frontend bundle: the script rebuilds `frontend/dist` through the frontend container when it
  can, then searches every `.js`, `.html`, `.css`, `.json` and `.map` file for the values of
  `LLM_API_KEY` and `MONGO_PASSWORD` read quietly from `.env`, `k8s/secret.env` and the committed
  example files. A value in the bundle fails the task.

### Task 3: what it reads

- One `POST /api/patients/{id}/summary` (or your generative route, if you kept the path): a 200
  with text, tokens and source rows passes; 429 (ceiling spent today) and 503 (no `LLM_API_KEY` on
  the server) are `WARN`. Before and after the call the script reads `GET /api/usage?days=1` as
  the demo admin: `total_events` must grow by exactly one and the newest row must carry tokens or a
  status other than `ok`.
- `evals/golden.jsonl` has at least ten lines, and `evals/run_evals.py --threshold <your
  threshold> --no-store` exits 0 against the running API. Pass your REPORT.md threshold with
  `--eval-threshold` (default 0.7). This spends real tokens; it is skipped with a `WARN` when the
  server has no key.
- `GET /api/patients/{id}/ai/history?feature=readmission-risk`: after a fresh readmission score the
  newest item must be that score (same `model_version` and probability, written in the last five
  minutes) with `stored: true`; the other organisation's clinician gets 404 for the same patient.
- `GET /health` reports `ai_store: ok`.
- The store-down fallback: the script starts a second API container with
  `docker compose run -d --no-deps --name atlas-check-api -p 8001:8000 -e MONGO_URL=mongodb://127.0.0.1:1/atlas_ai -e LLM_DAILY_TOKEN_LIMIT=1 backend uvicorn app.main:app --host 0.0.0.0 --port 8000`
  (port 1 is closed, so the store is unreachable; `--alt-port` changes 8001), waits for its
  `/health`, and asks it for the readmission score: 200 with `stored: false` passes. The same
  process has a one-token daily ceiling, so the summary route must answer 429 on the first or second
  call. The container is removed afterwards. When Docker cannot start it, both checks are `WARN`
  with the reason.
- `docker compose exec backend pytest tests -k tenant` must collect and pass tests: your
  tenant-isolation tests need `tenant` in their names.

### Task 4: what it reads

- `k8s/base/*.yaml`: `kubectl kustomize k8s/base` renders (a `WARN` when kubectl is missing; the
  files are then checked directly); every Deployment has `readinessProbe` and `livenessProbe`, and
  the `api` Deployment also a `startupProbe`; no `image:` line and no `newTag` uses `latest`;
  `mongo.yaml` holds a StatefulSet with `volumeClaimTemplates` and a Service with `clusterIP: None`.
- Secrets: `git ls-files k8s/secret.env` must find nothing, and `k8s/base/secret.example.env`
  must carry exactly the six keys `POSTGRES_PASSWORD`, `JWT_SECRET`, `MERIDIAN_API_KEY`,
  `LLM_API_KEY`, `MONGO_USER`, `MONGO_PASSWORD` with `LLM_API_KEY` empty.
- The cluster, when `kubectl -n atlas get pods` answers: every Pod `Ready` (the finished seed pod
  counts as done) and a Pod named `mongo-0` present; then `scripts/smoke.py --base http://localhost`
  (`--ingress` changes the address) must exit 0. When kubectl cannot connect both lines are `WARN
  (no cluster reachable)`, so the manifest part still runs without a cluster.
- `docs/REPORT.md` contains the rollback drill: the text `rollout undo` and either
  `ImagePullBackOff` or the startup-probe stall.

### Pasting the output

Copy the whole block from `Atlas submission check` to the final count into the task's section of
`docs/REPORT.md`. Do not edit the lines; a reviewer compares them with the platform's own run.
