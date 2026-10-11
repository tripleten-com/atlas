"""Talking to Meridian Clinical Exchange, the (fictional) vendor that streams clinical events.

Meridian is the upstream system: hospitals' EHRs send it admissions, vitals, lab results and
medication activity, and Meridian forwards them to subscribers like Atlas. The contract:

  * Atlas subscribes once per facility with a callback URL and a shared secret.
  * Meridian POSTs batches of events to the callback, each signed with that secret.
  * Every event has a per-facility `sequence`. Atlas tracks the highest sequence it holds with
    no gaps below it (the high watermark). If a delivery skips ahead, Atlas pulls the missing
    range from the replay endpoint.
  * Every event also carries the stream `epoch`. A new epoch means Meridian's log was reset
    and sequences start again from 1.

See docs/DATA_FEED.md for the payloads.
"""
from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from ..config import settings

SIGNATURE_HEADER = "X-Meridian-Signature"
SIGNATURE_TOLERANCE_SECONDS = 300


class MeridianError(RuntimeError):
    pass


class MeridianClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 15.0):
        self._client = httpx.Client(
            base_url=base_url or settings.meridian_base_url,
            headers={"Authorization": f"Bearer {api_key or settings.meridian_api_key}"},
            timeout=timeout,
        )

    def _request(self, method: str, path: str, **kwargs) -> dict | list | None:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MeridianError(f"Meridian unreachable: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise MeridianError(f"Meridian returned {response.status_code}: {response.text[:200]}")
        return response.json() if response.content else {}

    def facilities(self) -> list[dict]:
        return self._request("GET", "/v1/facilities") or []

    def subscribe(self, facility: str, callback_url: str, secret: str, after_sequence: int = 0) -> dict:
        return self._request("POST", "/v1/subscriptions", json={
            "facility": facility, "callback_url": callback_url, "secret": secret, "after_sequence": after_sequence,
        })

    def subscription(self, subscription_id: str) -> dict | None:
        return self._request("GET", f"/v1/subscriptions/{subscription_id}")

    def pause(self, subscription_id: str) -> dict | None:
        return self._request("POST", f"/v1/subscriptions/{subscription_id}/pause")

    def resume(self, subscription_id: str) -> dict | None:
        return self._request("POST", f"/v1/subscriptions/{subscription_id}/resume")

    def events(self, facility: str, after_sequence: int, limit: int = 500) -> dict:
        result = self._request("GET", f"/v1/facilities/{facility}/events",
                               params={"after_sequence": after_sequence, "limit": limit})
        if result is None:
            raise MeridianError(f"Meridian does not know facility {facility}")
        return result

    def close(self) -> None:
        self._client.close()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """The signature header value Meridian sends: t=<unix seconds>,v1=<hex HMAC-SHA256 of 't.body'>."""
    ts = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(secret: str, body: bytes, header: str | None, now: float | None = None) -> bool:
    if not header:
        return False
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        ts = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False  # a replayed old delivery
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
