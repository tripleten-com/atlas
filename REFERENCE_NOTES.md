# Reference notes: the numbers behind the Atlas reference build

Every number a lesson or a project page may quote about the reference build, with the command
that produced it and the date it was observed. Sections 1 to 8 are the 2026-10-11 pass (the
platform, the readmission model, the stay summary, the first cluster); sections 9 to 15 are the
2026-10-14 pass that added the early-warning model, the MongoDB document store and the automated
check. Numbers marked "reported, not re-run" come from the build agents' reports and were not
reproduced in the verification pass of that date; everything else was observed in that pass
(Windows 11, Docker Desktop, Docker server 29.5.2, 16 GB of memory, Python 3.12.3 in `.venv`).
Where a later section re-measured a number, the later section wins.

Student pages should keep these soft ("about 1,200 patients", "around 0.75 AUROC"): the seed
shifts its two-year window to end on the seed day, so another machine gets slightly different
counts, and the metrics move with them (see section 3).

## 1. The Compose stack from clean

```bash
docker compose down -v
docker compose up -d --build
```

| What | Observed | How |
|---|---|---|
| `down -v` | 2 s | timestamps around the command |
| `up -d --build` returned (images cached, backend healthy, worker started) | 56 s | timestamps around the command |
| Seed service (`python -m app.seed`), fresh volume | 38 s, `[seed] done in 38s` | `docker compose logs seed` |
| Backend `Uvicorn running` after the seed exited | under 1 s | `docker compose logs -t seed backend` |
| Backend healthy after `up` returned | about 6 s (reported, not re-run; the `up` timing above includes it) | |
| Services after start | `seed` Exited (0); `backend`, `db`, `meridian` healthy; `frontend`, `worker` Up | `docker compose ps -a` |

Seed counts, fresh database seeded 2026-10-11 (history 2024-10-11 to 2026-10-11):

| Facility | Patients | Encounters | Vitals | Labs | Medication orders | Administrations |
|---|---|---|---|---|---|---|
| DEMO (Test Facility) | 1,234 | 8,259 | 298,729 | 179,203 | 9,380 | 70,675 |
| RGH (live, after 14-day backfill, about 12 min in) | 472 | 550 | 20,594 | 12,597 | 764 | 5,100 |
| RNC (live, outpatient only) | 343 | 357 | 2,045 | 1,059 | 0 | 0 |
| HMC (live, other tenant) | 295 | 350 | 12,516 | 7,669 | 479 | 2,419 |

```sql
SELECT f.code, (SELECT count(*) FROM patients p WHERE p.facility_id=f.id), ... FROM facilities f ORDER BY f.code;
```

DEMO encounters by type (discharged in brackets): outpatient 4,611 (4,610), inpatient 1,799
(1,783), emergency 1,739 (1,738), observation 110 (110). `ingest_events` after the backfill:
24,500 processed, none failed (`SELECT status, count(*) FROM ingest_events GROUP BY 1`).

The same seed on a volume created 2026-09-16 (the build agents' database, history 2024-09-16
to 2026-09-16) held DEMO 1,234 patients / 8,526 encounters / 308,458 vitals / 186,866 labs /
9,873 orders / 74,761 administrations (reported, not re-run). Patient count is stable; visit
and measurement counts depend on the seed day.

## 2. Tests and lint

Counts as of 2026-10-11; section 9 has the 2026-10-14 numbers (70 backend tests).

| Check | Result | Command |
|---|---|---|
| Backend tests | 47 passed, 0 failed, 0 skipped (23 platform + 13 `test_risk.py` + 7 `test_summary.py` + 4 `test_ingest.py`), after the review fix pass (section 8) | `docker compose exec backend pytest -q --disable-warnings` |
| Backend lint | All checks passed | `docker compose exec backend ruff check app tests` |
| Scripts lint | All checks passed | `.venv/Scripts/python -m ruff check --config backend/ruff.toml training evals scripts` |
| Frontend compile | built in 4.68 s, pre-existing chunk-size warning only (3.90 s before the RiskCard rewrite) | `docker compose exec frontend npx vite build` |
| Smoke, Compose | 14 checks ok, `smoke ok`, 3.6 s wall | `.venv/Scripts/python scripts/smoke.py` |

The smoke run after the fix pass scored DEMO patient 801 (probability 0.163, band medium,
model `readmission-20261011-870b90fb`; the first reference model gave 0.025 low for the same
patient) and got a summary in 3,004 ms for 1,262 input + 272 output tokens with 23 source rows.

## 3. The readmission model

### 3a. The committed set (training/data, training/runs/2026-10-11, models/)

Re-exported and retrained in the review fix pass on 2026-10-11 from the database seeded
2026-10-11 (the same database the tests, the golden set and the cluster use), with the
regularised pipeline described in section 8. The hashes in
`training/runs/2026-10-11/training_run.json` match the committed files:

```bash
sha256sum training/data/readmission.csv   # 870b90fba542... = data_sha256
sha256sum models/readmission.joblib       # ca33eaca550d... = artifact_sha256 (same file as training/runs/2026-10-11/readmission.joblib, which is gitignored)
```

| What | Value | Source |
|---|---|---|
| Export: data end, cutoff | ends 2026-10-11; discharges after 2026-09-11 excluded (30-day follow-up); 1.9 s | `export_training_set.py` output |
| Rows, positives, rate | 1,790 rows, 465 positives, 26.0%, 782 patients, 33 features | `train.log` |
| CSV size | 372,705 bytes (364 KB), plus `readmission.columns.json` | `ls -l training/data/readmission.csv` |
| Grouped split (by `patient_id`, test fraction 0.2, random_state 42) | train 1,418 rows (25.2% positive), test 372 (28.8%) | `training_run.json` |
| Boosting rounds | 112 (early stopping on a 20% validation slice of the training rows, patience 20) | `train.log` |
| Training-set metrics | AUROC 0.8289, average precision 0.6745, Brier 0.1366 | `training_run.json` `train_metrics` |
| Test metrics | AUROC 0.7596, average precision 0.6263, Brier 0.1627 (base-rate Brier 0.2049) | `training_run.json` `metrics` |
| Fit time, total | 0.2 s, 0.5 s | `train.log` |
| Artifact | 112,058 bytes (109 KB; the 300-round model was 1,107,442 bytes), `model_version readmission-20261011-870b90fb`, `trained_on local` | `ls -l models/readmission.joblib`, `training_run.json` |

Calibration, ten bins (bin, count, mean predicted, observed), from `training_run.json`:

| Bin | Count | Mean predicted | Observed |
|---|---|---|---|
| 0.0-0.1 | 41 | 0.082 | 0.098 |
| 0.1-0.2 | 133 | 0.147 | 0.135 |
| 0.2-0.3 | 88 | 0.242 | 0.318 |
| 0.3-0.4 | 49 | 0.341 | 0.306 |
| 0.4-0.5 | 16 | 0.442 | 0.500 |
| 0.5-0.6 | 15 | 0.545 | 0.533 |
| 0.6-0.7 | 14 | 0.655 | 0.786 |
| 0.7-0.8 | 8 | 0.758 | 1.000 |
| 0.8-0.9 | 7 | 0.849 | 0.857 |
| 0.9-1.0 | 1 | 0.922 | 1.000 |

The low band (below 0.15) now holds rows whose observed rate is below 0.15 (0.098 and 0.135),
which the first model did not manage (0.157). The high bins hold few rows (one in the last),
so their observed rates are noisy.

### 3b. The earlier runs, for comparison (not committed)

The first reference model (build agent, 2026-10-11, database seeded 2026-09-16, 300 unchecked
boosting rounds): 1,886 rows, 525 positives (27.8%), 813 patients; test AUROC 0.7675, average
precision 0.6368, Brier 0.1735; train AUROC 1.0000, Brier 0.0026 (measured by the reviewer on
the same split); calibration 0.0-0.1 bin 223 rows, mean predicted 0.032, observed 0.157;
artifact 1,107,442 bytes. The same pipeline on the current seed's export (the CSV committed
now): test AUROC 0.7283, AP 0.5696, Brier 0.1881, train AUROC 1.0000. The seed's 730-day
window moving by 25 days changed the eligible cohort by 96 stays, so students should export,
train and commit their own set from their own seed; the numbers here are a shape to compare
against, not a target to reproduce.

