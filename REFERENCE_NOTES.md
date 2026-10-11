# Reference notes: the numbers behind the Atlas reference build

Every number a lesson or a project page may quote about the reference build, with the command
that produced it and the date it was observed. Numbers marked "reported, not re-run" come from
the build agents' reports on 2026-10-11 and were not reproduced in the final verification pass;
everything else was observed in that pass on 2026-10-11 (Windows 11, Docker Desktop, Docker
server 29.5.2, 16 GB of memory, Python 3.12.3 in `.venv`).

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
