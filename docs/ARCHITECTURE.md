# Atlas architecture

Atlas is a multi-tenant patient-management platform: one deployment, many customer
organisations, each seeing only its own records. It is also the reference platform students
fork for their capstone, so this page covers the platform, the two AI features that ship with
it, and the two ways the stack runs (Compose for development, Kubernetes for deployment).

## Services

`docker compose up --build` starts six containers, one of which exits on purpose.

| Service | What it is | Port | Code |
|---|---|---|---|
| `db` | PostgreSQL 16 | 5433 on your machine | `backend/app/migrations/` |
| `seed` | One-shot: applies migrations and seeds the Test Facility, then exits 0. The API and the worker wait for it (`service_completed_successfully`). Harmless to run again: it leaves a seeded database alone. | none | `backend/app/seed.py` |
| `backend` | FastAPI app: the Atlas API | 8000 (`/docs`) | `backend/app/` |
| `worker` | Ingestion worker, same image as the backend | none | `backend/app/ingest/worker.py` |
| `meridian` | Meridian Clinical Exchange, the mock data vendor | 8200 | `feed/meridian/` |
| `frontend` | React app served by the Vite dev server | 3000 | `frontend/src/` |

The backend and frontend mount your working copy, so edits reload without a rebuild. A change
to `requirements.txt`, `package.json`, `packages/clinsim` or `feed/` needs
`docker compose up --build`. The `models/` folder is mounted into the backend at
`/app/models`, so a newly trained artifact needs only `docker compose restart backend`.

`packages/clinsim` is a small clinical simulator shared by two things: the seed script that
writes the Test Facility's two-year history, and the Meridian feed that streams the live
facilities. One generator means seeded and live data have the same shape.

## Repository map

```
atlas/
├── docker-compose.yml
├── .env.example
├── README.md, REFERENCE_NOTES.md
├── backend/
│   ├── Dockerfile             one image: API, worker and seed with different commands; copies models/
│   ├── app/
│   │   ├── main.py            FastAPI app; every router is registered here
│   │   ├── config.py          settings from the environment (table below)
│   │   ├── db.py              connection pool, get_db dependency, fetch_all / fetch_one / execute
│   │   ├── auth.py            passwords, tokens, get_current_user, require_admin
│   │   ├── tenancy.py         load_patient, check_facility, facility_clause, audit
│   │   ├── schemas.py         Pydantic request and response models
│   │   ├── reference.py       vital-sign ranges and labels
│   │   ├── metering.py        record_usage(): one usage_events row per AI call
│   │   ├── ml.py              readmission model: feature SQL, load_model(), bands
│   │   ├── llm.py             OpenAI-compatible client: configured(), chat()
│   │   ├── migrate.py         applies migrations/*.sql in order; wait_for_db()
│   │   ├── seed.py            organisations, users, Test Facility history
│   │   ├── migrations/        0001_init.sql ...
│   │   ├── routers/           one file per area: patients, encounters, dashboard, results,
│   │   │                      activity, integrations, facilities, org, session, health,
│   │   │                      risk (predictive feature), summary (generative feature)
│   │   └── ingest/            meridian.py (client + signatures), store.py (raw events,
│   │                          watermark), processor.py (event -> rows), worker.py
│   └── tests/                 pytest, runs against the seeded database
├── feed/meridian/             the mock vendor: simulation, event log, webhook delivery
├── packages/clinsim/          the shared simulator
├── frontend/
│   ├── Dockerfile             dev image (Vite dev server)
│   ├── Dockerfile.prod        multi-stage: npm run build, then nginx serving dist/
│   ├── nginx.conf, docker-entrypoint.d/   SPA fallback; writes config.js from VITE_API_URL at start
│   └── src/
│       ├── api/client.js      every API call; adds the base URL and the bearer token
│       ├── context/           AuthContext, FacilityContext (the facility switcher), LiveContext (SSE)
│       ├── hooks/             useApi, usePatient, useLiveRefresh, ...
│       ├── components/        Card, Badge, Tabs, Spinner, Skeleton, Retry, Empty, Button, ...
│       ├── layout/AppShell.jsx    sidebar, top bar, facility switcher
│       ├── pages/             one file per route; AiFeatures.jsx is the map of the AI features
│       └── panels/            the cards on the patient record, including RiskCard and SummaryCard
├── training/                  export_training_set.py, train.py, CODIO.md, data/, runs/<date>/
├── models/                    the trained artifact the API serves (mounted or baked in at /app/models)
├── evals/                     golden.jsonl, make_golden.py, run_evals.py for the generative feature
├── k8s/                       kind-config.yaml, base/ (kustomize), optional/hpa.yaml, exercises/
├── scripts/smoke.py           end-to-end smoke check, Compose or cluster
└── docs/
```

