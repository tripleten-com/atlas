# The document store for AI outputs

Atlas keeps two kinds of data in two places. The clinical record (patients, encounters, vitals,
labs, medications) is relational and lives in PostgreSQL. What the AI features produce (a
readmission score with the 33 features it read, a stay summary with the rows it was grounded in,
an eval run with one result per case) lives in MongoDB, database `atlas_ai`.

Why not one more Postgres table? It would work at this scale, and Atlas already stores the
vendor's raw deliveries as JSONB. The document store earns its place in three ways: retention by
TTL (scores expire by themselves), schema freedom (every feature and every model version writes a
different shape, with no migration), and a boundary (AI history can be backed up, purged or moved
without touching patient data). The choice is a judgement; the architecture review asks you to
defend yours.

The code is `backend/app/ai_store.py`. Routes call `record_prediction()` or
`record_generation()` right after `record_usage()`, with the same `request_id`, so one request can
be followed from the Postgres `usage_events` row to its document.

## Collections

Every document carries `tenant_id` (the same integer as in Postgres), `created_at` (UTC),
`feature`, `model_version` and `request_id`. Beyond those:

| Collection | One document per | Fields | Index | Retention |
|---|---|---|---|---|
| `predictions` | scored request | `patient_id`, `encounter_id`, `probability`, `band`, `alert`, `inputs` (the feature row or window summary), `state` | `tenant_patient_created` on `{tenant_id: 1, patient_id: 1, created_at: -1}`; `created_at_ttl` on `created_at`, expires after `AI_STORE_PREDICTION_TTL_DAYS` (90) | expire |
| `generations` | generated text | `patient_id`, `encounter_id`, `text`, `sources` (`[{table, id}]`), `input_tokens`, `output_tokens`, `status`, `user_id` | `tenant_patient_created` | kept (manual purge below) |
| `agent_runs` | agentic request | `patient_id`, `question`, `steps` (tool, arguments, row count, elapsed), `answer`, `approved`, `status` (`completed`, or `stopped` when the step or token cap ended the run) | `tenant_patient_created` | kept |
| `eval_runs` | run of `evals/run_evals.py` | `golden_file_sha256`, `cases`, `passed`, `threshold`, `results` (one per case) | `tenant_created` on `{tenant_id: 1, created_at: -1}` | kept |

`ensure_indexes()` creates all of these from the API's lifespan, after the model is loaded. When
the store is not reachable at that moment (on a cluster the api Pods can start before `mongo-0`
answers), the attempt logs one warning and the first write that does reach the store creates
the indexes instead; `create_index` is idempotent, so trying again costs nothing. A TTL
index cannot be recreated with a different expiry, so when `AI_STORE_PREDICTION_TTL_DAYS` changes
the code adjusts the existing index with `collMod` instead. MongoDB's TTL monitor runs about once
a minute, so a document may outlive its deadline by that long.

## Who reads and writes

- `GET /api/patients/{id}/risk/readmission` writes a `predictions` document (`routers/risk.py`).
- `POST /api/patients/{id}/summary` writes a `generations` document on a successful answer
  (`routers/summary.py`); refusals and failures are metered in Postgres only.
- `GET /api/patients/{id}/ai/history?feature=readmission-risk&limit=5` (`routers/ai_history.py`)
  reads the newest documents for one patient and returns
  `{items: [{created_at, feature, model_version, probability, band, state, text_preview}], stored}`.
  It reads `predictions`, `generations` and `agent_runs`, so any feature name works as the
  filter; an agent run's `status` comes back as `state` and the start of its `answer` as
  `text_preview`.
  The Postgres patient lookup runs first, so another organisation's patient id is a 404 before
  the store is touched; the store query then filters on the caller's `tenant_id` as well.
- `evals/run_evals.py` writes an `eval_runs` document when `MONGO_URL` is set (skip with
  `--no-store`).
- `/health` reports `ai_store: ok` or `unavailable`.

Both responses that write carry `"stored": true|false`. The cards show "Earlier outputs" under
their result from the history route, and "history unavailable" when `stored` is false.

## Settings

| Variable | Compose | Cluster |
|---|---|---|
| `MONGO_URL` | built in `docker-compose.yml` from `MONGO_USER`, `MONGO_PASSWORD`, `AI_STORE_DB`: `mongodb://<user>:<password>@mongo:27017/atlas_ai?authSource=admin` (the backend and the worker both receive it, though only the API writes) | built in `api.yaml` only, from the Secret keys `MONGO_USER` and `MONGO_PASSWORD`; the worker never opens the store, so `worker.yaml` does not get them (least privilege) |
| `AI_STORE_DB` | `atlas_ai` (`.env`) | `atlas_ai` (ConfigMap) |
| `AI_STORE_PREDICTION_TTL_DAYS` | `90` (`.env`) | `90` (ConfigMap) |
| `MONGO_USER`, `MONGO_PASSWORD` | `.env` (dev placeholders in `.env.example`) | Secret `atlas-secrets` (template in `k8s/base/secret.example.env`) |

`authSource=admin` is there because the root user the image creates on first start lives in the
`admin` database, not in `atlas_ai`.

## Failure mode

A store that is down never breaks a route. The client is built lazily with a two-second server
selection timeout; every write is wrapped and returns `True` or `False`; the route puts that into
the response as `stored`. The warning is logged at most once a minute. `/health` keeps
`status: ok` (the clinical record is served) and adds `ai_store: unavailable`, and its ping has a
half-second budget so Kubernetes readiness is not held for the full timeout.

Writes are fire-and-forget: the Postgres metering row is written first, then the document. A
crash between the two loses the document, by design (no two-phase commit across stores). The two
are reconciled by `request_id`; the query is below.

