"""The Meridian data feed from Atlas's side: health of each connection, the ingestion log,
admin controls, and the webhook endpoint Meridian delivers to."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from psycopg import Connection

from ..auth import CurrentUser, get_current_user, require_admin
from ..db import connection as db_connection
from ..db import execute, fetch_all, fetch_one, get_db
from ..ingest.meridian import (
    SIGNATURE_HEADER,
    MeridianClient,
    MeridianError,
    verify_signature,
)
from ..ingest.processor import summarize
from ..ingest.store import handle_epoch, store_events
from ..schemas import ConnectionOut, HourlyVolume, IngestEventOut, Page
from ..tenancy import audit, not_found

router = APIRouter(tags=["integrations"])

CONNECTIONS_SQL = """
SELECT c.id, c.facility_id, f.code AS facility_code, f.name AS facility_name, c.vendor, c.status, c.subscription_id,
       c.stream_epoch, c.high_watermark, c.last_event_at, c.last_delivery_at, c.last_resync_at, c.needs_resync, c.last_error,
       (SELECT count(*) FROM ingest_events ie WHERE ie.connection_id = c.id AND ie.received_at > now() - interval '24 hours') AS events_24h,
       (SELECT count(*) FROM ingest_events ie WHERE ie.connection_id = c.id AND ie.status = 'pending') AS pending,
       (SELECT count(*) FROM ingest_events ie WHERE ie.connection_id = c.id AND ie.status = 'deferred') AS deferred,
       (SELECT count(*) FROM ingest_events ie WHERE ie.connection_id = c.id AND ie.status = 'failed') AS failed
FROM integration_connections c JOIN facilities f ON f.id = c.facility_id
"""


def _connection(conn: Connection, user: CurrentUser, connection_id: int) -> dict:
    row = fetch_one(conn, CONNECTIONS_SQL + " WHERE c.id = %s AND c.tenant_id = %s", (connection_id, user.tenant_id))
    if row is None:
        raise not_found("Connection not found")
    return row


@router.get("/api/integrations", response_model=list[ConnectionOut])
def list_connections(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> list[ConnectionOut]:
    rows = fetch_all(conn, CONNECTIONS_SQL + " WHERE c.tenant_id = %s ORDER BY f.name", (user.tenant_id,))
    return [ConnectionOut(**r) for r in rows]


@router.get("/api/integrations/{connection_id}", response_model=ConnectionOut)
def get_connection(connection_id: int, user: CurrentUser = Depends(get_current_user),
                   conn: Connection = Depends(get_db)) -> ConnectionOut:
    return ConnectionOut(**_connection(conn, user, connection_id))


@router.get("/api/integrations/{connection_id}/volume", response_model=list[HourlyVolume])
def volume(connection_id: int, hours: int = Query(24, ge=1, le=24 * 14), user: CurrentUser = Depends(get_current_user),
           conn: Connection = Depends(get_db)) -> list[HourlyVolume]:
    """Events received per hour and type, by the time Meridian says they happened."""
    _connection(conn, user, connection_id)
    rows = fetch_all(
        conn,
        """SELECT date_trunc('hour', occurred_at) AS hour, event_type, count(*)
           FROM ingest_events WHERE connection_id = %s AND occurred_at > now() - make_interval(hours => %s) AND occurred_at <= now()
           GROUP BY 1, 2 ORDER BY 1""",
        (connection_id, hours),
    )
    return [HourlyVolume(**r) for r in rows]


@router.get("/api/integrations/{connection_id}/events", response_model=Page[IngestEventOut])
def ingest_log(
    connection_id: int,
    status: Literal["any", "pending", "processed", "deferred", "failed", "skipped"] = "any",
    event_type: str | None = None,
    include_payload: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> Page[IngestEventOut]:
    _connection(conn, user, connection_id)
    where = "connection_id = %(c)s"
    params: dict = {"c": connection_id, "limit": page_size, "offset": (page - 1) * page_size}
    if status != "any":
        where += " AND status = %(s)s"
        params["s"] = status
    if event_type:
        where += " AND event_type = %(t)s"
        params["t"] = event_type
    total = fetch_one(conn, f"SELECT count(*) AS n FROM ingest_events WHERE {where}", params)["n"]
    rows = fetch_all(
        conn,
        f"""SELECT id, external_id, sequence, event_type, occurred_at, received_at, received_via, status, attempts,
                   processed_at, error, payload
            FROM ingest_events WHERE {where}
            ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    )
    items = []
    for r in rows:
        payload = r.pop("payload")
        items.append(IngestEventOut(**r, summary=summarize(payload), payload=payload if include_payload else None))
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/api/integrations/{connection_id}/resync")
def request_resync(connection_id: int, user: CurrentUser = Depends(require_admin), conn: Connection = Depends(get_db)) -> dict:
    """Ask the worker to pull anything Meridian holds past our high watermark."""
    _connection(conn, user, connection_id)
    execute(conn, "UPDATE integration_connections SET needs_resync = true WHERE id = %s", (connection_id,))
    execute(conn, "SELECT pg_notify('atlas_maintain', %s)", (str(connection_id),))
    audit(conn, user, "integration.resync", f"connection:{connection_id}")
    return {"status": "queued"}


