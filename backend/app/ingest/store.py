"""Writing raw events into ingest_events, and keeping the connection's watermark honest.

Both entry points use this: the webhook receiver (events pushed by Meridian) and the resync
job (events pulled from Meridian's replay endpoint). Storing is idempotent: the same Meridian
event id stored twice is one row.
"""
from __future__ import annotations

from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..db import execute, fetch_one


def handle_epoch(conn: Connection, connection: dict, epoch: str) -> dict:
    """If Meridian's log was reset, start counting again and close visits the old log left open."""
    if connection["stream_epoch"] == epoch:
        return connection
    if connection["stream_epoch"] is not None:
        execute(
            conn,
            """UPDATE encounters SET discharge_date = now(), discharge_disposition = 'unknown'
               WHERE facility_id = %s AND discharge_date IS NULL""",
            (connection["facility_id"],),
        )
    execute(
        conn,
        "UPDATE integration_connections SET stream_epoch = %s, high_watermark = 0, needs_resync = false WHERE id = %s",
        (epoch, connection["id"]),
    )
    return {**connection, "stream_epoch": epoch, "high_watermark": 0}


def store_events(conn: Connection, connection: dict, events: list[dict], via: str) -> dict:
    """Insert events, advance the watermark, flag gaps. Returns counts for the caller to report."""
    if not events:
        return {"received": 0, "stored": 0, "duplicates": 0, "gap": False}
    stored = 0
    with conn.cursor() as cur:
        for ev in events:
            cur.execute(
                """INSERT INTO ingest_events
                     (tenant_id, connection_id, external_id, epoch, sequence, event_type, occurred_at, received_via, payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (connection_id, external_id) DO NOTHING""",
                (connection["tenant_id"], connection["id"], ev["id"], ev["epoch"], ev["sequence"], ev["type"],
                 datetime.fromisoformat(ev["occurred_at"]), via, Jsonb(ev)),
            )
            stored += cur.rowcount

    # Advance the watermark over any unbroken run of sequences we now hold.
    row = fetch_one(
        conn,
        """WITH held AS (
             SELECT DISTINCT sequence FROM ingest_events
             WHERE connection_id = %(c)s AND epoch = %(epoch)s AND sequence > %(hw)s AND sequence <= %(top)s),
           numbered AS (SELECT sequence, sequence - row_number() OVER (ORDER BY sequence) AS grp FROM held)
           SELECT max(sequence) AS new_hw FROM numbered WHERE grp = %(hw)s""",
        {"c": connection["id"], "epoch": connection["stream_epoch"], "hw": connection["high_watermark"],
         "top": max(e["sequence"] for e in events)},
    )
    new_hw = row["new_hw"] or connection["high_watermark"]
    top = max(e["sequence"] for e in events)
    gap = top > new_hw
    execute(
        conn,
        """UPDATE integration_connections
           SET high_watermark = %s, needs_resync = needs_resync OR %s,
               last_event_at = greatest(last_event_at, %s),
               last_delivery_at = CASE WHEN %s = 'webhook' THEN now() ELSE last_delivery_at END,
               last_error = CASE WHEN %s THEN last_error ELSE NULL END,
               status = CASE WHEN status = 'error' THEN 'active' ELSE status END
           WHERE id = %s""",
        (new_hw, gap, max(datetime.fromisoformat(e["occurred_at"]) for e in events), via, gap, connection["id"]),
    )
    execute(conn, "SELECT pg_notify('atlas_ingest', %s)", (str(connection["id"]),))
    return {"received": len(events), "stored": stored, "duplicates": len(events) - stored, "gap": gap,
            "high_watermark": new_hw}