### 3c. Serving

| What | Observed | How |
|---|---|---|
| Model load | In the FastAPI lifespan at startup: `INFO atlas.ml model readmission-20261011-870b90fb loaded from /app/models/readmission.joblib` before `Application startup complete`, on Compose and in every api pod | `docker compose logs backend`, `kubectl -n atlas logs <api pod>` |
| First call after start (Compose) | no cold penalty any more: the load happened at startup (1.29 s on first request before the change) | `curl -w '%{time_total}'` |
| First and second call through the Ingress | 0.225 s, 0.234 s (1.70 s cold before the change; 0.21 s is the Docker Desktop port-mapping floor, section 6) | `curl -w '%{time_total}' localhost/api/patients/801/risk/readmission` |
| Response | `probability 0.163, band medium, model_version readmission-20261011-870b90fb, trained_on local, as_of 2025-12-22T14:55:57Z, window_closes_at 2026-01-21T14:55:57Z, outcome not_readmitted, readmitted_at null, encounter_id 4754`, 33 `features_used` | same |
| Other tenant | HTTP 404 `{"detail":"Patient not found"}` (Harborview clinician on patient 801), same body as a missing id | `test_risk.py` |
| Not applicable | HTTP 404 `{"detail":"not applicable: no discharged inpatient or observation encounter"}`; HTTP 404 `{"detail":"not applicable: patient currently admitted"}` when a later stay is open | `test_risk.py` |
| Outcome states on the DEMO seed (801 patients with a scorable latest stay) | 727 windows already closed, 78 came back inside the window, 16 currently admitted after the scored stay, 54 pending (open window, nothing yet) | SQL in section 8 |
| Wrong `MODEL_SHA256` | HTTP 503 `model not available`, `WARNING atlas.ml model file ... does not match MODEL_SHA256` | `test_risk.py` |
| Missing categorical | `None` and `float('nan')` both score (the first model crashed on NaN with `TypeError: ufunc 'isnan'`) | `test_risk.py` |
| Metering | `usage_events` rows: feature `readmission-risk`, status ok, predictions 1 | `SELECT feature, status, count(*) ... FROM usage_events GROUP BY 1,2` |

## 4. The stay summary and its evals

Model: `Qwen/Qwen3-235B-A22B-Instruct-2507` through Nebius Token Factory, temperature 0.2,
max 600 output tokens, 20 s timeout. The key lives only in `.env`.

| What | Observed | How |
|---|---|---|
| Single call, DEMO patient 1 (outpatient visit, 6 sources) | 200 in 2.81 s, 422 input + 137 output tokens (reported, not re-run) | `curl -X POST localhost:8000/api/patients/1/summary` |
| Single call from the smoke run (inpatient stay, 23 sources) | 4,090 ms, 1,262 + 286 tokens | `scripts/smoke.py` |
| Golden set | 10 DEMO patients whose latest encounter is a finished inpatient stay with labs and orders; 6 vitals, 20 labs (the cap), 2 to 6 medications each; `evals/golden.jsonl` 10 lines, 10,042 bytes; regenerated in 1 s | `.venv/Scripts/python evals/make_golden.py` |
| Eval run (final pass) | pass rate 10/10 = 100%; all three evaluators ok on every case (numbers grounded, no foreign medication, 5 to 6 sentences); 21,442 tokens; 1,706 to 1,935 input and 219 to 459 output per call; 2,388 to 4,776 ms per call; 36 s wall; exit 0 with `--threshold 0.7` | `.venv/Scripts/python evals/run_evals.py --threshold 0.7` |
| Eval run (build agent, old seed) | 10/10, 22,125 tokens, 2,417 to 4,975 ms per call, 34.8 s (reported, not re-run) | same |
| Refusal paths | `usage_events` holds `limited` rows (0 tokens, 0 ms), `error` rows (1 ms) and, since the fix pass, `unconfigured` rows (the 503 "no model configured" refusal) written by the tests, proving every refusal is metered | SQL above |
| Daily ceiling | sums every `usage_events` row of the tenant since midnight UTC, across features (it filtered on `feature = 'stay-summary'` before the fix pass) | `summary.tokens_used_today()` |

The golden set had to be regenerated in the final pass: the committed one named encounter ids
from the 2026-09-16 seed (patient 2, encounter 8362), and `run_evals.py` stops with
"regenerate the golden set" when the API summarises a different encounter. After any reseed,
run `make_golden.py` before `run_evals.py`. The regenerated set is the one committed now
(patients 2, 88, 208, 296, 390, 501, 648, 777, 917, 1057).

## 5. Images

`docker images`, uncompressed SIZE, tag `sha-c2e3970` (the short hash of the initial import;
the images were built from the working tree at the end of the reference build):

| Image | Size | Build time (fix pass rebuild, root `.dockerignore` in place) |
|---|---|---|
| `atlas-api` (backend/Dockerfile, includes `models/`) | 830 MB (832 MB with the 1.1 MB model; 798 MB before the `openai` pin was added) | 45 s (pip layer rebuilt) |
| `atlas-web` (frontend/Dockerfile.prod) | 75.1 MB | 7 s |
| `atlas-meridian` (feed/Dockerfile) | 236 MB | 15 s |

Verified inside the rebuilt `atlas-api:sha-c2e3970`: `/app/models/readmission.joblib`
(112,058 bytes, sha256 `ca33eaca550d...` = `artifact_sha256`), no `.env` in `/app`
(`docker run --rm atlas-api:sha-c2e3970 sh -c "ls -l /app/models; sha256sum /app/models/readmission.joblib"`).
The build context no longer carries `.venv` (439 MB on this machine), `.git`, `frontend/node_modules`,
`.env` or `k8s/secret.env`: `.dockerignore` at the repository root excludes them.

## 6. The Kubernetes cluster

Final pass: the cluster was deleted and recreated from scratch with the three final images,
while the Compose stack kept running on the same machine (ports do not clash: 3000/8000/8200/5433
against 80/443). Context `kind-atlas`, node `atlas-control-plane` on `kindest/node:v1.37.0`.

```bash
kind delete cluster --name atlas
kind create cluster --name atlas --config k8s/kind-config.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s
kind load docker-image atlas-api:$TAG atlas-web:$TAG atlas-meridian:$TAG --name atlas
kubectl apply -f k8s/base/namespace.yaml
kubectl -n atlas create secret generic atlas-secrets --from-env-file=k8s/secret.env
kubectl apply -k k8s/base
```

| Step | Observed (final pass) | Reported by the build agent (first build) |
|---|---|---|
| `kind delete cluster` | 1 s | |
| `kind create cluster` | 16 s (node image already pulled) | 35.9 s |
| ingress-nginx controller Ready | about 1 min (see the note below) | 24.6 s |
| `kind load` of three images | 30 s | 15.9 s + 3.5 s |
| Secret | `atlas-secrets` Opaque, DATA 4 | same |
| `kubectl apply -k k8s/base` to every Deployment available and the seed Job Complete | 103 s (poll every 5 s) | 84 s |
| Seed Job | `done in 94s`, same counts as the Compose seed (1,234 / 8,259 / 298,729 / 179,203 / 9,380 / 70,675); Job DURATION 99 s | 79 s |
| Pods | api x2, web x2, db, meridian, worker all 1/1 Running with 0 restarts; atlas-seed Completed | same, after the wait-for-db init container was added |
| PVCs | `pgdata` 2Gi and `meridian-data` 1Gi Bound, StorageClass `standard` | same |

Note on the ingress controller: the final pass ran `kubectl wait --for=condition=ready pod
--selector=...` before the controller pod existed, which exits at once with `error: no matching
resources found`; the first `kubectl apply -k k8s/base` then failed only on the Ingress
(`failed calling webhook "validate.nginx.ingress.kubernetes.io" ... connection refused`) and a
second apply a minute later created it. `k8s/README.md` now uses `rollout status`, which waits
properly, and documents the recovery.