def _set_status(conn: Connection, user: CurrentUser, connection_id: int, status: str) -> dict:
    row = _connection(conn, user, connection_id)
    if row["subscription_id"]:
        client = MeridianClient()
        try:
            (client.pause if status == "paused" else client.resume)(row["subscription_id"])
        except MeridianError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            client.close()
    execute(conn, "UPDATE integration_connections SET status = %s WHERE id = %s", (status, connection_id))
    audit(conn, user, f"integration.{'pause' if status == 'paused' else 'resume'}", f"connection:{connection_id}")
    return {"status": status}


@router.post("/api/integrations/{connection_id}/pause")
def pause(connection_id: int, user: CurrentUser = Depends(require_admin), conn: Connection = Depends(get_db)) -> dict:
    return _set_status(conn, user, connection_id, "paused")


@router.post("/api/integrations/{connection_id}/resume")
def resume(connection_id: int, user: CurrentUser = Depends(require_admin), conn: Connection = Depends(get_db)) -> dict:
    return _set_status(conn, user, connection_id, "active")


@router.post("/api/integrations/{connection_id}/retry-failed")
def retry_failed(connection_id: int, user: CurrentUser = Depends(require_admin), conn: Connection = Depends(get_db)) -> dict:
    _connection(conn, user, connection_id)
    n = execute(
        conn,
        """UPDATE ingest_events SET status = 'pending', attempts = 0, next_attempt_at = now(), error = NULL
           WHERE connection_id = %s AND status = 'failed'""",
        (connection_id,),
    )
    execute(conn, "SELECT pg_notify('atlas_ingest', %s)", (str(connection_id),))
    audit(conn, user, "integration.retry_failed", f"connection:{connection_id}", {"events": n})
    return {"requeued": n}


@router.post("/api/integrations/webhooks/meridian", status_code=202, tags=["webhooks"])
async def meridian_webhook(request: Request) -> dict:
    """Where Meridian delivers events. Public, but every delivery must carry a valid signature."""
    body = await request.body()
    try:
        delivery = json.loads(body)
        subscription_id = delivery["subscription_id"]
        events = delivery["events"]
        epoch = delivery["epoch"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Malformed delivery") from None
    signature = request.headers.get(SIGNATURE_HEADER)

    def _store() -> dict:
        with db_connection() as conn:
            row = fetch_one(conn, "SELECT * FROM integration_connections WHERE subscription_id = %s", (subscription_id,))
            if row is None or not verify_signature(row["webhook_secret"], body, signature):
                # Same answer for an unknown subscription and a bad signature.
                raise HTTPException(status_code=401, detail="Invalid signature")
            row = handle_epoch(conn, row, epoch)
            return store_events(conn, row, events, via="webhook")

    result = await run_in_threadpool(_store)
    return {"delivery_id": delivery.get("delivery_id"), **result}
