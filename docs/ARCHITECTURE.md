# Atlas architecture

Atlas is a multi-tenant patient-management platform: one deployment, many customer
organisations, each seeing only its own records.

## Services

`docker compose up --build` starts five containers.

| Service | What it is | Port | Code |
|---|---|---|---|
| `db` | PostgreSQL 16 | 5433 on your machine | `backend/app/migrations/` |
| `backend` | FastAPI app: the Atlas API | 8000 (`/docs`) | `backend/app/` |
| `worker` | Ingestion worker, same image as the backend | none | `backend/app/ingest/worker.py` |
| `meridian` | Meridian Clinical Exchange, the mock data vendor | 8200 | `feed/meridian/` |
| `frontend` | React app served by the Vite dev server | 3000 | `frontend/src/` |

The backend and frontend mount your working copy, so edits reload without a rebuild. A change
to `requirements.txt`, `package.json`, `packages/clinsim` or `feed/` needs
`docker compose up --build`.

`packages/clinsim` is a small clinical simulator shared by two things: the seed script that
writes the Test Facility's two-year history, and the Meridian feed that streams the live
facilities. One generator means seeded and live data have the same shape.

## Repository map

```
atlas/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app; every router is registered here
│   │   ├── config.py          settings from the environment
│   │   ├── db.py              connection pool, get_db dependency, fetch_all / fetch_one / execute
│   │   ├── auth.py            passwords, tokens, get_current_user, require_admin
│   │   ├── tenancy.py         load_patient, check_facility, facility_clause, audit
│   │   ├── schemas.py         Pydantic request and response models
│   │   ├── reference.py       vital-sign ranges and labels
│   │   ├── metering.py        record_usage(): one usage_events row per AI call
│   │   ├── migrate.py         applies migrations/*.sql in order
│   │   ├── seed.py            organisations, users, Test Facility history
│   │   ├── migrations/        0001_init.sql ...
│   │   ├── routers/           one file per area: patients, encounters, dashboard, results,
│   │   │                      activity, integrations, facilities, org, session, health
│   │   └── ingest/            meridian.py (client + signatures), store.py (raw events,
│   │                          watermark), processor.py (event -> rows), worker.py
│   └── tests/                 pytest, runs against the seeded database
├── feed/meridian/             the mock vendor: simulation, event log, webhook delivery
├── packages/clinsim/          the shared simulator
├── frontend/src/
│   ├── api/client.js          every API call; adds the base URL and the bearer token
│   ├── context/               AuthContext, FacilityContext (the facility switcher), LiveContext (SSE)
│   ├── hooks/                 useApi, usePatient, useLiveRefresh, ...
│   ├── components/            Card, Badge, Tabs, Spinner, Skeleton, Retry, Empty, ...
│   ├── layout/AppShell.jsx    sidebar, top bar, facility switcher
│   ├── pages/                 one file per route
│   └── panels/                the cards on the patient record
├── models/                    trained model files (mounted into the backend at /app/models)
├── scripts/smoke.py           end-to-end smoke check
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
- `tests/test_api.py` checks cross-tenant reads. Add a test like it for every new route.

Demo organisations: **Riverside Health** (Test Facility, Riverside General Hospital,
Northgate Clinic) and **Harborview Medical Group** (Harborview Medical Center).

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
| `usage_events` | one per AI call | tokens, predictions, status, latency |
| `audit_log` | one per sensitive action | logins, record views, integration changes |

There is no outcome column anywhere. Labels such as "readmitted within 30 days" are built
from `encounters` dates.

To change the schema, add `0002_<name>.sql`. Migrations run in order on backend start and
never run twice.

## Live updates

The worker ends each batch with `NOTIFY atlas_events '{tenant_id, facility_id, counts,
patient_ids}'`. `GET /api/activity/stream` holds a `LISTEN` connection and relays matching
notifications as Server-Sent Events. The frontend opens one stream for the whole app
(`LiveContext`) and pages subscribe with `useLiveRefresh(refresh, { facilityId })` or
`{ patientId }`. The stream uses `fetch()` with a `ReadableStream` rather than
`EventSource`, because it has to send the Authorization header.

## Frontend conventions

- Every HTTP call goes through `src/api/client.js`. Components call named functions such as
  `getVitals(patientId)`; none builds a URL.
- Pages get the facility filter from `useFacility()` and pass `facility_id` to the API.
  `facilityId === null` means all facilities.
- Loading, error and empty states use the shared `Skeleton` / `PanelSkeleton`, `Retry` and
  `Empty`, so every panel behaves the same way.
- Styling is Tailwind plus a few component classes in `src/index.css` (`card`, `btn`,
  `badge`, `field`, `data-table`). Charts use Recharts.
- Colour never carries meaning alone: every flag has a text label.

## Timekeeping

The Test Facility is a fixed history that ends when it was seeded. Dashboard windows ("last 24
hours") count back from `as_of`, the newest record in scope, so the dashboard stays
meaningful days later. Live facilities' `as_of` is effectively now.