Ladder of proof through the Ingress, all on http://localhost, observed in the final pass:

| Step | Command | Result |
|---|---|---|
| 1 | `kubectl -n atlas get pods` | all Running and Ready (table above) |
| 2 | `kubectl -n atlas get jobs` | `atlas-seed Complete 1/1 99s` |
| 3 | `curl -s localhost/health` | `{"status":"ok","database":"ok","migrations":1}` |
| 3 | `curl -s localhost/health/live` | `{"status":"alive"}` |
| 4 | `curl -s -o /dev/null -w '%{http_code}' localhost/api/facilities` | 401 |
| 4 | `curl -s -X POST localhost/api/auth/login ...` (clinician@riverside.example) | 200, access token (155 characters) |
| 4 | `GET /api/facilities` with the token | DEMO 1,234 patients, RNC 344, RGH 473 |
| 5 | `curl -s localhost/` | 200 text/html; `curl -s localhost/config.js` gives `window.__ATLAS_CONFIG__ = { apiUrl: "http://localhost" };`; `/patients/12` returns the SPA page (200 text/html) |
| 5 | `curl -s localhost/docs`, `localhost/openapi.json` | 200, 200; the schema lists `/api/patients/{patient_id}/risk/readmission` and `/api/patients/{patient_id}/summary` |
| 6 | `GET /api/integrations` with the token | RNC active, watermark 1,654; RGH active, watermark 14,577 (about two minutes after the seed) |
| 7 | `kubectl -n atlas logs deploy/worker --tail=6` | `applied batch of 500: {'processed': 500}`, `applied batch of 110`, then `applied batch of 1` as events arrive in real time |
| 7 | heartbeat file age inside the worker pod | 1 s |
| 7 | `ingest_events` in the cluster database | 24,513 processed, none failed; 2,346 patients across all facilities |
| AI | `GET /api/patients/801/risk/readmission` through the Ingress | 200 in 1.70 s cold (at that time the first call on a pod loaded the model; since the fix pass the lifespan loads it and the first call takes 0.225 s, section 3c), 0.24 s on the next call; `0.025 low readmission-20261011-6eb8cb95 local` with the first model, `0.163 medium readmission-20261011-870b90fb` with the committed one |
| AI | `POST /api/patients/801/summary` through the Ingress | 503 `{"detail":"no model configured"}`, because `k8s/secret.env` holds an empty `LLM_API_KEY` |
| Smoke | `.venv/Scripts/python scripts/smoke.py --base http://localhost` | `smoke ok`: 12 checks ok, 2 skips (Meridian health, no Ingress route; stay summary, no key), 28 s wall on the first run, 25 s warm with `--no-summary`; the same script takes 1 s on Compose |

Why the cluster smoke run is slow: every request through `http://localhost` costs about 0.21 s
on this machine whatever it asks for (`curl -w '%{time_total}'`: `/health` 0.216 s, `/`
served by nginx 0.213 s, `/api/facilities` 0.211 s, the risk route 0.229 s), against 0.011 to
0.040 s for the same calls on Compose. The floor is the Docker Desktop for Windows port
mapping between `localhost` and the kind node, not the pods; a student measuring latency
should measure inside the cluster (`kubectl exec` plus `curl http://api:8000/...`) or accept the
constant offset.

Rollback drill and the broken Deployment exercise: run and recorded by the build agent on the
first cluster (`set image deploy/api api=atlas-api:sha-bad` gives a third pod in ErrImagePull
then ImagePullBackOff while the two old pods keep serving; `rollout undo` restores
`atlas-api:sha-c2e3970`; `broken-api.yaml` gives CreateContainerConfigError, `couldn't find
key JWT_SECRETS in Secret atlas/atlas-secrets`). Reported, not re-run in the final pass; the
procedure is in `k8s/README.md` sections 7 and 8. `kubectl rollout status` without
`--timeout` waits for the Deployment's `progressDeadlineSeconds` (600), so the runbook now
passes `--timeout=30s`.

Redeploy in the review fix pass (same cluster, rebuilt images under the same tag, fresh
`JWT_SECRET` in the Secret, least-privilege env on worker and seed, `VITE_API_URL=""`):

| Step | Observed |
|---|---|
| `kind load` of the three rebuilt images | 20 s |
| `delete secret` + `create secret` | `atlas-secrets Opaque DATA 4` |
| `kubectl apply -k k8s/base` + `rollout restart` of api, worker, web, meridian, to all four rolled out | 23 s; the seed Job ran again and finished in 4 s (database already seeded) |
| api pod log | `model readmission-20261011-870b90fb loaded` before `Application startup complete`; no JWT warning (fresh secret) |
| `env` inside the worker pod (Secret-derived names) | `POSTGRES_PASSWORD`, `MERIDIAN_API_KEY`, `DATABASE_URL` only; seed pod: `POSTGRES_PASSWORD`, `DATABASE_URL`; api pod: all four keys |
| `curl -s localhost/config.js` | `window.__ATLAS_CONFIG__ = { apiUrl: "" };` (same origin) and the web app logs in through it |
| Ladder | `/health` ok with 1 migration, `/health/live` alive, `/api/facilities` 401 without a token, login token 155 characters, `/docs` and `/openapi.json` 200, `POST .../summary` 503 (empty `LLM_API_KEY` in the cluster Secret) |
| `scripts/smoke.py --base http://localhost` | `smoke ok`: 12 ok, 2 skips (Meridian health, stay summary), 27 s wall |
| Immutable Job | `kubectl kustomize k8s/base \| sed s/sha-c2e3970/sha-newtag/ \| kubectl apply --dry-run=server -f -` with the seed Job present: `The Job "atlas-seed" is invalid: spec.template: Invalid value: ... field is immutable`, which is why the runbook deletes the Job before re-applying with a new tag |

## 7. Tooling versions

kind v0.33.0 (go1.26.7 windows/amd64, installed with `winget install -e --id Kubernetes.kind`;
not on PATH in shells started before the install, see k8s/README.md), kubectl Client v1.34.1
with Kustomize v5.7.1, kind node image `kindest/node:v1.37.0`, ingress-nginx controller
1.15.1 (the runbook pins the manifest to `controller-v1.15.1`; the `main` branch URL served the
same version on 2026-10-11), Docker server 29.5.2, Python 3.12.3, numpy 2.0.2, pandas 2.2.3,
scikit-learn 1.6.1, joblib 1.4.2, psycopg 3.2.6, openai 3.28.0, Vite 6.1.1, React 18.3.1,
lucide-react 0.474.0, PostgreSQL 16 (alpine image).

## 8. The review fix pass (2026-10-11)

Measurements behind the changes made in response to the reference review.

Hyperparameters, all on the committed CSV and the same grouped split (train 1,418 / test 372),
`scratchpad hp_trial.py`:

| Pipeline | Rounds | Train AUROC / Brier | Test AUROC / AP / Brier | Low band (< 0.15): rows, observed |
|---|---|---|---|---|
| 300 rounds, lr 0.05, no early stopping (the first reference) | 300 | 1.0000 / 0.0027 | 0.7283 / 0.5696 / 0.1881 | 240, 0.188 |
| early stopping only (validation 0.2, patience 20) | 58 | 0.9422 / 0.0847 | 0.7513 / 0.6097 / 0.1650 | 159, 0.145 |
| early stopping + min_samples_leaf 40 | 59 | 0.9109 / 0.1098 | 0.7543 / 0.6318 / 0.1617 | 130, 0.154 |
| early stopping + leaf 40 + max_depth 3 (committed) | 112 | 0.8289 / 0.1366 | 0.7596 / 0.6263 / 0.1627 | 116, 0.095 |
| 60 rounds, depth 3, leaf 50, no early stopping | 60 | 0.8027 / 0.1443 | 0.7706 / 0.6421 / 0.1628 | 83, 0.084 |

`HistGradientBoostingClassifier(early_stopping='auto')` is off below 10,000 rows, which is why
the first run memorised the set. The committed choice keeps early stopping (so the lesson can
show it working) and accepts slightly lower test AUROC than the fixed-60-rounds variant in
exchange for a rule the student can explain.

