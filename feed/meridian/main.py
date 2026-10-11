"""Meridian Clinical Exchange: a mock health-data vendor that streams clinical events to Atlas.

    uvicorn meridian.main:app --port 8100

Everything under /v1 needs `Authorization: Bearer <MERIDIAN_API_KEY>`.
"""
from __future__ import annotations

import asyncio
import html
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, HttpUrl

from .config import PROFILES, settings
from .delivery import run_dispatcher
from .simulation import FacilityFeed
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("meridian")

store = Store(settings.data_dir)
feeds: dict[str, FacilityFeed] = {}
tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    for code in settings.facilities:
        if code not in PROFILES:
            log.warning("unknown facility code %s in FEED_FACILITIES, skipping", code)
            continue
        feeds[code] = FacilityFeed(store, PROFILES[code], settings)
        tasks.append(asyncio.create_task(feeds[code].run(), name=f"sim-{code}"))
    tasks.append(asyncio.create_task(run_dispatcher(store, settings), name="dispatcher"))
    log.info("Meridian up: epoch %s, facilities %s, cadence x%s", store.epoch, ", ".join(feeds), settings.cadence_scale)
    yield
    for task in tasks:
        task.cancel()
    for feed in feeds.values():
        feed.save()


app = FastAPI(
    title="Meridian Clinical Exchange (mock)",
    version="1.0.0",
    description="A fictional health-data network that streams admissions, vitals, labs and medication events to subscribers.",
    lifespan=lifespan,
)

bearer = HTTPBearer(auto_error=False)


def require_key(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    if credentials is None or credentials.credentials != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key", headers={"WWW-Authenticate": "Bearer"})


class SubscriptionIn(BaseModel):
    facility: str = Field(..., examples=["RGH"])
    callback_url: HttpUrl
    secret: str | None = Field(None, description="Shared secret for signing deliveries; generated if omitted")
    after_sequence: int = Field(0, ge=0, description="Start delivering after this sequence")


def _public(sub: dict) -> dict:
    out = {k: v for k, v in sub.items() if k != "secret"}
    out["epoch"] = store.epoch
    out["latest_sequence"] = store.latest_sequence(sub["facility"])
    out["lag"] = out["latest_sequence"] - sub["cursor"]
    return out


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "epoch": store.epoch, "facilities": {c: f.status for c, f in feeds.items()}}


@app.get("/v1/facilities", dependencies=[Depends(require_key)], tags=["facilities"])
def list_facilities() -> list[dict]:
    return [feed.describe() for feed in feeds.values()]


@app.get("/v1/facilities/{code}/events", dependencies=[Depends(require_key)], tags=["events"])
def replay(code: str, after_sequence: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)) -> dict:
    """The replay endpoint: events for one facility, in sequence order, after a given sequence."""
    if code not in feeds:
        raise HTTPException(status_code=404, detail="Unknown facility")
    events = store.events_after(code, after_sequence, limit)
    return {
        "facility": code,
        "epoch": store.epoch,
        "latest_sequence": store.latest_sequence(code),
        "events": events,
        "next_after_sequence": events[-1]["sequence"] if events else after_sequence,
    }


@app.post("/v1/subscriptions", dependencies=[Depends(require_key)], status_code=201, tags=["subscriptions"])
def subscribe(body: SubscriptionIn) -> dict:
    if body.facility not in feeds:
        raise HTTPException(status_code=404, detail="Unknown facility")
    sub = store.upsert_subscription(body.facility, str(body.callback_url), body.secret, body.after_sequence)
    log.info("subscription %s for %s -> %s from sequence %d", sub["id"], body.facility, body.callback_url, body.after_sequence)
    return _public(sub)


@app.get("/v1/subscriptions", dependencies=[Depends(require_key)], tags=["subscriptions"])
def subscriptions() -> list[dict]:
    return [_public(s) for s in store.subscriptions()]


def _sub_or_404(sub_id: str) -> dict:
    sub = store.subscription(sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Unknown subscription")
    return sub


@app.get("/v1/subscriptions/{sub_id}", dependencies=[Depends(require_key)], tags=["subscriptions"])
def get_subscription(sub_id: str) -> dict:
    return _public(_sub_or_404(sub_id))


@app.post("/v1/subscriptions/{sub_id}/pause", dependencies=[Depends(require_key)], tags=["subscriptions"])
def pause(sub_id: str) -> dict:
    _sub_or_404(sub_id)
    store.update_subscription(sub_id, status="paused")
    return _public(store.subscription(sub_id))


@app.post("/v1/subscriptions/{sub_id}/resume", dependencies=[Depends(require_key)], tags=["subscriptions"])
def resume(sub_id: str) -> dict:
    _sub_or_404(sub_id)
    store.update_subscription(sub_id, status="active", failures=0, next_attempt_at=0)
    return _public(store.subscription(sub_id))


@app.delete("/v1/subscriptions/{sub_id}", dependencies=[Depends(require_key)], status_code=204,
            response_class=Response, tags=["subscriptions"])
def unsubscribe(sub_id: str) -> Response:
    if not store.delete_subscription(sub_id):
        raise HTTPException(status_code=404, detail="Unknown subscription")
    return Response(status_code=204)


@app.get("/v1/subscriptions/{sub_id}/deliveries", dependencies=[Depends(require_key)], tags=["subscriptions"])
def deliveries(sub_id: str, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    _sub_or_404(sub_id)
    return store.deliveries(sub_id, limit)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def status_page() -> str:
    rows = "".join(
        f"<tr><td>{html.escape(f['code'])}</td><td>{html.escape(f['name'])}</td><td>{f['status']}</td>"
        f"<td>{f['latest_sequence']:,}</td><td>{f['active_encounters']}</td><td>{html.escape(f['simulated_until'][:19])}</td></tr>"
        for f in (feed.describe() for feed in feeds.values())
    )
    subs = "".join(
        f"<tr><td>{html.escape(s['id'])}</td><td>{html.escape(s['facility'])}</td><td>{html.escape(s['callback_url'])}</td>"
        f"<td>{s['status']}</td><td>{s['cursor']:,}</td><td>{html.escape(s['last_error'] or '')}</td></tr>"
        for s in store.subscriptions()
    )
    return f"""<!doctype html><html><head><title>Meridian Clinical Exchange</title><meta http-equiv="refresh" content="10">
<style>body{{font-family:system-ui,sans-serif;margin:40px;color:#1d2433;background:#f6f7f9}}h1{{font-size:22px}}
table{{border-collapse:collapse;background:#fff;margin:12px 0 28px;min-width:720px}}td,th{{border:1px solid #dde1e8;padding:6px 10px;text-align:left;font-size:13px}}
th{{background:#eef1f5}}small{{color:#667}}</style></head><body>
<h1>Meridian Clinical Exchange <small>mock vendor &middot; epoch {store.epoch} &middot; cadence x{settings.cadence_scale:g}</small></h1>
<h3>Facilities</h3><table><tr><th>Code</th><th>Name</th><th>Status</th><th>Latest sequence</th><th>In encounters now</th><th>Simulated until (UTC)</th></tr>{rows}</table>
<h3>Subscriptions</h3><table><tr><th>Id</th><th>Facility</th><th>Callback</th><th>Status</th><th>Cursor</th><th>Last error</th></tr>{subs}</table>
<p><a href="/docs">API docs</a></p></body></html>"""
