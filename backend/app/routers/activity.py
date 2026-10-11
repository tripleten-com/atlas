"""What just happened: a feed of recently applied clinical events, and a live stream of them.

The live stream is Server-Sent Events over a plain GET. The frontend reads it with fetch() and
a ReadableStream (not EventSource), because it needs to send the Authorization header.
"""
from __future__ import annotations

import asyncio
import json

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from psycopg import Connection

from ..auth import CurrentUser, _unauthorized, bearer, get_current_user, user_from_token
from ..config import settings
from ..db import connection, fetch_all, get_db
from ..ingest.processor import is_abnormal, summarize
from ..schemas import ActivityItem
from ..tenancy import check_facility, facility_clause

router = APIRouter(tags=["activity"])


@router.get("/api/activity", response_model=list[ActivityItem])
def recent_activity(
    facility_id: int | None = None,
    patient_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> list[ActivityItem]:
    """Most recently received events from the Meridian feed that Atlas has applied, newest first."""
    check_facility(conn, user, facility_id)
    params = {"t": user.tenant_id, "facility_id": facility_id, "limit": limit, "patient_id": patient_id}
    patient_filter = " AND p.id = %(patient_id)s" if patient_id else ""
    rows = fetch_all(
        conn,
        f"""SELECT ie.id, ie.occurred_at AS at, ie.event_type, ie.payload, c.facility_id, f.name AS facility_name,
                   p.id AS patient_id, p.name AS patient_name
            FROM ingest_events ie
            JOIN integration_connections c ON c.id = ie.connection_id
            JOIN facilities f ON f.id = c.facility_id
            LEFT JOIN patients p ON p.facility_id = c.facility_id AND p.mrn = ie.payload -> 'patient' ->> 'mrn'
            WHERE ie.tenant_id = %(t)s AND ie.status = 'processed' {facility_clause('c', facility_id)} {patient_filter}
            ORDER BY ie.id DESC
            LIMIT %(limit)s""",
        params,
    )
    return [
        ActivityItem(**{k: v for k, v in r.items() if k != "payload"}, summary=summarize(r["payload"]),
                     abnormal=is_abnormal(r["payload"]))
        for r in rows
    ]


async def _stream(user: CurrentUser, facility_id: int | None, request: Request):
    aconn = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True)
    try:
        await aconn.execute("LISTEN atlas_events")
        yield "retry: 5000\n\n"
        yield f"event: ready\ndata: {json.dumps({'tenant_id': user.tenant_id, 'facility_id': facility_id})}\n\n"
        while True:
            async for note in aconn.notifies(timeout=15.0):
                try:
                    payload = json.loads(note.payload)
                except ValueError:
                    continue
                if payload.get("tenant_id") != user.tenant_id:
                    continue  # never leak another customer's activity
                if facility_id is not None and payload.get("facility_id") != facility_id:
                    continue
                yield f"event: ingest\ndata: {json.dumps(payload)}\n\n"
            if await request.is_disconnected():
                break
            yield ": keep-alive\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        await aconn.close()


@router.get("/api/activity/stream")
async def activity_stream(
    request: Request,
    facility_id: int | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> StreamingResponse:
    """Server-Sent Events: one `ingest` event each time the worker applies a batch for this tenant."""
    if credentials is None:
        raise _unauthorized()
    # Authenticate with a short-lived pooled connection, so the stream does not hold one open.
    with connection() as conn:
        user = user_from_token(credentials.credentials, conn)
        check_facility(conn, user, facility_id)
    return StreamingResponse(
        _stream(user, facility_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