Train/serve skew, `ml.encounter_features(facility_id=...)` on the reference database:

| Facility | History span | Eligible stays | `prior_admissions_365d` mean | Zeros |
|---|---|---|---|---|
| DEMO | 2024-10-11 to 2026-10-11 | 1,872 | 1.374 | 47.0% |
| RGH | 2026-09-27 to 2026-10-11 | 107 | 0.028 | 97.2% |
| HMC | 2026-09-27 to 2026-10-11 | 71 | 0.000 | 100% |
| RNC | 2026-09-27 to 2026-10-11 | 0 (outpatient only) | | |

Label definition on the DEMO seed (reviewer's count, 1,872 eligible stays): 483 positives when
an emergency visit counts as "came back" (rate 0.258) against 325 with inpatient and
observation only (0.174). The reference keeps emergency visits in the label and says so in
`ml.py`, `training/README.md`, the RiskCard footer, the README and SPEC row A.

Outcome states for the latest scorable stay, DEMO seed, data ending 2026-10-11:

```sql
WITH latest AS (SELECT DISTINCT ON (e.patient_id) e.patient_id, e.discharge_date FROM encounters e
  WHERE e.facility_id = (SELECT id FROM facilities WHERE code = 'DEMO') AND e.discharge_date IS NOT NULL
    AND e.admission_type IN ('inpatient', 'observation') AND coalesce(e.discharge_disposition, '') <> 'expired'
  ORDER BY e.patient_id, e.discharge_date DESC, e.id DESC)
SELECT count(*) FROM latest;                                          -- 801 scorable
-- window closed 727; came back inside the window 78; a later stay still open 16; pending 54
```

Other checks: `ml.predict_probability` with `discharge_disposition = float('nan')` raised
`TypeError: ufunc 'isnan' not supported` before the `frame_from_rows` fix and scores after it;
`kind` was absent from this session's PATH although `winget list` showed it installed, which is
the gotcha the runbook now explains; the pinned ingress-nginx URLs
(`controller-v1.15.1/deploy/static/provider/kind/deploy.yaml` and `.../cloud/deploy.yaml`)
answered HTTP 200.

## 9. The 2026-10-14 integration pass (early-warning model, document store, automated check)

Four build agents added the early-warning model (training and serving), the MongoDB document
store and a submission checker script on 2026-10-14 (removed from the repository on 2026-10-19, see
section 18; the rows below that name "the checker" record what it printed then); this pass integrated their work, re-ran
every check from a stopped Compose stack, recreated the kind cluster from scratch and recorded
the numbers below. Same machine as the earlier sections (Windows 11, Docker Desktop, 16 GB of
memory). Numbers marked "reported" come from the agents' reports and were not reproduced here;
everything else was observed in this pass. The Compose data volumes were kept (`docker compose
down`, not `down -v`), so the database is still the one seeded 2026-10-11.

```bash
docker compose down            # 3 s
docker compose up -d --build   # 22 s (image layers cached; backend healthy, worker started)
```

| Check | Result | Command |
|---|---|---|
| Backend startup log | `JWT_SECRET is the development default` warning, `model readmission-20261011-870b90fb loaded`, `early-warning model deterioration-20261014-0877aaca loaded from /app/models/deterioration.onnx in 61 ms (onnxruntime 1.31.0, CPU)`, `Application startup complete` | `docker compose logs backend` |
| `/health` | `{"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}` | `curl -s localhost:8000/health` |
| Backend tests | 69 passed in 7.87 s at the start of the pass (47 before + 8 `test_ai_store.py` + 14 `test_early_warning.py`); 70 passed in 8.18 s after the index fix of section 14 added one test, re-run after `docker compose up -d --build` | `docker compose exec -T backend pytest -p no:warnings` |
| Backend lint | All checks passed | `docker compose exec -T backend ruff check app tests` |
| Scripts lint | All checks passed | `.venv/Scripts/python -m ruff check --config backend/ruff.toml training evals scripts` |
| Frontend compile | built in 4.65 s (4.89 s on the re-run), the pre-existing chunk-size warning only | `docker compose exec -T frontend npx vite build` |
| Smoke, Compose | 14 checks ok, `smoke ok`; readmission patient 801 `0.163 medium`; summary 1,262 + 273 tokens in 5,060 ms with 23 sources | `.venv/Scripts/python scripts/smoke.py` |
| Automated check, Task 2 | 16 passed, 2 warnings (both runs `trained_on: local`, the cutline), 0 failed, exit 0, 18 s wall (includes `pytest -k "risk or early_warning"`: 28 passed in 7.78 s, and a fresh vite build scanned for the two secret values) | the checker, `--task 2` |
| Automated check, Task 3 | 11 passed, 0 warnings, 0 failed, exit 0, 111 s wall; summary call 565 tokens in 3,688 ms; `usage_events` 185 -> 186; evals 10/10 at threshold 0.7 for 21,710 tokens; the second API container at port 8001 answered `stored: false` with `ai_store unavailable` and 429 at `LLM_DAILY_TOKEN_LIMIT=1`; `pytest -k tenant` 10 passed in 1.39 s | the checker, `--task 3` |
| Automated check, Task 4 | see section 14 | the checker, `--task 4 --base http://localhost` |

Two small fixes were needed for the automated check to pass on the reference, both made in this
pass: `k8s/base/worker.yaml` gained a `readinessProbe` (the same heartbeat exec as its liveness
probe; spec section 13 says every Deployment has both, and the checker follows it) and
`docs/REPORT.md` was added with the rollback-drill transcript from section 14 (the checker
requires the markers `rollout undo` and `ImagePullBackOff`). `k8s/base/kustomization.yaml` and
`k8s/exercises/broken-api.yaml` now carry the tag `sha-d5956e4`, the hash of the commit the images
were built from; the commit that adds this work has a different hash, which is the documented
"the committed `newTag` never matches your fork" situation.

`usage_events` after this pass (Compose database, all test runs included): `early-warning ok 44`,
`readmission-risk ok 159`, `stay-summary ok 74 / limited 21 / error 18 / unconfigured 14`
(`SELECT feature, status, count(*) FROM usage_events GROUP BY 1,2`).

## 10. The early-warning model (deterioration within 12 hours)

Trained by the GPU build agent on 2026-10-14 on this laptop's NVIDIA GeForce RTX 4090 (24,564 MiB
per `nvidia-smi`, driver 610.60), Windows 11, Python 3.12.7, torch 2.6.0+cu124, numpy 2.5.3,
pandas 2.3.3, scikit-learn 1.9.1, onnx 1.23.2 (the CUDA venv under `carescribe/.venv`, not the
backend venv). These are laptop numbers. Nothing has been run on the lab's `g4dn.xlarge` (one T4,
4 vCPUs): the T4 epoch time, the total run time and the 15-minute training budget on student pages
are estimates until a live session confirms them; a T4 is slower than a 4090 and 4 vCPUs are slower
than this machine's 24 threads, so the CPU-versus-GPU ratio there is unknown. The run record is
`training/runs/2026-10-14-deterioration/training_run.json` and `train.log`; `training/data/`
holds the export. The automated check (section 9) verified the hashes below.

### 10a. Export (`.venv/Scripts/python training/export_vitals_hourly.py`, 3.5 s)

| What | Value |
|---|---|
| Encounters | 1,783 discharged inpatient stays at DEMO, 791 patients; 16 open stays excluded (their length is not final); stays ending in death kept (the readmission export drops them; documented in both) |
| Onset encounters | 193 (10.8%) by the reference rule: first hour at or after hour 12 with at least two of HR > 120, RR > 24, SpO2 < 90, SBP < 90, temperature > 38.5 |
| Encounter-hours | 191,341 rows, 39,863 (20.8%) with at least one measurement; 232,118 vitals rows read |
| Files | `vitals_hourly.csv` 3,858,287 bytes (3.68 MB), `encounters_static.csv` 182,616 bytes, `deterioration.columns.json` 1,488 bytes; 3.86 MB together, far under the 20 MB console-upload limit |
| `data_sha256` | `0877aaca0e20...` (`sha256sum training/data/vitals_hourly.csv`) |