## Request path

```
browser ── fetch ──▶ frontend/src/api/client.js ── Authorization: Bearer <token> ──▶ FastAPI router
                                                                                        │
                                               Depends(get_current_user) ◀──────────────┤
                                               verifies the token, loads the user,       │
                                               returns CurrentUser(id, tenant_id, role)  │
                                                                                        ▼
                                                               SQL with  WHERE tenant_id = %s
```

1. `POST /api/auth/login` checks the password (PBKDF2) and returns a signed JWT (HS256,
   8 hours, claims `sub`, `tid`, `role`). The frontend stores it in `localStorage`.
2. Every other call sends `Authorization: Bearer <token>`. `get_current_user` (alias
   `current_user`) rejects a missing, forged or expired token with 401 and
   `WWW-Authenticate: Bearer`, and loads the user from the database.
3. Routes read `user.tenant_id` from that object. Never from the request body or a query
   parameter.
4. `require_admin` gates admin routes (usage, audit, integration controls) with 403.

## Multi-tenancy

Row-level: one database, one schema, and a `tenant_id` column on every clinical row. There
are no Postgres row-level security policies, so isolation is exactly as strong as the tenant
filter on each query. The rules the code follows:

- Every query that reads clinical data has `tenant_id = %s` bound to `user.tenant_id`.
- A record in another tenant is a 404, the same 404 as a record that does not exist
  (`tenancy.load_patient`). A response never confirms that another customer's id is real.
- A `facility_id` filter is checked against the tenant first (`tenancy.check_facility`).
- The live stream filters notifications by tenant before sending them.
- `tests/test_api.py` checks cross-tenant reads; `test_risk.py` and `test_summary.py` do the
  same for the AI routes. Add a test like them for every new route.

Demo organisations: Riverside Health (Test Facility, Riverside General Hospital, Northgate
Clinic) and Harborview Medical Group (Harborview Medical Center).

## Database

`backend/app/migrations/0001_init.sql` is the schema. Read it with
`docker compose exec db psql -U atlas -d atlas -c '\d+ encounters'`.

| Table | Grain | Notes |
|---|---|---|
| `tenants`, `facilities`, `users` | one row each | facilities have `data_source` = `seed` or `meridian` |
| `patients` | one per person per facility | `mrn`, `name`, `sex`, `date_of_birth`, `age` (at registration) |
| `encounters` | one per visit | `admission_type`, `admit_source`, `admit_date`, `discharge_date` (NULL while still here), `discharge_disposition` |
| `vitals` | one per measurement | `measurement`, `value`, `unit`, `recorded_at` |
| `labs` | one per result | `test_code`, `loinc`, `panel`, `value`, `unit`, `ref_low`, `ref_high`, `flag`, `collected_at`, `resulted_at` |
| `medications` | one per order | `drug_code`, `drug_name`, `dose`, `route`, `frequency`, `status`, `ordered_at`, `ended_at` |
| `medication_administrations` | one per dose given | `administered_at`, `dose` |
| `integration_connections` | one per fed facility | subscription, watermark, health |
| `ingest_events` | one per Meridian event | raw payload, processing status |
| `usage_events` | one per AI call | feature, model, tokens, predictions, status (`ok`, `limited`, `error`), latency |
| `audit_log` | one per sensitive action | logins, record views, integration changes |

There is no outcome column anywhere. Labels such as "readmitted within 30 days" are built
from `encounters` dates.

To change the schema, add `0002_<name>.sql`. Migrations run in order when the seed service
(or the seed Job) starts and never run twice.

## The two AI features

Both sit in the AI insights section of the patient record, both go through the request path
above (token, tenant check, bound SQL), and both write one `usage_events` row per call.

### Readmission risk (predictive)

```
training time                                  request time
Test Facility rows ──▶ ml.encounter_features() ──▶ CSV      patient id ──▶ ml.build_features_for_patient()
                       (one SQL definition)        │                        (the same SQL, one row)
                                                   ▼                                    │
                              training/train.py: Pipeline(ColumnTransformer,            ▼
                              HistGradientBoostingClassifier) ──▶ models/readmission.joblib ──▶ ml.load_model() once
                                                   │                                    │
                                                   ▼                                    ▼
                              training/runs/<date>/training_run.json      probability ──▶ band ──▶ record_usage()
                              (metrics, calibration, hashes)                         ──▶ RiskCard.jsx
```

- One feature definition in `backend/app/ml.py` is used by both the export and the route, so
  the model never reads a column it was not trained on.