The cost of an outage is visible: each risk or summary call waits the two-second timeout before
answering with `stored: false`, and the history list says "history unavailable".

Try it on Compose: `docker compose stop mongo`, open a patient record (the score still appears,
the footer says it was not saved), `curl -s localhost:8000/health`, then
`docker compose start mongo`.

## Five queries a reviewer can run

From your machine, with the Compose stack running (`mongosh` comes with MongoDB Shell; or use
`docker compose exec mongo mongosh ...` and drop the `localhost:27018` part):

```bash
mongosh "mongodb://atlas:atlas-dev-only@localhost:27018/atlas_ai?authSource=admin"
```

On the cluster, run each query as a one-shot command inside the `mongo-0` Pod, which already has
the user and password as environment variables, so you never type them:

```bash
kubectl -n atlas exec mongo-0 -- sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin atlas_ai --eval "db.predictions.countDocuments()"'
```

Put the query in place of `db.predictions.countDocuments()`, using single quotes inside it
(`db.predictions.find({feature: 'early-warning'}).limit(5)`). Do not open an interactive
`mongosh` inside the Pod: the shell is a Node.js process that shares the Pod's memory limit with
the database, and a long session can get `mongo-0` OOMKilled. For an interactive session, forward
the port and use the `mongosh` on your machine instead (the password is the `MONGO_PASSWORD` in
your `k8s/secret.env`):

```bash
kubectl -n atlas port-forward pod/mongo-0 27019:27017
mongosh "mongodb://atlas@localhost:27019/atlas_ai?authSource=admin"   # asks for the password
```

1. How many documents per tenant per feature, newest first:

   ```javascript
   db.predictions.aggregate([
     { $group: { _id: { tenant: "$tenant_id", feature: "$feature" }, n: { $sum: 1 }, last: { $max: "$created_at" } } },
     { $sort: { last: -1 } }
   ])
   ```

2. The last five scores for one patient (what the card's "Earlier outputs" shows):

   ```javascript
   db.predictions.find({ tenant_id: 1, patient_id: 1 }, { probability: 1, band: 1, state: 1, created_at: 1, model_version: 1 })
     .sort({ created_at: -1 }).limit(5)
   ```

3. Tenant isolation: no document without a tenant id, and the set of tenant ids in use:

   ```javascript
   db.predictions.countDocuments({ tenant_id: { $exists: false } })   // must be 0
   db.generations.distinct("tenant_id")
   ```

4. Reconcile a document with its Postgres usage row. Take a `request_id` from the store and
   look it up in Postgres:

   ```javascript
   db.generations.find({}, { request_id: 1, created_at: 1, input_tokens: 1 }).sort({ created_at: -1 }).limit(1)
   ```

   ```bash
   docker compose exec db psql -U atlas -d atlas -c "SELECT ts, feature, status, input_tokens, output_tokens FROM usage_events WHERE request_id = '<paste it>'"
   ```

   A usage row with no document means the write failed or the store was down at the time (the
   response said `stored: false`). A `SELECT count(*) FROM usage_events WHERE status = 'ok' AND
   feature = 'stay-summary'` against `db.generations.countDocuments({status: "ok"})` shows how
   many are missing in total.

5. The indexes, including the TTL (the `expireAfterSeconds` value is the retention in seconds):

   ```javascript
   db.predictions.getIndexes()
   db.serverStatus().metrics.ttl       // deletedDocuments counts what the TTL monitor removed
   ```

Manual purge of one tenant's generated text (the runbook step for a customer who leaves):

```javascript
db.generations.deleteMany({ tenant_id: 2 })
db.agent_runs.deleteMany({ tenant_id: 2 })
```

## Backup and restore

The AI history is backed up on its own, apart from the clinical record. `mongodump` writes a
folder of BSON files; `mongorestore` reads it back. Both tools come with MongoDB Database Tools,
and the `mongo:7` image contains them, which is what the commands below use.

Compose:

```bash
# back up to ./backups/ai_store-<date> on your machine
docker compose exec -T mongo mongodump --quiet -u atlas -p atlas-dev-only --authenticationDatabase admin \
  --db atlas_ai --archive > backups/ai_store-$(date +%F).archive

# restore it (--drop replaces the collections with the archive's contents)
docker compose exec -T mongo mongorestore --quiet -u atlas -p atlas-dev-only --authenticationDatabase admin \
  --drop --archive < backups/ai_store-2026-10-14.archive
```

The cluster, through `kubectl exec` into the `mongo-0` Pod (the user and password are the Secret's
`MONGO_USER` and `MONGO_PASSWORD`; the Pod has them as `MONGO_INITDB_ROOT_USERNAME` and
`MONGO_INITDB_ROOT_PASSWORD`, so the shell inside the Pod can read them without you typing them):

```bash
kubectl -n atlas exec mongo-0 -- sh -c 'mongodump --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db atlas_ai --archive' \
  > backups/ai_store-cluster-$(date +%F).archive

kubectl -n atlas exec -i mongo-0 -- sh -c 'mongorestore --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --drop --archive' \
  < backups/ai_store-cluster-2026-10-14.archive
```

The StatefulSet's disk (`mongo-data-mongo-0`) survives Pod restarts and `kubectl apply`, not
`kind delete cluster`. A backup taken on Compose restores into the cluster and the other way
round: same database name, same collections.

## Adding a feature that writes to the store

Call the matching `record_*()` right after `record_usage()`, passing the same `request_id`, and
put its return value in the response as `stored`. Give the card an `EarlierOutputs` list with
the feature name. Add two tests: the document appears with the caller's `tenant_id`, and the
other organisation's history is a 404. `docs/ADDING_A_FEATURE.md` has the full checklist.