### 10b. Reference training run

```bash
<carescribe venv python> training/train_deterioration.py --stride 1 --device cuda --time-one-epoch --out training/runs/2026-10-14-deterioration
cp training/runs/2026-10-14-deterioration/deterioration.{onnx,json} models/
.venv/Scripts/python training/check_onnx.py training/runs/2026-10-14-deterioration
```

| What | Value | Source |
|---|---|---|
| Windows at stride 1 | 145,989, of which 2,299 positive (prevalence 1.57%), built in 1.5 s (2.1 s in another run) | `train.log` |
| Grouped split (by `patient_id`, test 0.2, validation 0.15 of train, seed 42) | train 101,464 windows / 537 patients; validation 17,325 / 95; test 27,200 / 159 (39 onset stays in test) | `training_run.json` `split` |
| `pos_weight` | 64.9 (negatives per positive in the training windows) | `train.log` |
| Epochs | 21 run, best epoch 15 (validation AP 0.8772), stopped by patience 6; fit 23.6 s | `train.log` |
| Test metrics | AUROC 0.9896, average precision 0.8936, Brier 0.0200 (base-rate Brier 0.0169) | `metrics` |
| Training-set metrics | AUROC 0.9985, average precision 0.945, Brier 0.0213 | `train_metrics` |
| One epoch, same data and batch 512 | cpu 5.63 s in the committed run (8.09 s, 10.25 s and 11.3 s in three earlier runs of the same command; 24 threads), cuda 0.6 s (0.65 s, 0.71 s and 0.86 s earlier) | `epoch_seconds`, `--time-one-epoch` |
| Whole run | 38.1 s wall (37.8 s in the earlier run) | `train.log` |
| Alerts at the 0.20 threshold (test windows) | 1,810 alerts over 1,133.3 patient-days = 159.71 per 100 patient-days; 39 of 39 deteriorations caught by an alert inside the 12-hour horizon (encounter-level sensitivity 1.0), median lead time 11.0 h; counting any earlier alert of the stay as well (`caught_encounters_any_hour`, the definition the first run used) still 39 of 39 with a median lead of 15.0 h; window recall 0.94, window precision 0.2425 | `alert_detail` |
| Artifact | `deterioration.onnx` 71,413 bytes (70 KB), opset 17, inputs `sequence` [batch, 24, 12] and `static` [batch, 4], one output `probability` [batch] (sigmoid inside the graph); `deterioration.json` 2,881 bytes | `ls -l models/` |
| Hashes | onnx sha256 `5872e65386e45bacd355df8bb2b601158167753d9ca7d26f3f971cbf12fc476c` = `artifact_sha256`; identical across three runs of the same command (seed 42 makes the training deterministic) | `sha256sum models/deterioration.onnx` |
| ONNX against torch | onnxruntime 1.31.0 reproduces the torch probabilities on ten saved test windows spanning the probability range within 2.88e-05 (tolerance 1e-4) | `training/check_onnx.py` |
| `model_version`, `trained_on` | `deterioration-20261014-0877aaca`, `local` | `training_run.json` |

Calibration, ten bins (bin, count, mean predicted, observed), from `training_run.json`:

| Bin | Count | Mean predicted | Observed |
|---|---|---|---|
| 0.0-0.1 | 24,926 | 0.003 | 0.001 |
| 0.1-0.2 | 464 | 0.145 | 0.006 |
| 0.2-0.3 | 278 | 0.245 | 0.011 |
| 0.3-0.4 | 199 | 0.343 | 0.015 |
| 0.4-0.5 | 160 | 0.452 | 0.019 |
| 0.5-0.6 | 181 | 0.553 | 0.028 |
| 0.6-0.7 | 166 | 0.644 | 0.018 |
| 0.7-0.8 | 133 | 0.748 | 0.007 |
| 0.8-0.9 | 110 | 0.843 | 0.054 |
| 0.9-1.0 | 583 | 0.987 | 0.712 |

The ranking is good and the calibration is not: `pos_weight` of about 65 inflates every
probability, so the observed rate sits far below the predicted one in every bin but the first, and
the alert rate at the spec's 0.20 threshold is high. The lesson and REPORT.md should say so and
use the threshold sweep the run record carries (`alert_table`, all on the test windows). "Caught"
means an alert inside the 12-hour horizon the model was trained for (a positive window), so the
lead time is at most 11 hours; the last column keeps the looser count the first run reported (any
alert earlier in the stay), which a random scorer at the same alert rate also does well on. All 39
of 39 test deteriorations are caught at every threshold by both definitions, so the sensitivity
column does not separate the thresholds; the alert rate and the window precision do:

| Threshold | Alerts per 100 patient-days | Caught (in horizon) | Median lead time (h) | Window precision | Median lead, any earlier alert (h) |
|---|---|---|---|---|---|
| 0.20 | 159.71 | 39/39 | 11 | 0.2425 | 15 |
| 0.35 | 124.68 | 39/39 | 11 | 0.3071 | 14 |
| 0.50 | 103.50 | 39/39 | 11 | 0.3666 | 13 |
| 0.70 | 72.88 | 39/39 | 11 | 0.5109 | 13 |
| 0.80 | 61.15 | 39/39 | 11 | 0.6075 | 13 |
| 0.90 | 51.44 | 39/39 | 11 | 0.7118 | 11 |
| 0.95 | 46.41 | 39/39 | 11 | 0.7719 | 11 |

The 11-hour median at every threshold says the model alerts in the very first window of the
horizon (onset minus 11 hours) for most of the 39 stays. The run record and `train.log` in
`training/runs/2026-10-14-deterioration/` are from the rerun of 2026-10-14 12:10 UTC with the
corrected `alert_metrics()`; seed 42 reproduced the identical `.onnx` (same sha256, same
`model_version`), so only `trained_at`, the epoch timings, the lead-time fields and
`artifact_json_sha256` differ from the first run, and `models/deterioration.json` was recopied.

### 10c. Other runs (reported by the GPU agent, not committed)

| Run | Result |
|---|---|
| Rehearsal `--stride 4 --epochs 3 --device cuda` | 37,194 windows, 577 positive (1.55%); test AUROC 0.9888, AP 0.8829, Brier 0.0236; 61.32 alerts per 100 patient-days at 0.20; about 0.2 s per cuda epoch; 6.2 s wall |
| Mask ablation `--stride 1 --device cuda --no-mask` | test AUROC 0.9878, AP 0.8709, Brier 0.0151; best epoch 1 of 7; 157.32 alerts per 100 patient-days at 0.20. The mask channel (nursing cadence) adds about 0.02 average precision |

