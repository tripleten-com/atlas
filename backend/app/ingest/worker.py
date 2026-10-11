"""The ingestion worker: turns stored Meridian events into clinical rows, and keeps the feed healthy.

    python -m app.ingest.worker

Two jobs, one loop:

  1. Apply. Claim a batch of pending events (FOR UPDATE SKIP LOCKED, so more than one worker
     is safe), apply each in its own savepoint, record the outcome, commit, and announce what
     changed on the `atlas_events` channel so open dashboards refresh.
  2. Maintain. Every RESYNC_CHECK_SECONDS, or right away when someone presses Resync: make sure
     each facility has a live Meridian subscription, and pull any range of events that
     webhooks missed from Meridian's replay endpoint.
"""
from __future__ import annotations

import json
import logging
import select
import signal
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import psycopg

from ..config import settings
from ..db import close_pool, connection, execute, fetch_all, fetch_one, open_pool
from ..migrate import wait_for_db
from .meridian import MeridianClient, MeridianError
from .processor import Deferred, Invalid, apply_event, is_abnormal
from .store import handle_epoch, store_events

log = logging.getLogger("atlas.worker")

MAX_ATTEMPTS = 8
RESYNC_PAGE = 500
RESYNC_MAX_PAGES = 40
STALE_DELIVERY_SECONDS = 90

_running = True


def _stop(*_):
    global _running
    _running = False


CONNECTION_SQL = """
SELECT c.*, f.code AS facility_code, f.name AS facility_name
FROM integration_connections c JOIN facilities f ON f.id = c.facility_id
"""


# ---------------------------------------------------------------- apply

