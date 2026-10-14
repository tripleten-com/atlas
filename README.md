# Atlas

Atlas is the reference platform for Project 5: a finished example of the shape your product
should have, which you fork and extend. It is the patient-management platform built by Atlas
Health, a fictional forty-person company selling to hospitals, clinics and research groups. A
clinician logs in, picks a facility, searches patients, and reads one patient's visits, vitals,
labs and medications on a single page. Admins watch the dashboards across the caseload, the
Usage page, the audit log and the health of the data feed. Every customer is a separate tenant
whose records nobody outside it can reach.

Clinical data reaches Atlas the way it does in real life: hospital systems send it to a
health-data network, Meridian Clinical Exchange, which forwards it to Atlas in signed webhook
deliveries. Meridian runs here as a mock service that simulates three live facilities.

Three AI features are already built and wired into the patient record, so you can read working
code before writing your own: a readmission-risk model, the early-warning model every student
ships, and a stay summary written by a language model. Their outputs go to a MongoDB document
store next to the Postgres clinical record. Your capstone replaces the readmission model with
your own clinical question, keeps the early-warning model, adds your own generative or agentic
feature, and ships the whole thing on Kubernetes.

Everything is synthetic. No real patients.

## Start it

You need Docker Desktop (or Docker Engine with Compose v2), Python 3.12 for the scripts, and
about 4 GB of free memory for the Compose stack (8 GB once you run the Kubernetes cluster as
well: MongoDB now runs beside Postgres in both). On Windows, run every command in this repository in Git Bash (installed with git), not in
PowerShell or cmd: the commands use `curl`, `$(...)`, `&&` and `sha256sum`, which PowerShell
either lacks or reads differently.

```bash
cp .env.example .env
docker compose up --build
```

`up --build` keeps the terminal, so open a second one for everything below (or start with
`docker compose up -d --build` and read the logs with `docker compose logs -f`).

The first start takes a few minutes: images build, the database is created, a one-shot `seed`
service migrates the schema and writes the Test Facility's two years of history (about 40
seconds), and Meridian backfills two weeks of data for the live facilities. The API and the
worker start only after the seed has finished. When the logs settle:

| What | Where |
|---|---|
| Atlas | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Meridian (mock data feed) | http://localhost:8200 |
| PostgreSQL | `localhost:5433`, database `atlas`, user `atlas`, password `atlas` |
| MongoDB (AI outputs) | `localhost:27018`, database `atlas_ai`, user `atlas`, password `atlas-dev-only`, `authSource=admin` |

Then check everything is wired up:

```bash
python scripts/smoke.py
```

It logs in, reads a patient, scores one with the readmission model and asks for a stay
summary. The summary step is skipped, not failed, while `LLM_API_KEY` in `.env` is empty.
`python scripts/check_submission.py --task 2` (then `3`, then `4` once the cluster is up) is the
automated check that closes Tasks 2 to 4; it passes on the reference, with two `WARN` lines for
the models trained on this laptop rather than in the AWS lab (`scripts/README.md`).

## Demo logins

Password for every account: `atlas-demo`

| Email | Person | Organisation | Role |
|---|---|---|---|
| `clinician@riverside.example` | Dr. Maya Chen | Riverside Health | clinician |
| `admin@riverside.example` | Jordan Blake | Riverside Health | admin |
| `clinician@harborview.example` | Dr. Samuel Okoro | Harborview Medical Group | clinician |
| `admin@harborview.example` | Priya Nair | Harborview Medical Group | admin |

Admins also see Usage, the audit log, and the integration controls.

## Facilities and data

Use the facility switcher at the top right.

| Facility | Organisation | Data |
|---|---|---|
| Test Facility (`DEMO`) | Riverside Health | About 1,200 patients and 8,300 visits over two years, with vitals, labs and medications. Seeded once; it never changes. Train on this. |
| Riverside General Hospital (`RGH`) | Riverside Health | Live from Meridian: admissions, vitals, labs and medications as they happen |
| Northgate Clinic (`RNC`) | Riverside Health | Live from Meridian, outpatient visits only |
| Harborview Medical Center (`HMC`) | Harborview Medical Group | Live from Meridian |

Live facilities run in real time: each event arrives when it happens (a few dozen an hour per
hospital, in bursts as rounds and doses fall due), after a fourteen-day backfill on first
start. `docs/DATA_FEED.md` explains the feed, its event format, and how to speed it up.