- The unit of prediction is one finished inpatient or observation stay; the label is another
  inpatient, observation or emergency encounter within 30 days of discharge (an emergency
  visit counts as "came back", so the card says "readmission or emergency visit"). Features
  are what the record held at discharge, so nothing from afterwards leaks in. Encounters that
  ended in death are excluded.
- `GET /api/patients/{id}/risk/readmission` answers 200 `{probability, band, model_version,
  trained_on, trained_at, features_used, as_of, encounter_id, window_closes_at, outcome,
  readmitted_at}`, 404 "not applicable" when the patient has no finished stay or is admitted
  right now, 404 "Patient not found" for another tenant's patient, and 503 when there is no
  file at `MODEL_PATH` (or it fails the optional `MODEL_SHA256` check). Bands: low below 0.15,
  medium to 0.35, high above. `outcome` is "pending" only while the 30-day window is open;
  otherwise the record already knows ("readmitted" or "not_readmitted") and the card shows
  the score as what the model said at discharge, not as a live risk.
- The artifact is loaded in the FastAPI lifespan, before the first request (about a second),
  so the load happens inside the Kubernetes startup probe's window rather than inside a user's
  request. A missing file is logged and the route answers 503 until one appears.
- `prior_admissions_365d` is the strongest feature and is almost always zero at the live
  facilities, which start with fourteen days of history. Scores there show the plumbing, not
  the model's accuracy; `training/README.md` has the numbers.
- The repository root has a `.dockerignore` that keeps `.env`, `k8s/secret.env`, `.venv` and
  `node_modules` out of the build context, so even a careless `COPY . .` could not bake a key
  into `atlas-api` or `atlas-meridian`.

### Stay summary (generative)

```
patient id ──▶ summary.fetch_grounding()  encounter + last vital per measurement + up to 20 labs + the stay's orders
                     │
                     ▼  one line per row, ids included, no patient name
            llm.chat(messages) ──▶ OpenAI-compatible endpoint (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY)
                     │
                     ▼
            {summary, sources: [{table, id}...], model, input_tokens, output_tokens, elapsed_ms, encounter_id}
                     │
                     ├──▶ record_usage(status ok | limited | error, tokens)  ──▶ Usage page, daily ceiling
                     └──▶ SummaryCard.jsx (button, Sources toggle)
```

- Before calling the model the route checks `llm.configured()` (503 "no model configured" when
  `LLM_API_KEY` is empty) and the tenant's tokens since midnight UTC against
  `LLM_DAILY_TOKEN_LIMIT` (429 "daily token limit reached", recorded as `status="limited"`).
  An endpoint failure is 502 "model endpoint error", recorded as `status="error"`. Those two
  paths commit before raising, or FastAPI's rollback would lose the row.
- The prompt holds only rows fetched for the caller's tenant. The same ids come back as
  `sources`, and `evals/run_evals.py` checks every number and medication in the text against
  them. `evals/make_golden.py` calls `fetch_grounding()` itself so the golden set is exactly
  what the route sends.
- `backend/tests/test_summary.py` stubs `llm.chat` so the tests never call a real endpoint.

## Environment

Settings are read once in `backend/app/config.py`; every one has a development default so a
fresh clone runs. Compose sets them from `.env`; Kubernetes from the ConfigMap `atlas-config`
and the Secret `atlas-secrets`.

| Variable | Default | Used by | Secret? |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://atlas:atlas@localhost:5432/atlas` (Compose and Kubernetes assemble it) | api, worker, seed | holds the password |
| `JWT_SECRET` | `dev-only-change-me` | api | yes |
| `JWT_TTL_MINUTES` | `480` | api | |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` (cluster: `http://localhost`) | api | |
| `SEED_DEMO`, `DEMO_PASSWORD` | `true`, `atlas-demo` | seed | |
| `MERIDIAN_BASE_URL` | `http://localhost:8100` (Compose and cluster: `http://meridian:8100`) | worker | |
| `MERIDIAN_API_KEY` | `mcx-dev-key` | api, worker, meridian | yes |
| `MERIDIAN_CALLBACK_URL` | Compose `http://backend:8000/...`, cluster `http://api:8000/api/integrations/webhooks/meridian` | worker | |
| `WORKER_POLL_SECONDS`, `WORKER_BATCH_SIZE`, `RESYNC_CHECK_SECONDS` | `1.0`, `500`, `60` | worker | |
| `FEED_FACILITIES`, `FEED_BACKFILL_DAYS`, `FEED_CADENCE_SCALE` | `RGH,RNC,HMC`, `14`, `1` | meridian | |
| `MODEL_PATH` | `/app/models/readmission.joblib` | api (risk route) | |
| `LLM_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | api (summary route) | |
| `LLM_MODEL` | `Qwen/Qwen3-235B-A22B-Instruct-2507` | api | |
| `LLM_API_KEY` | empty (the summary route answers 503 until set) | api | yes |
| `LLM_DAILY_TOKEN_LIMIT` | `200000` input plus output tokens per tenant per UTC day | api | |
| `VITE_API_URL` | `http://localhost:8000` (cluster: empty, meaning same origin as the page) | frontend (dev: build time; prod image: written to `config.js` at start) | |