def process_batch(limit: int) -> int:
    with connection() as conn:
        rows = fetch_all(
            conn,
            """SELECT ie.id, ie.connection_id, ie.payload, ie.attempts
               FROM ingest_events ie
               JOIN integration_connections c ON c.id = ie.connection_id
               WHERE ie.status IN ('pending', 'deferred') AND ie.next_attempt_at <= now() AND c.status <> 'paused'
               ORDER BY ie.connection_id, ie.sequence
               LIMIT %s
               FOR UPDATE OF ie SKIP LOCKED""",
            (limit,),
        )
        if not rows:
            return 0
        connections = {c["id"]: c for c in fetch_all(conn, CONNECTION_SQL + " WHERE c.id = ANY(%s)",
                                                      (list({r["connection_id"] for r in rows}),))}
        outcomes = Counter()
        changed: dict[int, dict] = defaultdict(lambda: {"counts": Counter(), "patients": set(), "abnormal": 0})
        for row in rows:
            conn_row = connections[row["connection_id"]]
            event = row["payload"]
            status, error, next_attempt = "processed", None, None
            try:
                with conn.transaction():  # a savepoint: a failing event rolls back only its own rows
                    applied = apply_event(conn, conn_row, event, row["id"])
                if not applied:
                    status, error = "skipped", f"no handler for {event.get('type')}"
            except Deferred as exc:
                attempts = row["attempts"] + 1
                if attempts >= MAX_ATTEMPTS:
                    status, error = "failed", f"gave up after {attempts} attempts: {exc}"
                else:
                    status, error = "deferred", str(exc)
                    next_attempt = min(300, 2 ** attempts)
            except Invalid as exc:
                status, error = "failed", str(exc)
            except psycopg.Error as exc:
                status, error = "failed", f"database error: {exc.__class__.__name__}: {str(exc)[:300]}"
            execute(
                conn,
                """UPDATE ingest_events
                   SET status = %s, error = %s, attempts = attempts + 1,
                       processed_at = CASE WHEN %s IN ('processed', 'skipped') THEN now() ELSE processed_at END,
                       next_attempt_at = now() + make_interval(secs => %s)
                   WHERE id = %s""",
                (status, error, status, next_attempt or 0, row["id"]),
            )
            outcomes[status] += 1
            if status == "processed":
                info = changed[conn_row["facility_id"]]
                info["tenant_id"] = conn_row["tenant_id"]
                info["counts"][event["type"]] += 1
                info["abnormal"] += is_abnormal(event)
                mrn = (event.get("patient") or {}).get("mrn")
                if mrn:
                    info["patients"].add(mrn)
        for facility_id, info in changed.items():
            patient_ids = [r["id"] for r in fetch_all(
                conn, "SELECT id FROM patients WHERE facility_id = %s AND mrn = ANY(%s)",
                (facility_id, list(info["patients"])[:50]))]
            payload = {
                "tenant_id": info["tenant_id"], "facility_id": facility_id, "counts": dict(info["counts"]),
                "abnormal": info["abnormal"], "patient_ids": patient_ids,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            execute(conn, "SELECT pg_notify('atlas_events', %s)", (json.dumps(payload),))
        log.info("applied batch of %d: %s", len(rows), dict(outcomes))
        return len(rows)


# ---------------------------------------------------------------- maintain

def ensure_subscription(client: MeridianClient, conn_row: dict) -> dict | None:
    """Return Meridian's view of this facility's subscription, creating it if needed."""
    remote = client.subscription(conn_row["subscription_id"]) if conn_row["subscription_id"] else None
    if remote is None:
        remote = client.subscribe(conn_row["facility_code"], settings.meridian_callback_url,
                                  conn_row["webhook_secret"], after_sequence=conn_row["high_watermark"])
        with connection() as conn:
            execute(conn, "UPDATE integration_connections SET subscription_id = %s, last_error = NULL WHERE id = %s",
                    (remote["id"], conn_row["id"]))
        log.info("subscribed %s to Meridian (subscription %s)", conn_row["facility_code"], remote["id"])
    if conn_row["status"] == "paused" and remote.get("status") != "paused":
        client.pause(remote["id"])
    elif conn_row["status"] != "paused" and remote.get("status") == "paused":
        client.resume(remote["id"])
    return remote


def resync(client: MeridianClient, connection_id: int) -> int:
    """Pull every event after our high watermark from the replay endpoint."""
    pulled = 0
    for _ in range(RESYNC_MAX_PAGES):
        with connection() as conn:
            conn_row = fetch_one(conn, CONNECTION_SQL + " WHERE c.id = %s", (connection_id,))
            page = client.events(conn_row["facility_code"], conn_row["high_watermark"], RESYNC_PAGE)
            if page.get("epoch") and page["epoch"] != conn_row["stream_epoch"]:
                conn_row = {**conn_row, **handle_epoch(conn, conn_row, page["epoch"])}
                conn.commit()
                continue  # start again from sequence 0 in the new epoch
            events = page.get("events", [])
            if not events:
                execute(conn, """UPDATE integration_connections
                                 SET needs_resync = false, last_resync_at = now(), last_error = NULL WHERE id = %s""",
                        (connection_id,))
                return pulled
            store_events(conn, conn_row, events, via="resync")
            pulled += len(events)
            if page.get("latest_sequence", 0) <= events[-1]["sequence"]:
                execute(conn, """UPDATE integration_connections
                                 SET needs_resync = false, last_resync_at = now(), last_error = NULL WHERE id = %s""",
                        (connection_id,))
                return pulled
    return pulled


def maintain(client: MeridianClient) -> None:
    with connection() as conn:
        rows = fetch_all(conn, CONNECTION_SQL + " ORDER BY c.id")
    for conn_row in rows:
        try:
            remote = ensure_subscription(client, conn_row)
            if conn_row["status"] == "paused" or remote is None:
                continue
            behind = remote.get("latest_sequence", 0) - conn_row["high_watermark"]
            delivery_age = (
                (datetime.now(timezone.utc) - conn_row["last_delivery_at"]).total_seconds()
                if conn_row["last_delivery_at"] else None
            )
            epoch_changed = conn_row["stream_epoch"] and remote.get("epoch") and remote["epoch"] != conn_row["stream_epoch"]
            stalled = behind > 0 and (delivery_age is None or delivery_age > STALE_DELIVERY_SECONDS) and conn_row["high_watermark"] > 0
            if conn_row["needs_resync"] or epoch_changed or stalled:
                pulled = resync(client, conn_row["id"])
                log.info("resync %s pulled %d events", conn_row["facility_code"], pulled)
        except MeridianError as exc:
            log.warning("Meridian problem for %s: %s", conn_row["facility_code"], exc)
            with connection() as conn:
                execute(conn, "UPDATE integration_connections SET last_error = %s WHERE id = %s", (str(exc), conn_row["id"]))


# ---------------------------------------------------------------- main loop

def wait_for_schema() -> None:
    while _running:
        try:
            with psycopg.connect(settings.database_url) as conn:
                n = conn.execute("SELECT count(*) FROM integration_connections").fetchone()[0]
                if n:
                    return
        except psycopg.Error:
            pass
        log.info("waiting for the backend to create the schema and seed the organisations")
        time.sleep(3)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    wait_for_db()
    wait_for_schema()
    open_pool()
    client = MeridianClient()
    listener = psycopg.connect(settings.database_url, autocommit=True)
    listener.execute("LISTEN atlas_ingest")
    listener.execute("LISTEN atlas_maintain")
    log.info("worker started; Meridian at %s, callback %s", settings.meridian_base_url, settings.meridian_callback_url)
    next_maintenance = 0.0
    try:
        while _running:
            wake_maintenance = False
            if time.monotonic() >= next_maintenance:
                maintain(client)
                next_maintenance = time.monotonic() + settings.resync_check_seconds
            handled = process_batch(settings.worker_batch_size)
            if handled:
                continue
            # idle: sleep until a notification arrives or the poll interval passes
            if select.select([listener.fileno()], [], [], settings.worker_poll_seconds) != ([], [], []):
                for note in listener.notifies(timeout=0):
                    if note.channel == "atlas_maintain":
                        wake_maintenance = True
            if wake_maintenance:
                next_maintenance = 0.0
    finally:
        listener.close()
        client.close()
        close_pool()
        log.info("worker stopped")


if __name__ == "__main__":
    main()
    sys.exit(0)
