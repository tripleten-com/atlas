# Working on Atlas

Atlas is a multi-tenant patient-management platform (FastAPI + PostgreSQL + React/Vite +
Tailwind), with clinical data streamed in from a mock vendor feed (Meridian). All data is
synthetic. It is the reference platform for a capstone project: students fork it, keep the
platform, and add their own predictive feature and their own generative or agentic feature,
then deploy the stack on a local Kubernetes cluster. Two reference AI features are already in
place as worked examples. Read `docs/ARCHITECTURE.md` before changing structure,
`docs/DATA_FEED.md` before touching ingestion, and `docs/ADDING_A_FEATURE.md` before adding a
route or a panel.

## Run and check

- `docker compose up --build` starts everything; the app is on http://localhost:3000, the API
  on http://localhost:8000/docs, Meridian on http://localhost:8200. A one-shot `seed` service
  migrates and seeds first; the API and the worker wait for it.
- `python scripts/smoke.py` checks the whole stack (Compose). `python scripts/smoke.py --base
  http://localhost` checks the Kubernetes cluster through its Ingress.
- `docker compose exec backend pytest` and `docker compose exec backend ruff check app tests`
  must pass before you finish a change. `ruff check --config backend/ruff.toml training evals
  scripts` covers the scripts outside the backend.
- `docker compose exec frontend npx vite build` must pass for frontend changes.
- Demo login: `clinician@riverside.example` / `atlas-demo` (admin: `admin@riverside.example`;
  the other organisation: `clinician@harborview.example`).
- Database: `docker compose exec db psql -U atlas -d atlas`, or `localhost:5433` from the host.
- Training and evals run on the host against `localhost:5433` from a venv:
  `.venv/Scripts/python training/export_training_set.py`, `training/train.py`,
  `evals/make_golden.py`, `evals/run_evals.py`.
- Kubernetes: `k8s/README.md`. Cluster name `atlas`, namespace `atlas`, images
  `atlas-api`, `atlas-web`, `atlas-meridian` tagged `sha-<short git hash>`, applied with
  `kubectl apply -k k8s/base`.

## Rules that keep the platform safe

1. Every route that returns clinical data depends on `get_current_user` (from `app/auth.py`).
   Only `/health`, `/health/live`, `/api/auth/login` and the signed Meridian webhook are public.
2. Every clinical query filters on `user.tenant_id`. Use `tenancy.load_patient()` for a single
   patient and `tenancy.check_facility()` for a facility filter. A record from another tenant
   must return the same 404 as a missing one, and every new route gets a test for that.
3. Tenant and user come from the token, never from the request body or query string.
4. SQL uses bound parameters (`%s` or `%(name)s`). Never format values into SQL.
5. Secrets live in `.env` and `k8s/secret.env`, both gitignored. Never hard-code a key, never
   commit either file, never print or log a key, never write one into a document or a test.
   `LLM_API_KEY` is read from settings only and is never returned to the browser.
6. Do not log patient names or record contents. Log ids and counts. Prompts to a language model
   carry only rows the backend fetched for the caller's tenant, and no patient name.
7. Schema changes are new files in `backend/app/migrations/` (`0002_...sql`). Never edit a
   migration that has run.

## Conventions

- Backend routers declare full paths (`@router.get("/api/...")`) with no prefix and are
  registered in `backend/app/main.py`. Response shapes live in `app/schemas.py`.
- Database access is plain SQL through `app/db.py` (`fetch_all`, `fetch_one`, `execute`);
  there is no ORM.
- Frontend HTTP goes only through `frontend/src/api/client.js`. Add a named function there;
  do not call `fetch` from components.
- New patient-record cards go in `frontend/src/panels/` as named exports (re-exported from
  `panels/index.js`) and are added to the AI insights section of
  `frontend/src/pages/PatientDetail.jsx`.
- Use shared components from `frontend/src/components` (`Card`, `Badge`, `Tabs`, `Spinner`,
  `PanelSkeleton`, `Retry`, `Empty`, `Button`...). Every panel handles loading, error and empty
  states; AI panels also handle "not applicable", "model unavailable" or "no model configured",
  and "daily limit reached" where they apply.
- Pages read the facility filter from `useFacility()` and pass it as `facility_id`
  (`null` = all facilities). Live pages call `useLiveRefresh(refresh, { facilityId })`.
- Styling: Tailwind utilities plus the component classes in `frontend/src/index.css`. Colour
  never carries meaning on its own; pair it with text.
- AI features record each call with `record_usage()` from `backend/app/metering.py`, including
  refused (`status="limited"`) and failed (`status="error"`) calls. Commit the connection before
  raising an HTTPException, or the row is rolled back with the request.
- Model files go in `models/` (`/app/models` in the container, `MODEL_PATH` names the file).
  LLM settings are `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_DAILY_TOKEN_LIMIT`; the
  client lives in `app/llm.py` and speaks the OpenAI chat-completions protocol to any endpoint.
- Code is read by beginners: short functions, comments that say why, no cleverness.

## The two reference AI features

- Readmission risk: `backend/app/ml.py` (feature SQL shared with
  `training/export_training_set.py`, `load_model()`, bands low < 0.15, medium < 0.35, high),
  `backend/app/routers/risk.py` (`GET /api/patients/{id}/risk/readmission`; 404 "not
  applicable" when the patient has no finished inpatient or observation stay or is admitted
  right now; 503 when the artifact is missing or fails the optional `MODEL_SHA256` check; the
  response carries `outcome` so the card shows an old score as history, not as a live risk),
  `frontend/src/panels/RiskCard.jsx`, `backend/tests/test_risk.py`. The label counts emergency
  visits as "came back"; the history feature counts stays only; both are documented.
  Training: `training/` (scripts, `CODIO.md`, `data/readmission.csv` plus
  `readmission.columns.json`, `runs/<date>/`); `train.py` writes the artifact into its `--out`
  folder and you copy it to `models/readmission.joblib`. The model is loaded in the lifespan in
  `main.py`. Encounters that ended in death are excluded on both sides; keep the two in step.
- Stay summary: `backend/app/llm.py`, `backend/app/routers/summary.py`
  (`POST /api/patients/{id}/summary`; `fetch_grounding()` is what the prompt sees and what
  `evals/make_golden.py` records; 503 no key, 429 daily ceiling, 502 endpoint failure),
  `frontend/src/panels/SummaryCard.jsx` (button-driven), `backend/tests/test_summary.py`
  (stubbed `llm.chat`), `evals/golden.jsonl` and `evals/run_evals.py`.

## Data facts worth knowing

- Tables: `patients`, `encounters`, `vitals`, `labs`, `medications`,
  `medication_administrations`. Grain: one row per person, per visit, per measurement, per
  result, per order, per dose.
- `encounters.discharge_date IS NULL` means the patient is still here.
- There is no outcome label column; build labels from encounter dates.
- The Test Facility (code `DEMO`) is the static training set. Live facilities keep changing.
- The seed uses a fixed random seed but shifts the history to end on the seed day, so a
  database seeded on another day has slightly different counts and ids. The committed training
  CSV, run record, artifact and golden set belong together; regenerate the golden set
  (`evals/make_golden.py`) after a reseed, and treat a re-export as a new training set.
- There is no ward, bed, staffing, billing, imaging or free-text note data.
- `REFERENCE_NOTES.md` holds every measured number (seed counts, metrics, timings) with the
  command that produced it; update it when a number changes.
