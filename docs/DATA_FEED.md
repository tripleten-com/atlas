# The data feed: how clinical data reaches Atlas

Atlas does not generate clinical data. Hospitals' own systems do, and a health-data network
forwards it. In this repository that network is **Meridian Clinical Exchange**, a mock vendor
that runs as its own container (`meridian`, http://localhost:8200). It simulates three
facilities and streams what happens in them to Atlas, exactly the way a real integration
vendor would.

```
 hospital systems        Meridian Clinical Exchange              Atlas
 (simulated)             (feed/)                                 (backend/)
┌──────────────┐   ┌──────────────────────────────┐   ┌──────────────────────────────────────┐
│ admissions   │   │ event log, one sequence per  │   │ POST /api/integrations/webhooks/     │
│ vitals       │──▶│ facility                     │──▶│      meridian  (signature checked)   │
│ labs         │   │ subscriptions + signed       │   │   │                                  │
│ medications  │   │ webhook deliveries, retries  │   │   ▼                                  │
└──────────────┘   │ replay endpoint              │◀──│ ingest_events (raw, idempotent)      │
                   └──────────────────────────────┘   │   │  worker: apply in order, retry   │
                                                      │   ▼                                  │
                                                      │ patients, encounters, vitals, labs,  │
                                                      │ medications, administrations         │
                                                      │   │  NOTIFY atlas_events             │
                                                      │   ▼                                  │
                                                      │ SSE /api/activity/stream → browser   │
                                                      └──────────────────────────────────────┘
```

## Facilities

| Code | Facility | Organisation | Source |
|---|---|---|---|
| `DEMO` | Test Facility | Riverside Health | Seeded once: two years of history with fixed dates that end on the day it was seeded; never changes |
| `RGH` | Riverside General Hospital | Riverside Health | Meridian feed, live |
| `RNC` | Northgate Clinic | Riverside Health | Meridian feed, live, outpatient only |
| `HMC` | Harborview Medical Center | Harborview Medical Group | Meridian feed, live |

The Test Facility is the dataset to train on. The live facilities are where you watch a
feature react to data arriving.

## Timing

On first start each live facility plays `FEED_BACKFILL_DAYS` (default 14) of history at full
speed, so there is something to look at within a minute. After that it keeps pace with the
clock: Meridian simulates the next hour in advance and publishes each event when its own time
arrives, so events trickle in continuously (a few dozen an hour per hospital, in bursts as
rounds and doses fall due) and nothing is ever stamped in the future. Clinics keep office hours, so Northgate is quiet at night.

`FEED_CADENCE_SCALE` (default 1, real time) compresses clinical time for a fast demo. At 30, a
clinical day passes every 48 minutes. Timestamps stay real, so daily charts for live
facilities will show that compression as spikes.

## Event envelope

Every event looks like this. `sequence` increases by one per facility; `epoch` identifies the
log (it changes only if Meridian's storage is wiped).

```json
{
  "id": "evt_4c1f0b6e2a9d4f5e8b7a6c5d4e3f2a1b",
  "sequence": 1843,
  "type": "vital_signs.recorded",
  "schema_version": 1,
  "facility": "RGH",
  "epoch": "0dec0bbfcbda",
  "occurred_at": "2026-09-16T14:02:00+00:00",
  "published_at": "2026-09-16T14:02:01.412+00:00",
  "patient": { "mrn": "RGH-100520" },
  "encounter": { "visit_id": "VRGH0DE10000159" },
  "data": { "...": "depends on type, below" }
}
```

## Event types

| Type | `data` | Atlas writes |
|---|---|---|
| `patient.registered` | `mrn, first_name, last_name, sex, date_of_birth` | `patients` (upsert on facility + MRN) |
| `encounter.admitted` | `visit_id, admission_type, admit_source, admitted_at` | `encounters` (upsert on facility + visit id) |
| `encounter.discharged` | `visit_id, discharged_at, disposition` | `encounters.discharge_date`, `discharge_disposition` |
| `vital_signs.recorded` | `recorded_at, measurements: [{code, label, value, unit}]` | one `vitals` row per measurement |
| `lab.resulted` | `order_id, panel, panel_name, collected_at, resulted_at, results: [{code, loinc, label, value, unit, ref_low, ref_high, flag}]` | one `labs` row per result |
| `medication.ordered` | `order_id, code, name, drug_class, dose, dose_unit, route, frequency, ordered_at` | `medications` (upsert on order id) |
| `medication.administered` | `order_id, code, dose, dose_unit, route, administered_at` | `medication_administrations` |
| `medication.discontinued` | `order_id, reason, discontinued_at` | `medications.status`, `ended_at` |

Vital codes: `heart_rate`, `systolic_bp`, `diastolic_bp`, `respiratory_rate`, `temperature`,
`spo2`. Lab panels: `BMP`, `CBC`, `LACTATE`, `TROPONIN`, `BNP`, `HBA1C`, `CRP`. Lab flags:
`L`, `N`, `H`. Admission types: `inpatient`, `observation`, `emergency`, `outpatient`.

## Delivery

Atlas subscribes once per facility (`POST /v1/subscriptions` with a callback URL and a shared
secret). The worker does this on startup, so there is nothing to click. Meridian then POSTs
batches of up to 250 events:

```
POST /api/integrations/webhooks/meridian
X-Meridian-Signature: t=1788465600,v1=5d41402abc4b2a76b9719d911017c592...
X-Meridian-Delivery: dlv_9f86d081884c7d65

{ "delivery_id": "...", "subscription_id": "sub_...", "facility": "RGH", "epoch": "...", "events": [ ... ] }
```

`v1` is the hex HMAC-SHA256 of `"{t}." + body` with the shared secret. Atlas rejects a
delivery whose signature does not match or whose timestamp is more than five minutes old.
Anything other than a 2xx makes Meridian back off (1 s, 2 s, 4 s, up to a minute) and retry
the same batch, so nothing is lost while Atlas is down.

## What Atlas does with it

1. Store raw, idempotently. Every event lands in `ingest_events` first, unique on
   Meridian's event id. A redelivered event is counted as a duplicate and ignored.
2. Track the high watermark. For each connection Atlas records the highest sequence it
   holds with no gap below it. A delivery that skips ahead sets `needs_resync`.
3. Resync. Every minute (`RESYNC_CHECK_SECONDS`) the worker compares each connection with
   Meridian and pulls anything after the watermark from
   `GET /v1/facilities/{code}/events?after_sequence=N` when an admin pressed Resync, when a
   gap set `needs_resync`, when Meridian's log was reset (new `epoch`), or when Meridian holds
   events Atlas does not and no delivery has landed for 90 seconds. A connection that has
   never received a delivery counts as stalled too, so a wrong `MERIDIAN_CALLBACK_URL` still
   gets data by resync (`should_resync()` in `backend/app/ingest/worker.py`).
4. Apply in order. The worker (`python -m app.ingest.worker`) claims pending events in
   sequence order and applies each in its own savepoint.
5. Defer what arrives early. A vital for a visit whose admission has not arrived yet is
   marked `deferred` and retried with backoff (2 s, 4 s, 8 s...). After eight attempts it is
   `failed` and shows on the Integrations page, where an admin can requeue it.
6. Announce. After each batch the worker sends `NOTIFY atlas_events`. The API relays that
   to browsers over Server-Sent Events, and open pages refresh.

## Meridian is untidy on purpose

Real feeds are. Meridian deliberately:

- redelivers an event now and then (`FEED_DUPLICATE_RATE`, default 3% of deliveries),
- publishes a new patient's registration after their first visit events (`FEED_REORDER_RATE`),
- publishes a few measurements 10 to 60 minutes late (`FEED_LATE_RATE`).

The ingestion log on each integration's page shows all three happening, and what Atlas did.

## Useful commands

```bash
# Meridian's own view: facilities, sequences, subscriptions: visit http://localhost:8200 in your browser
curl -H "Authorization: Bearer mcx-dev-key" http://localhost:8200/v1/subscriptions

# replay events yourself
curl -H "Authorization: Bearer mcx-dev-key" "http://localhost:8200/v1/facilities/RGH/events?after_sequence=0&limit=5"

# what the worker is doing
docker compose logs -f worker

# ingestion health in SQL
docker compose exec db psql -U atlas -d atlas -c "SELECT status, count(*) FROM ingest_events GROUP BY 1"
```