## Kubernetes topology

`k8s/base/` runs the same containers on a local cluster (kind by default). Namespace `atlas`.

```
 your machine                                   kind node (one Docker container)
 http://localhost ─────▶ :80 ───▶ ingress-nginx ───▶ Ingress atlas
                                                      │  /api, /health, /docs, /openapi.json
                                                      │             ▼
                                                      │   Service api:8000 ──▶ Deployment api x2 (atlas-api)
                                                      │                          startup /health, readiness /health,
                                                      │                          liveness /health/live, models/ baked in
                                                      │  /
                                                      └───▶ Service web:80 ──▶ Deployment web x2 (atlas-web, nginx,
                                                                                   config.js written from VITE_API_URL)

   Deployment worker x1 (atlas-api, python -m app.ingest.worker, liveness = heartbeat file age)
   Deployment meridian x1 (atlas-meridian) ── Service meridian:8100 ── PVC meridian-data 1Gi
   Deployment db x1 (postgres:16-alpine, Recreate) ── Service db:5432 ── PVC pgdata 2Gi
   Job atlas-seed (atlas-api, python -m app.seed) runs once, then is removed after 10 minutes

   ConfigMap atlas-config: every non-secret variable above      Secret atlas-secrets: POSTGRES_PASSWORD,
   (created from k8s/base/configmap.yaml)                       JWT_SECRET, MERIDIAN_API_KEY, LLM_API_KEY
                                                                (created from the gitignored k8s/secret.env)

   Meridian ──webhooks──▶ http://api:8000/api/integrations/webhooks/meridian (MERIDIAN_CALLBACK_URL)
```

Three images, tagged `sha-<short git hash>` and loaded with `kind load docker-image`:
`atlas-api` (backend/Dockerfile: the API, the worker and the seed Job with different commands;
`models/` is copied in), `atlas-web` (frontend/Dockerfile.prod), `atlas-meridian`
(feed/Dockerfile). `k8s/README.md` has the build, create, load, apply sequence, the ladder of
proof and the rollback drill; `k8s/optional/hpa.yaml` is an untested autoscaler for lessons;
`k8s/exercises/broken-api.yaml` is a Deployment with planted faults.

## Live updates

The worker ends each batch with `NOTIFY atlas_events '{tenant_id, facility_id, counts,
patient_ids}'`. `GET /api/activity/stream` holds a `LISTEN` connection and relays matching
notifications as Server-Sent Events. The frontend opens one stream for the whole app
(`LiveContext`) and pages subscribe with `useLiveRefresh(refresh, { facilityId })` or
`{ patientId }`. The stream uses `fetch()` with a `ReadableStream` rather than
`EventSource`, because it has to send the Authorization header. Through ingress-nginx the
stream needs the Ingress annotation `proxy-read-timeout: "3600"`, which `ingress.yaml` sets.

## Frontend conventions

- Every HTTP call goes through `src/api/client.js`. Components call named functions such as
  `getVitals(patientId)`; none builds a URL.
- Pages get the facility filter from `useFacility()` and pass `facility_id` to the API.
  `facilityId === null` means all facilities.
- Loading, error and empty states use the shared `Skeleton` / `PanelSkeleton`, `Retry` and
  `Empty`, so every panel behaves the same way. AI panels add the states their route can
  answer with: `RiskCard` shows not-applicable (404), model unavailable (503) and a
  low-confidence note for the medium band; `SummaryCard` shows no model configured (503),
  daily limit reached (429), not applicable (404) and a Sources toggle on success.
- Styling is Tailwind plus a few component classes in `src/index.css` (`card`, `btn`,
  `badge`, `field`, `data-table`). Charts use Recharts.
- Colour never carries meaning alone: every flag has a text label.

## Timekeeping

The Test Facility is a fixed history that ends when it was seeded. Dashboard windows ("last 24
hours") count back from `as_of`, the newest record in scope, so the dashboard stays
meaningful days later. Live facilities' `as_of` is effectively now. The seed's random seed is
fixed, but the 730-day window is shifted to end on the seed day, so two databases seeded on
different days hold slightly different histories (counts, ids, and therefore training rows and
golden-set encounters). The committed training snapshot and golden set belong to the seed day
recorded in `REFERENCE_NOTES.md`.
