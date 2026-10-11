# Atlas

**Atlas** is the patient-management platform built by Atlas Health, a (fictional) forty-person
company selling to hospitals, clinics and research groups. A clinician logs in, picks a
facility, searches patients, and reads one patient's visits, vitals, labs and medications on a
single page. Admins watch the dashboards across the caseload and the health of the data feed.
Every customer is a separate tenant whose records nobody outside it can reach.

Clinical data reaches Atlas the way it does in real life: hospital systems send it to a
health-data network, **Meridian Clinical Exchange**, which forwards it to Atlas in signed
webhook deliveries. Meridian runs here as a mock service that simulates three facilities.

Everything is synthetic. No real patients.

## Start it

You need Docker Desktop (or Docker Engine with Compose v2) and about 4 GB of free memory.

```bash
cp .env.example .env
docker compose up --build
```

The first start takes a few minutes: images build, the database is created, the Test Facility
is seeded with two years of history (about 40 seconds), and Meridian backfills two weeks of data
for the live facilities. When the logs settle:

| What | Where |
|---|---|
| Atlas | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Meridian (mock data feed) | http://localhost:8200 |
| PostgreSQL | `localhost:5433`, database `atlas`, user `atlas`, password `atlas` |

Then check everything is wired up:

```bash
python scripts/smoke.py
```

## Demo logins

Password for every account: **`atlas-demo`**

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
| **Test Facility** | Riverside Health | About 1,200 patients and 8,400 visits over two years, with vitals, labs and medications. Seeded once; it never changes. Train on this. |
| Riverside General Hospital | Riverside Health | Live from Meridian: admissions, vitals, labs and medications as they happen |
| Northgate Clinic | Riverside Health | Live from Meridian, outpatient visits only |
| Harborview Medical Center | Harborview Medical Group | Live from Meridian |

Live facilities run in real time: each event arrives when it happens (a few dozen an hour per
hospital, in bursts as rounds and doses fall due), after a fourteen-day backfill on first start. `docs/DATA_FEED.md` explains the
feed, its event format, and how to speed it up.

The clinical tables are `patients`, `encounters`, `vitals`, `labs`, `medications` and
`medication_administrations`. Read them yourself:

```bash
docker compose exec db psql -U atlas -d atlas
\dt
\d encounters
SELECT count(*) FROM patients;
```

## Everyday commands

```bash
docker compose up --build              # start, rebuilding images that changed
docker compose logs -f backend worker  # follow the API and the ingestion worker
docker compose exec backend pytest     # backend tests
docker compose exec backend ruff check app tests
docker compose exec backend python -m app.seed --reset   # rebuild the Test Facility history
docker compose down                    # stop (keeps data)
docker compose down -v                 # stop and delete all data, then `up` starts fresh
```

The backend (`backend/`) and frontend (`frontend/src/`) are mounted into their containers, so
saving a file reloads the app. Changes to dependencies need `--build`.

## Where things live

```
backend/app/routers/     API routes, one file per area, registered in backend/app/main.py
backend/app/ingest/      the Meridian client, raw event store, event processor, worker
backend/app/migrations/  the schema
frontend/src/api/client.js   every API call the frontend makes
frontend/src/pages/      one file per screen
frontend/src/panels/     the cards on the patient record
feed/meridian/           the mock data vendor
packages/clinsim/        the clinical simulator the seed and the feed share
models/                  trained model files (visible to the backend at /app/models)
docs/                    architecture, data feed, adding a feature
```

Start with `docs/ARCHITECTURE.md`, then `docs/ADDING_A_FEATURE.md`.

## Troubleshooting

- **A port is already in use.** Change `WEB_PORT`, `API_PORT`, `MERIDIAN_PORT` or `DB_PORT`
  in `.env` and run `docker compose up` again. If you change `WEB_PORT` or `API_PORT`, update
  `CORS_ORIGINS` and `VITE_API_URL` to match.
- **The page says it can't reach the API.** `docker compose ps` should show `backend` as
  healthy. `docker compose logs backend` shows why if not; the first start waits for the seed.
- **Live facilities show no data.** `docker compose logs worker meridian`. The Integrations
  page shows each connection's last delivery and any errors.
- **Edits don't reload on Windows or macOS.** Polling is on by default in `docker-compose.yml`
  (`CHOKIDAR_USEPOLLING`, `WATCHFILES_FORCE_POLLING`). Restart the service if a change is
  missed.
- **Start over.** `docker compose down -v && docker compose up --build`.
