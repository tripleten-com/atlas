"""Pushes new events to every active subscription, in order, signed, with retries.

A delivery is a POST of up to FEED_BATCH_SIZE events. A 2xx moves the subscription's cursor past
them. Anything else backs off (1s, 2s, 4s... up to a minute) and retries the same batch, so a
subscriber that was down gets everything once it is back.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import secrets
import time

import httpx

from .config import Settings
from .store import Store, now_iso

log = logging.getLogger("meridian.delivery")


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


async def deliver_once(client: httpx.AsyncClient, store: Store, sub: dict, settings: Settings, chaos: random.Random) -> bool:
    """Try one batch for one subscription. Returns True if a full batch went out (more may be waiting)."""
    events = store.events_after(sub["facility"], sub["cursor"], settings.batch_size)
    if not events:
        return False
    if sub["cursor"] > 0 and chaos.random() < settings.duplicate_rate:
        events = store.events_after(sub["facility"], sub["cursor"] - 1, 1) + events  # redeliver one we already sent
    delivery_id = "dlv_" + secrets.token_hex(8)
    body = json.dumps({
        "delivery_id": delivery_id,
        "subscription_id": sub["id"],
        "facility": sub["facility"],
        "epoch": store.epoch,
        "events": events,
    }).encode()
    started = time.monotonic()
    status_code, error = None, None
    try:
        response = await client.post(
            sub["callback_url"], content=body,
            headers={"Content-Type": "application/json", "X-Meridian-Signature": sign(sub["secret"], body),
                     "X-Meridian-Delivery": delivery_id, "User-Agent": "Meridian-Webhooks/1.0"},
        )
        status_code = response.status_code
        if response.is_success:
            store.update_subscription(sub["id"], cursor=events[-1]["sequence"], failures=0, next_attempt_at=0,
                                      last_error=None, last_delivered_at=now_iso())
        else:
            error = f"HTTP {status_code}: {response.text[:200]}"
    except httpx.HTTPError as exc:
        error = f"{exc.__class__.__name__}: {exc}"[:300]
    elapsed = int((time.monotonic() - started) * 1000)
    store.log_delivery(sub["id"], events[0]["sequence"], events[-1]["sequence"], len(events), status_code, error, elapsed)
    if error:
        failures = sub["failures"] + 1
        store.update_subscription(sub["id"], failures=failures, last_error=error,
                                  next_attempt_at=time.time() + min(60, 2 ** min(failures, 6)))
        if failures in (1, 5) or failures % 20 == 0:
            log.warning("delivery to %s failed (%d in a row): %s", sub["callback_url"], failures, error)
        return False
    return len(events) >= settings.batch_size


async def run_dispatcher(store: Store, settings: Settings) -> None:
    chaos = random.Random(settings.random_seed + 99)
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            busy = False
            for sub in store.subscriptions():
                if sub["status"] != "active" or sub["next_attempt_at"] > time.time():
                    continue
                try:
                    busy |= await deliver_once(client, store, sub, settings, chaos)
                except Exception:  # keep delivering to everyone else
                    log.exception("unexpected delivery error for %s", sub["id"])
            await asyncio.sleep(0.05 if busy else 1.0)