`training/requirements-gpu.txt` pins torch 2.6.0, numpy 2.0.2, pandas 2.2.3, scikit-learn 1.6.1
and onnx 1.23.2 (the backend's pins plus torch and onnx); those five were not installed together
in one fresh environment, and the reference run used the newer numpy, pandas and scikit-learn
listed above. EC2 detection (IMDSv2 token, then the instance id) is copied from `train.py` and
has not been exercised on a live instance.

## 11. The document store

Added by the store build agent on 2026-10-14; versions and counts observed in this pass.

| What | Value | How |
|---|---|---|
| Server, driver | MongoDB 7.0.45 (`mongo:7` image), pymongo 4.18.3 | `docker compose exec -T mongo mongosh --quiet -u atlas -p ... --authenticationDatabase admin --eval "db.version()"`; `python -c "import pymongo; print(pymongo.version)"` in the backend container |
| Collections | `predictions`, `generations`, `agent_runs`, `eval_runs` in `atlas_ai` | `db.getCollectionNames()` |
| Indexes | `tenant_patient_created` `{tenant_id: 1, patient_id: 1, created_at: -1}` on `predictions`, `generations`, `agent_runs`; `tenant_created` on `eval_runs`; `created_at_ttl` on `predictions` with `expireAfterSeconds` 7,776,000 (90 days) | `db.predictions.getIndexes()` |
| Documents on the Compose store after this pass | 117 predictions, 50 generations, 0 eval runs (the automated check runs `run_evals.py --no-store`; a run with `MONGO_URL` set writes one) | `countDocuments()` |
| Tests | 8 in `backend/tests/test_ai_store.py`; the store-down test alone takes about 5.5 s (the 2,000 ms server-selection timeout, twice, plus the 0.5 s health ping) | `pytest -p no:warnings` |
| Container memory, Compose (no wiredTiger cap) | 331.4 MiB right after the test and check runs (330.3 MiB reported by the store agent after its test run, 185.9 MiB idle a few minutes later) | `docker stats --no-stream` |
| Timeouts | `serverSelectionTimeoutMS` 2000 on the client; `/health` wraps its ping in `pymongo.timeout(0.5)`; the api probes carry `timeoutSeconds: 3` so a store outage cannot fail readiness | `backend/app/ai_store.py`, `k8s/base/api.yaml` |
| Host port | 27018 (`mongodb://atlas:<password>@localhost:27018/atlas_ai?authSource=admin`) | `docker-compose.yml` |
| `/health` | `{"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}`; `ai_store: unavailable` with `status` still `ok` when the store is unreachable (observed through the checker's second API container) | `curl -s localhost:8000/health` |
| Frontend | `npx vite build` 5.49 s right after the store change (reported), 4.65 s in this pass; index JS 785.87 kB (gzip 220.40 kB), CSS 42.23 kB | `docker compose exec -T frontend npx vite build` |

The `mongosh` login needs `--authenticationDatabase admin` (or `authSource=admin` in the URL):
without it `mongosh -u atlas -p ... atlas_ai` answers `Authentication failed`, because the root
user the image creates on first start lives in `admin`.

## 12. Serving the early-warning model

Observed by the serving build agent on 2026-10-14 on the Compose backend (uvicorn with `--reload`,
`./backend` mounted) and re-checked in this pass where marked.

| What | Observed | How |
|---|---|---|
| Startup with both models | lifespan 0.93 s: `Waiting for application startup` 11:08:18.669Z, readmission model loaded 11:08:19.495Z (includes opening the pool), early-warning model loaded 11:08:19.572Z, `Application startup complete` 11:08:19.602Z (reported); early-warning load logged at 76 ms then, 61 ms in this pass; `ensure_indexes` about 30 ms | `docker compose logs --timestamps backend` |
| One request end to end, Compose | 9 to 16 ms (seven curl calls, reported); 10.1, 10.5 and 10.4 ms for patients 916, 343 and 135 in this pass | `curl -w '%{time_total}' localhost:8000/api/patients/<id>/risk/deterioration` |
| `assess()` in process | 11.7 ms first call, 0.3 to 7.3 ms warm over 16 stays (host venv, reported) | scratch script, deleted |
| Open DEMO inpatients at the time (16 stays; DEMO vitals frozen at 2026-10-11 17:52Z, wall clock 2026-10-14) | 15 `scored`, 1 `deteriorating` (encounter 8149, patient 916, onset at hour 46 of the stay, which is now at hour 191, `as_of` 2026-10-05T17:40Z), 0 `insufficient_data`; probabilities min 0.000, median 0.000, max 0.981; 13 of 15 round to 0.006 or less; two alerts at 0.20 (patient 991 at 0.262, patient 135 at 0.981); `hours_of_vitals` 4 to 87; youngest scored stay 13 hours (reported) | scratch script against `localhost:5433` and `models/deterioration.onnx` |
| Live RGH spot checks (reported) | hour 4 with 1 vitals hour and hour 9 with 3 vitals hours: `insufficient_data`; hours 209 and 337: `scored` 0.0 low | curl as `clinician@riverside.example` |
| Other tenant | 404 `{"detail":"Patient not found"}` for a Harborview patient asked as Riverside, the same body as a missing id | `test_early_warning.py`, checker Task 2 |
| Not applicable | 404 `{"detail":"not applicable: no open inpatient encounter"}` for a discharged patient | `test_early_warning.py` |
| Model missing or wrong `MODEL_DETERIORATION_SHA256` | 503 `{"detail":"model not available"}` | `test_early_warning.py` |
| History | `GET /api/patients/916/ai/history?feature=early-warning` lists the `deteriorating` documents newest first with `stored: true` (this pass) | curl |
| Metering | `usage_events` feature `early-warning`, `predictions` 1 only when a probability was produced (0 for `insufficient_data` and `deteriorating`) | `test_early_warning.py` |
| Image | `atlas-api` 944 MB with onnxruntime against 830 MB before (+114 MB: the onnxruntime 1.31.0 wheel is 23.76 MB, the installed package 68 MB plus coloredlogs, flatbuffers, protobuf and sympy); no torch in the image (`importlib.util.find_spec('torch')` is None); `/app/models` holds `deterioration.onnx` 71,413 B, `deterioration.json` 2,881 B and `readmission.joblib` | `docker images`, `docker run --rm atlas-api:sha-d5956e4 sh -c 'ls -la /app/models'` |

Design facts a lesson will need: the as-of hour is the hour after the stay's latest vitals
measurement (capped at the wall clock), not the wall clock itself, because the Test Facility is
frozen on its seed day and the feed may run faster than real time; `hours_admitted` is the as-of
hour index; `hours_of_vitals` counts stay hours with at least one measurement;
`insufficient_data` means the as-of hour is below 12 or there are no vitals at all; the band uses
the readmission thresholds (`ml.band`) and `alert` the json's 0.20; the backend repeats the grid,
window, forward-fill, standardisation and onset code from `training/deterioration.py` (the image
has no `training/`) and a test checks the serving constants against `models/deterioration.json`.

## 13. Images after the change

`docker images`, uncompressed SIZE, tag `sha-d5956e4` (the short hash of the commit before this
one; built in this pass from the working tree with every builder's change in it):

| Image | Size | Previous (`sha-c2e3970`) | Build time in this pass |
|---|---|---|---|
| `atlas-api` (backend/Dockerfile, includes `models/` with both artifacts) | 944 MB | 830 MB | 2 s (every layer cached from the Compose build minutes earlier; the serving agent's rebuild with the new pip layer was the real one) |
| `atlas-web` (frontend/Dockerfile.prod) | 75.2 MB | 75.1 MB | 8 s |
| `atlas-meridian` (feed/Dockerfile) | 236 MB | 236 MB | 1 s |

## 14. The Kubernetes cluster with the document store (2026-10-14)

The cluster was deleted and recreated from scratch in this pass with the three `sha-d5956e4` images,
while the Compose stack kept running on the same machine. Context `kind-atlas`, node
`atlas-control-plane` on `kindest/node:v1.37.0`, ingress-nginx from the pinned
`controller-v1.15.1` kind manifest, `k8s/secret.env` generated from `secret.example.env` with a
fresh `JWT_SECRET` (`openssl rand -hex 32`) and an empty `LLM_API_KEY`.

```bash
kind delete cluster --name atlas
kind create cluster --name atlas --config k8s/kind-config.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s
kind load docker-image atlas-api:$TAG atlas-web:$TAG atlas-meridian:$TAG --name atlas
kubectl apply -f k8s/base/namespace.yaml
kubectl -n atlas create secret generic atlas-secrets --from-env-file=k8s/secret.env
kubectl apply -k k8s/base
```

| Step | Observed | Earlier passes (section 6) |
|---|---|---|
| `kind delete cluster` | 3 s | 1 s |
| `kind create cluster` | 17 s (node image already pulled) | 16 s, 35.9 s |
| ingress-nginx controller rolled out | 39 s (`rollout status` waited properly this time) | about 1 min, 24.6 s |
| `kind load` of three images (944 + 75 + 236 MB) | 23 s; a later reload of the rebuilt `atlas-api` alone 14 s | 30 s, 20 s |
| Secret | `atlas-secrets Opaque DATA 6` | DATA 4 |
| `kubectl apply -k k8s/base` | 17 objects: namespace unchanged, configmap, 5 services (api, db, meridian, mongo, web), 2 PVCs, 5 Deployments, StatefulSet mongo, Job atlas-seed, Ingress atlas; applied first time without the webhook error | |
| `apply` to every pod Ready and the seed Job Complete (poll every 5 s) | 101 s | 103 s, 84 s |
| Seed Job | `done in 95s`, Job DURATION 100 s; seeded 2026-10-14 (history 2024-10-14 to 2026-10-14): 1,234 patients, 8,454 encounters, 327,538 vitals, 197,340 labs, 9,969 orders, 77,938 administrations (the Compose database is still the 2026-10-11 seed, section 1, which is why its counts differ) | `done in 94s` |
| Pods | api x2, web x2, db, meridian, worker, `mongo-0` all 1/1 Running with 0 restarts; atlas-seed Completed 0/1 | no mongo-0 |
| PVCs | `pgdata` 2Gi, `meridian-data` 1Gi, `mongo-data-mongo-0` 1Gi, all Bound, StorageClass `standard` | two PVCs |
| `rollout restart deploy/api deploy/worker` after reloading the rebuilt api image | both rolled out in 33 s | 23 s for four |
| Node container memory | 2.571 GiB after everything was Ready, 2.686 GiB after the smoke run (`docker stats --no-stream`, `atlas-control-plane`); `kubectl -n atlas top pod` answers `Metrics API not available` (no metrics-server, as before) | |
| `ingest_events` in the cluster database a minute after the seed | 24,320 processed, none failed | 24,513 |

One finding from this pass, fixed in it: the api Pods start before `mongo-0` answers (the api's init
container waits for Postgres only), so `ensure_indexes()` in the lifespan failed on both api Pods
(`WARNING atlas.ai_store ai_store indexes not created, the store is unavailable
(ServerSelectionTimeoutError)` at 11:41:06, three seconds after the models loaded) and the first
writes created the `predictions` collection with only its `_id_` index: no TTL, no compound index.
`ai_store.py` now remembers whether the indexes were created and the first successful write creates
them when the startup attempt failed (`create_index` is idempotent); `test_ai_store.py` gained a
test for it (70 backend tests now). After the api image was rebuilt, reloaded and restarted,
`mongo-0` showed `_id_`, `tenant_patient_created` and `created_at_ttl ttl=7776000` on
`predictions`. The gotcha is written down in `k8s/README.md` and `docs/AI_STORE.md`.

Ladder of proof through the Ingress, all on http://localhost:

| Step | Command | Result |
|---|---|---|
| 1 | `kubectl -n atlas get pods`, `get pvc` | table above; `mongo-0` present |
| 2 | `kubectl -n atlas get jobs` | `atlas-seed Complete 1/1 100s` |
| 3 | `curl -s localhost/health` | `{"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}` in 0.212 s |
| 3 | `curl -s localhost/health/live` | `{"status":"alive"}` in 0.207 s |
| 4 | `GET /api/facilities` without a token | 401 |
| 4 | `POST /api/auth/login` (clinician@riverside.example) | 200, token of 155 characters |
| 4 | `GET /api/facilities` with the token | DEMO 1,234 patients, RNC 336, RGH 471 |
| 5 | `curl -s localhost/`, `localhost/config.js` | 200 text/html; `window.__ATLAS_CONFIG__ = { apiUrl: "" };` (same origin) |
| 5 | `localhost/docs`, `localhost/openapi.json` | 200; the schema lists `/api/patients/{patient_id}/risk/readmission`, `.../risk/deterioration`, `.../summary` and `.../ai/history` |
| 6 | `GET /api/integrations` as admin@riverside.example | RNC active, watermark 1,650, 0 deferred, 0 failed; RGH active |
| 7 | `kubectl -n atlas logs deploy/worker --tail=5` | `applied batch of 500: {'processed': 497, 'deferred': 3}`, `applied batch of 398`, then batches of 7, 5 and 14 as events arrive |
| 7 | env names inside the worker pod | `POSTGRES_PASSWORD`, `MERIDIAN_API_KEY`, `DATABASE_URL` and no `JWT_SECRET`, `LLM_API_KEY`, `MONGO_USER`, `MONGO_PASSWORD` or `MONGO_URL` (least privilege, three names; this pass first gave the worker the three `MONGO_*` names too, removed in the review fix pass of section 16 because the worker never opens the store) |
| AI | api pod log | `model readmission-20261011-870b90fb loaded`, `early-warning model deterioration-20261014-0877aaca loaded from /app/models/deterioration.onnx in 292 ms` on the first start (180 ms after the restart; 61 to 76 ms on Compose: the pod has a 500m CPU limit), no JWT warning (fresh secret) |
| AI | `GET /api/patients/{199,35,403}/risk/deterioration` (the three oldest open DEMO inpatient stays in the cluster database) | 200 `scored` 0.0 low (hour 145, 22 hours of vitals) in 0.252 s; 0.029 low (hour 113, 18) in 0.235 s; 0.0 low (hour 89, 15) in 0.220 s; every answer `stored: true`, `as_of` on 2026-10-14 because this database was seeded today |
| AI | `GET /api/patients/199/ai/history?feature=early-warning&limit=5` | `{items: [the score just written, state scored], stored: true}` |
| AI | `GET /api/patients/801/risk/readmission` | 200, `0.163 medium readmission-20261011-870b90fb` (same patient and score as on Compose: the seed is deterministic up to the shift) |
| AI | `POST /api/patients/801/summary` | 503 `{"detail":"no model configured"}` (empty `LLM_API_KEY` in the cluster Secret) |
| Tenant | the three calls above as clinician@harborview.example | 404 `{"detail":"Patient not found"}` for the risk and the history route |
| Store | `mongosh` inside `mongo-0` | 4 predictions and 0 generations after the ladder; indexes as above after the fix |
| Smoke | `.venv/Scripts/python scripts/smoke.py --base http://localhost` | `smoke ok`: 12 ok, 2 skips (Meridian health, no Ingress route; stay summary, no key); readmission patient 167 `0.379 high`; 25 s wall (the 0.21 s per request port-mapping floor from section 6 still holds) |
| Check | the checker, `--task 4 --base http://localhost` | 15 passed, 0 warnings, 0 failed, exit 0, 26 s wall (11 manifest files, 17 kustomize objects, 9 pods Ready with mongo-0, smoke through the Ingress, `docs/REPORT.md` markers) |

Rollback drill, run twice in this pass (transcript in `docs/REPORT.md`): `set image deploy/api
api=atlas-api:sha-bad` adds a third api pod that goes `PodInitializing` (the init container still
uses the good tag), `ErrImagePull` at 38 s and `ImagePullBackOff` at about 40 s, while the two old
pods keep serving and `/health` keeps answering; `rollout status --timeout=30s` ends with
`error: timed out waiting for the condition`; `rollout undo` returns the image to
`atlas-api:sha-d5956e4` and the bad pod is terminated within a few seconds. The broken Deployment
exercise was not re-run.

## 15. Tooling versions added in this pass

pymongo 4.18.3, MongoDB 7.0.45 (`mongo:7`), onnxruntime 1.31.0 (CPU wheel, in the api image),
onnx 1.23.2 and torch 2.6.0+cu124 (training only, in the CUDA venv; the api image has no torch),
ruff 0.9.6 (the backend container's version, also installed into `.venv` to lint `training/`).
kind, kubectl, kustomize, node image, ingress-nginx and Docker are the versions in section 7;
the pinned `controller-v1.15.1` manifest URL applied cleanly in this pass.

## 16. The review fix pass (2026-10-14, after commit 5b1b962)

Twelve findings from the review of commit 5b1b962 (two of them reported twice under different
lenses), each with what was done. No blockers; one major.

| # | Finding (file) | Severity | Disposition |
|---|---|---|---|
| 1, 11 | Worker Deployment receives `MONGO_USER`, `MONGO_PASSWORD` and `MONGO_URL` it never uses; spec 6 fixes the worker to `POSTGRES_PASSWORD` and `MERIDIAN_API_KEY` (`k8s/base/worker.yaml`) | minor | Fixed. The three `env` entries and the sentence in the manifest's comment are gone; `docs/AI_STORE.md` (settings table) and section 14 row 7 above say so. The Compose worker still receives `MONGO_URL` from `docker-compose.yml`, as spec 12 says; only the cluster manifest is the least-privilege example. Live cluster after re-apply: `kubectl -n atlas get deploy worker -o jsonpath` lists `POSTGRES_PASSWORD MERIDIAN_API_KEY DATABASE_URL` and nothing else. |
| 2 | `test_indexes_are_created_on_the_first_write...` drops the live `predictions` indexes (TTL included) on the shared Compose store (`backend/tests/test_ai_store.py`) | minor | Fixed. A `scratch_store` fixture swaps `ai_store.db()` for the database `atlas_ai_test` on the same server (settings are a frozen dataclass, so the module function is patched, as `store_down` patches `_mongo_url`), the test drops indexes and inserts there, and the fixture drops the database afterwards. `atlas_ai` is never touched. |
| 3 | Encounter-level sensitivity and lead time count alerts anywhere in the stay, so 39/39 is nearly model-independent (`training/train_deterioration.py`) | minor | Fixed. `alert_metrics()` now counts a deterioration as caught only when an alert falls in a positive window (onset minus as-of under 12 hours) and takes the lead time from the first such alert; `caught_encounters_any_hour` and `median_lead_time_hours_any_hour` keep the old definition for comparison. Retrained with the same command (section 10b): identical `.onnx` (sha256 `5872e653...`, same `model_version`), median lead 11.0 h instead of 15.0 h at 0.20, still 39/39 at every threshold by both definitions; `training_run.json`, `train.log`, `deterioration.json` and `models/deterioration.json` replaced. Section 10b and the README gap say that the sensitivity column does not separate thresholds. |
| 4, 12 | `band` from the rounded probability, `alert` from the unrounded one (`backend/app/routers/early_warning.py`) | minor | Fixed. `assess()` rounds to three decimals once and decides `alert` on the rounded value; the router no longer rounds. New test `test_scored_probability_is_rounded_before_the_alert_decision`. |
| 5 | `hours_of_vitals` can exceed `hours_admitted` in the first hour of a stay (`backend/app/ml_deterioration.py`) | minor | Fixed. `hourly_grid()` accepts zero hours and returns an empty grid, so a stay younger than one hour reports `hours_admitted 0, hours_of_vitals 0`. New test `test_first_hour_of_a_stay_counts_no_vitals_hour`. |
| 6 | Scored card calls a class-weighted score a "chance" and flags low confidence only for the medium band (`frontend/src/panels/EarlyWarningCard.jsx`) | minor | Fixed. The sentence now reads "Model score for ... The score ranks patients; it is not the observed chance", and the low-confidence note shows for every scored probability under `CALIBRATED_FROM = 0.9` (the only bin whose observed rate, 0.712, came close to its mean prediction) and quotes the under-6-percent observed rates. The README gap bullet says the same. |
| 7 | Task 4 probe check reads commented-out YAML as a real probe (the checker) | major | Fixed. `strip_comments()` removes comment lines before every text search (probes, `:latest`), the probe regex wants an indented key, and when `kubectl kustomize` renders, the Deployments are read from the rendered output ("kustomize output" in the detail) instead of the source files. Verified on a scratch copy with the api `readinessProbe` block commented out: kustomize renders 17 objects, `FAIL Deployment api has readiness and liveness probes (kustomize output: missing readiness)`; the earlier text search passed it. |
| 8 | Artifact-hash check lets one artifact vouch for two runs and ignores `artifact_file` | minor | Fixed. `artifact_for()` hashes `models/<artifact_file>` (bare name only), falls back to the `.onnx` for `deterioration_12h` or to a hash match among `.joblib`/`.pkl`/`.pt` for the student's model, and a second run resolving to the same file fails ("is already the artifact of ..."). The `trained_on: ec2` PASS now says the claim is checked from the screenshots, not here. Verified: a scratch repo whose GPU run points at `readmission.joblib` gets `FAIL ... models/readmission.joblib is already the artifact of training/runs/2026-10-11/training_run.json`. `scripts/README.md` updated. |
| 9 | Task 3 never requires `stored: true` on a healthy store | minor | Fixed. `check_history()` also requires `stored: true` on the score it just fetched (detail shows both flags), and the two Task 2 route checks print `stored`. |
| 10 | Any git failure other than "untracked" counted as "secret.env absent from git" | minor | Fixed. Exit 1 is "not tracked" (PASS), 0 is "tracked" (FAIL), anything else is a WARN with git's last line. Verified: a scratch folder without `.git` now prints `WARN k8s/secret.env is not committed (git exited 128: fatal: not a git repository ...)`. |

Measurements in this pass:

| What | Result |
|---|---|
| `docker compose up -d --build backend worker` after the backend change | backend healthy, worker started; `docker compose exec -T backend pytest -q` 72 passed (70 before plus the two new early-warning tests), exit 0; `ruff check app tests` clean; `ruff check --config backend/ruff.toml training evals scripts` clean |
| `docker compose exec -T frontend npx vite build` | built in 4.67 s, `index-*.js` 792.78 kB (unchanged size class) |
| Retraining (`carescribe` venv, RTX 4090, same command as 10b) | 38.1 s wall, fit 25.0 s, 21 epochs, best 15 (validation AP 0.8772), one epoch cpu 5.63 s / cuda 0.6 s; `check_onnx.py` max gap 2.88e-05 OK |
| checker `--task 2` (Compose) | 16 passed, 2 warnings (`trained_on: local` twice), 0 failed |
| checker `--task 3` (Compose, one real summary: 420+147 tokens, 2,874 ms; evals 10/10, 21,635 tokens) | 11 passed, 0 warnings, 0 failed |
| Images | `atlas-api`, `atlas-web`, `atlas-meridian` rebuilt as `sha-5b1b962` (the commit under review; `kustomization.yaml` `newTag` set to it) and loaded into kind |
| `kubectl -n atlas delete job atlas-seed; kubectl apply -k k8s/base` | api, web, worker, meridian re-rolled, StatefulSet `configured` without a restart (`mongo-0` 37 min old afterwards), seed Job re-created and `Completed`; `rollout status` for the four Deployments all succeeded within about 30 s; the worker logged `Meridian unreachable` for a few seconds while meridian's new pod started, then resumed |
| `scripts/smoke.py --base http://localhost` | `smoke ok` (stay summary skipped: no key in the cluster Secret) |
| checker `--task 4 --base http://localhost` | 15 passed, 0 warnings, 0 failed; the probe lines now read `(kustomize output)` |
| `GET /api/patients/199/risk/deterioration` through the Ingress | 200 `scored` 0.0 low, hour 145, 22 hours of vitals, `trained_at 2026-10-14T12:10:45Z` (the rerun's card), `stored: true` |

## 17. GPU training made required (2026-10-15)

After the curriculum review, the early-warning run has no cutline. The Task 2 Check-list line on the
GPU run requires that run's `training_run.json` to have `trained_on: "ec2"`, an EC2 instance id in
`instance_hint`, `device: "cuda"`, a non-null `gpu_name`, and `epoch_seconds.cuda` below
`epoch_seconds.cpu`. This reference was trained on a laptop (RTX 4090, `trained_on: local`), so its
early-warning run is local and would fail that line, by design: the GPU part passes (NVIDIA GeForce
RTX 4090, one epoch cpu 5.63 s, cuda 0.6 s), the EC2 part does not. It passes once the run is
repeated on the lab's `g4dn.xlarge` and its record and artifact are committed. The readmission run
is local too and would also fail its Task 2 line since 2026-10-19 (section 18).

## 18. Checker removed, one path (2026-10-19)

After a second curriculum review the repository no longer ships a submission checker. Tasks 2 to 4
close with the platform's automated check, which tests the lines of each task's Check-list; the old
script is kept for the platform-test authors outside this repository. `scripts/smoke.py` stays. The
readmission model and the stay summary (questions A and G1) are worked examples only: students pick
their own clinical question and their own generative or agentic feature, and the readmission-style
CPU run of their own question must also say `trained_on: "ec2"`.
