# Working on Atlas

Atlas is a multi-tenant patient-management platform (FastAPI + PostgreSQL + React/Vite +
Tailwind), with clinical data streamed in from a mock vendor feed (Meridian). All data is
synthetic. Read `docs/ARCHITECTURE.md` before changing structure, and `docs/DATA_FEED.md`
before touching ingestion.

## Run and check

- `docker compose up --build` starts everything; the app is on http://localhost:3000, the API
  on http://localhost:8000/docs, Meridian on http://localhost:8200.
- `python scripts/smoke.py` checks the whole stack.
- `docker compose exec backend pytest` and `docker compose exec backend ruff check app tests`
  must pass before you finish a change.
- `cd frontend && npx vite build` must pass for frontend changes.
- Demo login: `clinician@riverside.example` / `atlas-demo` (admin: `admin@riverside.example`).
- Database: `docker compose exec db psql -U atlas -d atlas`, or `localhost:5433` from the host.

## Rules that keep the platform safe

1. **Every route that returns clinical data depends on `get_current_user`** (from
   `app/auth.py`). Only `/health`, `/api/auth/login` and the signed Meridian webhook are public.
2. **Every clinical query filters on `user.tenant_id`.** Use `tenancy.load_patient()` for a
   single patient and `tenancy.check_facility()` for a facility filter. A record from another
   tenant must return the same 404 as a missing one.
3. **Tenant and user come from the token**, never from the request body or query string.
4. **SQL uses bound parameters** (`%s` or `%(name)s`). Never format values into SQL.
5. **Secrets live in `.env`** (gitignored). Never hard-code keys or commit `.env`.
6. **Do not log patient names or record contents.** Log ids and counts.
7. **Schema changes are new files** in `backend/app/migrations/` (`0002_...sql`). Never edit a
   migration that has run.

## Conventions

- Backend routers declare full paths (`@router.get("/api/...")`) with no prefix and are
  registered in `backend/app/main.py`. Response shapes live in `app/schemas.py`.
- Database access is plain SQL through `app/db.py` (`fetch_all`, `fetch_one`, `execute`);
  there is no ORM.
- Frontend HTTP goes only through `frontend/src/api/client.js`. Add a named function there;
  do not call `fetch` from components.
- New patient-record cards go in `frontend/src/panels/` as named exports and are added to the
  panel list in `frontend/src/pages/PatientDetail.jsx`.
- Use shared components from `frontend/src/components` (`Card`, `Badge`, `Tabs`, `Spinner`,
  `PanelSkeleton`, `Retry`, `Empty`...). Every panel handles loading, error and empty states.
- Pages read the facility filter from `useFacility()` and pass it as `facility_id`
  (`null` = all facilities). Live pages call `useLiveRefresh(refresh, { facilityId })`.
- Styling: Tailwind utilities plus the component classes in `frontend/src/index.css`. Colour
  never carries meaning on its own; pair it with text.
- AI features record each call with `record_usage()` from `backend/app/metering.py`. Model
  files go in `models/` (`/app/models` in the container). LLM settings are `LLM_BASE_URL`,
  `LLM_API_KEY`, `LLM_MODEL`.

## Data facts worth knowing

- Tables: `patients`, `encounters`, `vitals`, `labs`, `medications`,
  `medication_administrations`. Grain: one row per person, per visit, per measurement, per
  result, per order, per dose.
- `encounters.discharge_date IS NULL` means the patient is still here.
- There is no outcome label column; build labels from encounter dates.
- The Test Facility (code `DEMO`) is the static training set. Live facilities keep changing.
- There is no ward, bed, staffing, billing, imaging or free-text note data.