The Test Facility's history is generated from a fixed random seed, so every reseed gives the
same patients and visits, shifted to end on the day you seeded. Row counts and ids therefore
differ slightly between machines seeded on different days; the committed training snapshot and
golden set are dated for that reason (see "Known gaps").

The clinical tables are `patients`, `encounters`, `vitals`, `labs`, `medications` and
`medication_administrations`. Read them yourself:

```bash
docker compose exec db psql -U atlas -d atlas
\dt
\d encounters
SELECT count(*) FROM patients;
```

## What is already built

The platform: login and sessions, tenant isolation on every query, the patient record with its
panels, dashboards, the Results worklist, the Integrations page with the Meridian feed
(signed webhooks, idempotent raw storage, an ingestion worker that applies events in order,
resync), live updates over Server-Sent Events, the Usage page fed by `record_usage()`, the
audit log, and a smoke test. Read `docs/ARCHITECTURE.md` for how the pieces fit.

Three reference AI features, all in the AI insights section of the patient record and all
behind auth and the tenant boundary:

| Feature | Kind | Route | What it does |
|---|---|---|---|
| Readmission risk | predictive | `GET /api/patients/{id}/risk/readmission` | For a patient whose inpatient or observation stay has ended, the chance that they come back (an admission or an emergency visit) within 30 days, as a probability and a band (low below 0.15, medium to 0.35, high above). A scikit-learn pipeline trained on the Test Facility. The response also says whether the 30-day window is still open, already closed, or already answered by a return visit, and the card shows the score as a forecast only while it is open. 404 "not applicable" when the patient has no finished stay or is in hospital right now, 503 when no model file is present. |
| Early warning | predictive | `GET /api/patients/{id}/risk/deterioration` | For an inpatient who is in hospital right now, the chance that their vitals cross the deterioration line (two of: heart rate above 120, respiratory rate above 24, SpO2 below 90, systolic pressure below 90, temperature above 38.5) within the next 12 hours. A one-layer GRU over the last 24 hours of vitals on an hourly grid plus four static features, trained with PyTorch, exported to ONNX and served on CPU with onnxruntime. Same bands as the readmission card; `alert` when the probability is at or above the threshold in `models/deterioration.json` (0.20). `state` is `scored`, `insufficient_data` (before hour 12 of the stay or no vitals) or `deteriorating` (the rule already fired; the hour it did, no forecast). 404 "not applicable" when the patient has no open inpatient stay, 503 when the model file is missing. |
| Stay summary | generative | `POST /api/patients/{id}/summary` | Four to six sentences about the latest visit, written by an OpenAI-compatible model from rows the backend fetched (the encounter, the last reading of each vital, up to twenty labs, the stay's medication orders). Returns the row ids as `sources`. 503 "no model configured" while `LLM_API_KEY` is empty, 429 "daily token limit reached" when the tenant has spent `LLM_DAILY_TOKEN_LIMIT` tokens today. |

All three record every call, including refusals and failures, with `record_usage()`, which is
what the Usage page and the daily ceiling count, and write the output as a document to MongoDB
under the same `request_id` (`docs/AI_STORE.md`); each card lists the last five earlier outputs
from `GET /api/patients/{id}/ai/history`. All three have tests (`backend/tests/test_risk.py`,
`backend/tests/test_early_warning.py`, `backend/tests/test_summary.py`) including the
cross-tenant 404, and `backend/tests/test_ai_store.py` covers the store. The summary feature
also has a golden set and code evaluators in `evals/`; the two models have their training
evidence in `training/runs/`.

The language model is any OpenAI-compatible endpoint, chosen by three environment variables:
`LLM_BASE_URL` (default Nebius Token Factory), `LLM_MODEL` (default
`Qwen/Qwen3-235B-A22B-Instruct-2507`) and `LLM_API_KEY` (empty by default; put your own key in
`.env`, never in a file you commit). `LLM_DAILY_TOKEN_LIMIT` (default 200000) is the number of
input plus output tokens one tenant may spend per UTC day across all generative features (the
check sums every `usage_events` row of the tenant, so a second feature shares the allowance);
the check happens in code before the call, and the UI shows the limit as a state when it is hit.

## Train the models on AWS

`training/` holds everything for both models. For the readmission model, `export_training_set.py`
reads the Test Facility from Postgres and writes `training/data/readmission.csv`; `train.py` fits
one scikit-learn pipeline and writes, into its `--out` folder, the artifact plus a run record
(`training_run.json`: metrics, a ten-bin calibration table, data and artifact hashes, where it
ran). You copy the artifact to `models/readmission.joblib`. For the early-warning model,
`export_vitals_hourly.py` writes `training/data/vitals_hourly.csv` and `encounters_static.csv`
(under 4 MB together), and `train_deterioration.py` (PyTorch; `--stride 4 --epochs 3` to
rehearse, `--time-one-epoch` to measure CPU against GPU on the same machine) writes
`deterioration.onnx`, `deterioration.json`, `training_run.json` and `train.log`; you copy the
`.onnx` and the `.json` to `models/`. `training/README.md` explains both questions, the
features, the onset rule, what the scores mean on the live facilities, and how to run the
scripts on your laptop.

The course requires your real artifacts to be trained on EC2 instances inside the Codio AWS
lab: the readmission model on a CPU instance (`training/CODIO.md`), the early-warning model on a
`g4dn.xlarge` with one T4 (`training/CODIO_GPU.md`). Each is a one-hour session: upload the data
and the script, launch the instance, install the pins, train, copy the results back, download
them before the hour ends. The reference runs in `training/runs/2026-10-11/` and
`training/runs/2026-10-14-deterioration/` were made on this laptop (the second on an RTX 4090,
not a T4) and are labelled `"trained_on": "local"`; your run records will say `"ec2"`.

The artifacts the API serves are `models/readmission.joblib` and `models/deterioration.onnx`
with `models/deterioration.json`. Compose mounts `models/` into the backend; the Kubernetes
image copies it in at build time.

## Run it on Kubernetes

Compose is the development loop. `k8s/` runs the same stack on a local cluster (kind by default,
Docker Desktop's built-in Kubernetes as the alternative) with Deployments for the web app, the
API, the worker and Meridian, Postgres on a PersistentVolumeClaim, MongoDB as a StatefulSet
(`mongo-0`), a seed Job, a ConfigMap and a Secret, three probes on the API, and one Ingress on
http://localhost.

Follow `k8s/README.md` sections 1 to 5 in order: build and tag the three images, create the
cluster and the ingress controller, load the images, create the Secret (with a fresh
`JWT_SECRET`), set `newTag` in `k8s/base/kustomization.yaml`, then `kubectl apply -k k8s/base`.
Skipping the Secret is the most common mistake: the api, worker and seed pods then sit in
`CreateContainerConfigError`. The same file has the ladder of proof, the rollback drill and the
broken Deployment exercise. The smoke test runs against the cluster with
`python scripts/smoke.py --base http://localhost`, and
`python scripts/check_submission.py --task 4` checks the manifests and the running cluster.

## Everyday commands

```bash
docker compose up --build              # start, rebuilding images that changed
docker compose ps -a                   # seed should show Exited (0); backend and db healthy
docker compose logs -f backend worker  # follow the API and the ingestion worker
docker compose exec backend pytest     # backend tests
docker compose exec backend ruff check app tests
docker compose exec frontend npx vite build          # the frontend must compile
docker compose exec backend python -m app.seed --reset   # rebuild the Test Facility history
python scripts/smoke.py                # end-to-end check (Compose)
.venv/Scripts/python training/export_training_set.py     # Windows; .venv/bin/python elsewhere
.venv/Scripts/python training/train.py                   # then copy training/runs/<today>/readmission.joblib to models/
.venv/Scripts/python training/export_vitals_hourly.py    # the early-warning training set (needs no torch)
<python with torch> training/train_deterioration.py --time-one-epoch   # then copy the .onnx and .json to models/
.venv/Scripts/python training/check_onnx.py training/runs/<today>-deterioration
python scripts/check_submission.py --task 2            # the automated check (also --task 3 and --task 4)
.venv/Scripts/python evals/make_golden.py && .venv/Scripts/python evals/run_evals.py --threshold 0.7
docker compose down                    # stop (keeps data)
docker compose down -v                 # stop and delete all data, then `up` starts fresh
```

The backend (`backend/`) and frontend (`frontend/src/`) are mounted into their containers, so
saving a file reloads the app. Changes to dependencies need `--build`. The training and eval
scripts run on your machine against `localhost:5433`, so make a virtual environment first:
`python -m venv .venv` and `.venv/Scripts/pip install -r backend/requirements.txt`.

## Where things live

```
backend/app/routers/     API routes, one file per area, registered in backend/app/main.py
backend/app/routers/risk.py      the predictive feature (readmission risk)
backend/app/routers/summary.py   the generative feature (stay summary)
backend/app/routers/early_warning.py   the early-warning model (deterioration within 12 hours)
backend/app/ml.py        the model's feature SQL (shared with training), load_model(), bands
backend/app/ml_deterioration.py   the ONNX session, the hourly window for one stay, the onset rule
backend/app/llm.py       the OpenAI-compatible client: configured(), chat()
backend/app/metering.py  record_usage(): one usage_events row per AI call
backend/app/ai_store.py  the MongoDB document store for AI outputs (docs/AI_STORE.md)
backend/app/routers/ai_history.py  earlier AI outputs for a patient, from the store
backend/app/ingest/      the Meridian client, raw event store, event processor, worker
backend/app/migrations/  the schema
backend/tests/           pytest against the seeded database; test_risk.py, test_early_warning.py, test_summary.py, test_ai_store.py
frontend/src/api/client.js   every API call the frontend makes
frontend/src/pages/      one file per screen (AiFeatures.jsx is the map of the AI features)
frontend/src/panels/     the cards on the patient record (RiskCard.jsx, EarlyWarningCard.jsx, SummaryCard.jsx, EarlierOutputs.jsx)
feed/meridian/           the mock data vendor
packages/clinsim/        the clinical simulator the seed and the feed share
training/                both exports and train scripts, deterioration.py, CODIO.md, CODIO_GPU.md, data/, runs/<date>-<model>/
models/                  the trained artifacts the API serves (/app/models in the container)
evals/                   golden set and evaluators for the generative feature
k8s/                     kind config, kustomize base, optional HPA, the broken-api exercise
scripts/smoke.py         end-to-end check for Compose and the cluster
scripts/check_submission.py   the automated check for Tasks 2 to 4 (scripts/README.md)
docs/                    architecture, data feed, adding a feature, the AI document store
REFERENCE_NOTES.md       every number measured on the reference build, with its command
```

Start with `docs/ARCHITECTURE.md`, then `docs/ADDING_A_FEATURE.md`.

## Known gaps

What the reference does not do, so you know where the edges are:

- The stay summary is a single synchronous call (about three to five seconds): the reference
  ships the Task 3 cutline. There is no streaming over SSE and no queued job with polling; the
  live-updates stream (`/api/activity/stream`, `backend/app/routers/activity.py`) is the SSE
  pattern to copy when you add it.
- There is no agentic feature: no tool loop, no model-chosen actions, no approval step.
- Both reference models were trained locally. `training/runs/2026-10-11/` and
  `training/runs/2026-10-14-deterioration/` say `"trained_on": "local"` and have no EC2 or S3
  screenshots. The lab runs are yours to do; the GPU run's timings come from an RTX 4090, so the
  T4 in the lab will be slower.
- The early-warning model ranks well (test average precision 0.89 on the reference seed) but its
  probabilities are inflated: training with `pos_weight` of about 65 pushes scores up, so the
  calibration table in its `training_run.json` shows observed rates far below the predicted ones
  and, at the 0.20 alert threshold, about 160 alerts per 100 patient-days on the test windows.
  The `alert_table` in the run record gives the trade-off at other thresholds; pick one and defend
  it in REPORT.md. The mask channel also encodes nursing cadence (`--no-mask` measures that).
  Because of the inflation, the card calls the number a model score, not a chance, and shows its
  low-confidence note for every score under 0.9 (the only calibration bin whose observed rate came
  close to its score).
- The early-warning run record counts a deterioration as caught only when an alert falls inside
  the 12-hour horizon (`caught_encounters`, `median_lead_time_hours`), and keeps the looser
  any-earlier-alert count beside it (`caught_encounters_any_hour`). On the reference seed every
  threshold in `alert_table` catches all 39 test deteriorations by both definitions, so that
  column does not help you choose a threshold; the alert rate and the window precision do.
- The early-warning window ends at the hour after the stay's latest vitals measurement, not at
  the wall clock, because the Test Facility is frozen on its seed day; the response's `as_of`
  says which hour was scored.
- The model's label counts an emergency visit as "came back"; the card and
  `training/README.md` say so. Its strongest feature, prior stays in the last year, is almost
  always zero at the live facilities (fourteen days of history), so scores there show the
  plumbing, not the accuracy. Rows from the first year of the Test Facility have a truncated
  history count (left-censoring). Encounters that ended in death are excluded from both
  training and serving.
- The daily token ceiling is a read-then-call check with no lock: requests that arrive at the
  same moment can each pass it and together overshoot the limit by up to one answer each.
- Writes to the AI document store are fire-and-forget: the Postgres metering row is written
  first, then the MongoDB document, with no transaction across the two. A crash between them, or
  a store that is down, loses the document and the response says `stored: false`. The two stores
  are reconciled by `request_id`; `docs/AI_STORE.md` has the query.
- `JWT_SECRET` ships as the same development placeholder in `.env.example` and
  `k8s/base/secret.example.env`. Anyone who reads the repository can forge session tokens for a
  deployment that keeps it; the API logs a warning at startup until you set a fresh one.
- The API docs (`/docs`, `/openapi.json`) and `/health` are public through the Ingress, and
  `/health` reports the migration count. Fine for a laptop; a production Ingress would
  restrict them.
- The committed training snapshot, run record, artifact and golden set come from one seed day.
  Re-running `export_training_set.py` on a database seeded on another day gives a different
  row count and different metrics, and `run_evals.py` refuses a golden set whose encounter ids
  no longer match until you run `make_golden.py` again. `REFERENCE_NOTES.md` records both runs.
- `k8s/optional/hpa.yaml` is lesson material and untested here (no metrics-server installed).
- No TLS. kind maps port 443 but nothing terminates it; everything is plain http://localhost.
- Postgres is one replica with no backups. The PersistentVolumeClaim survives pod restarts,
  not `kind delete cluster`.
- Secrets are plain Kubernetes Secrets created from a local env file; there is no external
  secret store and no rotation.
- No rate limiting on login or on the AI routes beyond the daily token ceiling, and no
  per-user ceiling.
- The frontend has no automated tests; `vite build` passing is the compile check.
- Test runs write real rows to `usage_events`, so the Usage page shows them.
- Meridian has no route through the Ingress; the smoke test skips its own health check on the
  cluster unless you port-forward it.

## Troubleshooting

- A port is already in use. Change `WEB_PORT`, `API_PORT`, `MERIDIAN_PORT` or `DB_PORT`
  in `.env` and run `docker compose up` again. If you change `WEB_PORT` or `API_PORT`, update
  `CORS_ORIGINS` and `VITE_API_URL` to match.
- The page says it can't reach the API. `docker compose ps -a` should show `backend` as
  healthy and `seed` as `Exited (0)`. `docker compose logs seed backend` shows why if not; the
  backend does not start until the seed has finished.
- Live facilities show no data. `docker compose logs worker meridian`. The Integrations
  page shows each connection's last delivery and any errors.
- The risk card says "Model unavailable". `models/readmission.joblib` is missing, `MODEL_PATH`
  points somewhere else, or `MODEL_SHA256` is set and does not match the file. Run
  `training/train.py` and copy the artifact in, or restore the file, then
  `docker compose restart backend` and read its first log lines. The early-warning card says the
  same when `models/deterioration.onnx` or its `.json` is missing or `MODEL_DETERIORATION_SHA256`
  does not match.
- The cards say "history unavailable" and `/health` says `"ai_store":"unavailable"`. The `mongo`
  service is down or still starting: `docker compose ps mongo`, `docker compose logs mongo`.
  Scores and summaries still work and answer `stored: false`.
- The summary card says "No model configured". `LLM_API_KEY` in `.env` is empty. Add your key
  and `docker compose up -d backend` so the container picks it up.
- Edits don't reload on Windows or macOS. Polling is on by default in `docker-compose.yml`
  (`CHOKIDAR_USEPOLLING`, `WATCHFILES_FORCE_POLLING`). Restart the service if a change is
  missed.
- Start over. `docker compose down -v && docker compose up --build`.
