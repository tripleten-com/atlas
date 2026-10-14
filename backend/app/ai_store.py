"""The document store for AI outputs: MongoDB, database `atlas_ai`, four collections.

    from .. import ai_store

    stored = ai_store.record_prediction(user.tenant_id, request_id, feature="readmission-risk", ...)
    items, ok = ai_store.history(user.tenant_id, patient_id, feature="readmission-risk", limit=5)

Why a second store: the clinical record is relational and stays in PostgreSQL. AI outputs have a
shape that changes with every feature and model version, are append-only, and have their own
retention (scores expire, generated text is kept), so they go in documents. The two stores are
tied together by `request_id`: the `usage_events` row in Postgres and the document here carry the
same one, so a single request can be followed across both.

Collections and what each document carries (every document also has `tenant_id`, `created_at`,
`feature`, `model_version`, `request_id`):

    predictions   patient_id, encounter_id, probability, band, alert, inputs, state      expire (TTL)
    generations   patient_id, encounter_id, text, sources, input_tokens, output_tokens, status, user_id
    agent_runs    patient_id, question, steps, answer, approved
    eval_runs     golden_file_sha256, cases, passed, threshold, results

The rule that keeps the store safe: a store that is down never breaks a route. Every write is
wrapped and returns True or False; the route puts that into the response as `stored`. Every read
filters by the caller's `tenant_id` first, the same rule `tenancy.py` applies to SQL.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pymongo
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from .config import settings

log = logging.getLogger("atlas.ai_store")

PREDICTIONS = "predictions"
GENERATIONS = "generations"
AGENT_RUNS = "agent_runs"
EVAL_RUNS = "eval_runs"

# How long a call waits for a server before giving up. Two seconds is long enough for a healthy
# store on a laptop and short enough that a clinician waiting on a risk score barely notices.
SERVER_SELECTION_TIMEOUT_MS = 2000
# /health is polled every few seconds by Docker and Kubernetes, so it gets a tighter budget.
HEALTH_PING_SECONDS = 0.5
# A store that is down would otherwise log the same warning on every request.
WARN_EVERY_SECONDS = 60
# How much of a generated text the history list shows.
PREVIEW_CHARS = 160

_client: MongoClient | None = None
_last_warning_at: float = 0.0
# Whether ensure_indexes() has succeeded since the client was built. On a cluster the api Pods
# can start before mongo-0 is ready, so the lifespan attempt may fail; the first write that does
# reach the store then creates the indexes instead (see _insert).
_indexes_ready: bool = False


# ---------------------------------------------------------------- the client

def _mongo_url() -> str:
    """Read the URL through a function so a test can point the store at a closed port."""
    return settings.mongo_url


def client() -> MongoClient:
    """Build the client once and reuse it. Building it never touches the network: pymongo
    connects lazily, so a store that is down costs nothing until the first read or write."""
    global _client
    if _client is None:
        _client = MongoClient(_mongo_url(), serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS, tz_aware=True)
    return _client


def db():
    return client()[settings.ai_store_db]


def reset() -> None:
    """Drop the cached client (tests use this after changing the URL)."""
    global _client, _last_warning_at, _indexes_ready
    if _client is not None:
        _client.close()
    _client = None
    _last_warning_at = 0.0
    _indexes_ready = False


def _warn(message: str, exc: Exception | None = None) -> None:
    """Log a warning, but at most once a minute, so a store outage does not flood the log."""
    global _last_warning_at
    now = time.monotonic()
    if _last_warning_at and now - _last_warning_at < WARN_EVERY_SECONDS:
        return
    _last_warning_at = now
    log.warning("%s (%s)", message, exc.__class__.__name__ if exc else "no store")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- indexes

def ensure_indexes() -> bool:
    """Create the indexes, including the TTL that expires predictions. Called from the lifespan
    after load_model(); when the store is down it logs a warning and the API still starts, and
    the first successful write tries again (create_index does nothing when the index exists)."""
    global _indexes_ready
    ttl_seconds = settings.ai_store_prediction_ttl_days * 86400
    try:
        for name in (PREDICTIONS, GENERATIONS, AGENT_RUNS):
            db()[name].create_index([("tenant_id", 1), ("patient_id", 1), ("created_at", -1)], name="tenant_patient_created")
        db()[EVAL_RUNS].create_index([("tenant_id", 1), ("created_at", -1)], name="tenant_created")
        _ensure_ttl(PREDICTIONS, ttl_seconds)
        _indexes_ready = True
        return True
    except Exception as exc:  # any pymongo error: unreachable, wrong password, ...
        _warn("ai_store indexes not created, the store is unavailable", exc)
        return False


def _ensure_ttl(collection: str, ttl_seconds: int) -> None:
    """A TTL index cannot be re-created with a different expiry; change it with collMod instead,
    so changing AI_STORE_PREDICTION_TTL_DAYS later just works."""
    try:
        db()[collection].create_index("created_at", name="created_at_ttl", expireAfterSeconds=ttl_seconds)
    except OperationFailure as exc:
        if exc.code != 85:  # IndexOptionsConflict: same index, different expiry
            raise
        db().command("collMod", collection, index={"name": "created_at_ttl", "expireAfterSeconds": ttl_seconds})
        log.info("ai_store TTL on %s changed to %d days", collection, ttl_seconds // 86400)


# ---------------------------------------------------------------- writes

def _insert(collection: str, document: dict[str, Any]) -> bool:
    """One wrapped write. True when the document is in the store, False when it is not; never raises."""
    try:
        db()[collection].insert_one(document)
    except Exception as exc:
        _warn(f"ai_store write to {collection} failed, the route answered without it", exc)
        return False
    if not _indexes_ready:
        # The store was down when the API started; it is up now, so create the indexes it missed.
        # After the insert, not before, so a store that is still down costs one timeout, not two.
        ensure_indexes()
    return True


def _base(tenant_id: int, request_id: str, feature: str, model_version: str) -> dict[str, Any]:
    """The fields every document carries."""
    return {
        "tenant_id": tenant_id,
        "created_at": _now(),
        "feature": feature,
        "model_version": model_version,
        "request_id": request_id,
    }


def record_prediction(
    tenant_id: int,
    request_id: str,
    feature: str,
    model_version: str,
    patient_id: int,
    encounter_id: int | None,
    probability: float | None,
    band: str | None,
    inputs: dict[str, Any] | None = None,
    alert: bool | None = None,
    state: str | None = None,
) -> bool:
    """One scored request. `inputs` is the feature row (or the window summary) the model read;
    `state` is the honest-UI state ("scored", "insufficient_data", "deteriorating", ...)."""
    doc = _base(tenant_id, request_id, feature, model_version)
    doc.update(
        patient_id=patient_id, encounter_id=encounter_id, probability=probability, band=band,
        alert=alert, inputs=_plain(inputs or {}), state=state,
    )
    return _insert(PREDICTIONS, doc)


def record_generation(
    tenant_id: int,
    request_id: str,
    feature: str,
    model_version: str,
    patient_id: int,
    encounter_id: int | None,
    text: str,
    sources: list[dict[str, Any]],
    input_tokens: int,
    output_tokens: int,
    status: str,
    user_id: int | None,
) -> bool:
    """One generated text (a summary, a draft, a triage note) and the rows it was grounded in."""
    doc = _base(tenant_id, request_id, feature, model_version)
    doc.update(
        patient_id=patient_id, encounter_id=encounter_id, text=text, sources=sources,
        input_tokens=input_tokens, output_tokens=output_tokens, status=status, user_id=user_id,
    )
    return _insert(GENERATIONS, doc)


def record_agent_run(
    tenant_id: int,
    request_id: str,
    feature: str,
    model_version: str,
    patient_id: int | None,
    question: str,
    steps: list[dict[str, Any]],
    answer: str,
    approved: bool | None = None,
) -> bool:
    """One agentic request: the question, every tool step (tool, arguments, row count, elapsed),
    the answer, and whether a person approved it."""
    doc = _base(tenant_id, request_id, feature, model_version)
    doc.update(patient_id=patient_id, question=question, steps=steps, answer=answer, approved=approved)
    return _insert(AGENT_RUNS, doc)


def record_eval_run(
    tenant_id: int,
    request_id: str,
    feature: str,
    model_version: str,
    golden_file_sha256: str,
    cases: int,
    passed: int,
    threshold: float | None,
    results: list[dict[str, Any]],
) -> bool:
    """One run of evals/run_evals.py, so pass rates over time can be read back later."""
    doc = _base(tenant_id, request_id, feature, model_version)
    doc.update(golden_file_sha256=golden_file_sha256, cases=cases, passed=passed, threshold=threshold, results=results)
    return _insert(EVAL_RUNS, doc)


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    """BSON knows fewer types than Python: numpy numbers and Decimals become plain values, NaN
    becomes null, and anything else unusual becomes its string."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if hasattr(value, "item"):  # a numpy scalar
            value = value.item()
        if isinstance(value, Decimal):  # Postgres numeric columns
            value = float(value)
        if isinstance(value, float) and value != value:  # NaN is not JSON and confuses readers
            value = None
        if value is not None and not isinstance(value, (str, int, float, bool, list, dict, datetime)):
            value = str(value)
        out[key] = value
    return out


# ---------------------------------------------------------------- reads

def history(tenant_id: int, patient_id: int, feature: str | None = None, limit: int = 5) -> tuple[list[dict], bool]:
    """The newest outputs for one patient, newest first, from predictions and generations.

    Returns (items, ok). `ok` is False when the store could not be read, and the items are then
    empty; the route passes it on as `stored` so the card can say "history unavailable".
    The tenant filter comes first and is never optional: the caller has already checked the
    patient belongs to this tenant in Postgres, and the query repeats the check anyway.
    """
    query: dict[str, Any] = {"tenant_id": tenant_id, "patient_id": patient_id}
    if feature:
        query["feature"] = feature
    try:
        docs = []
        for name in (PREDICTIONS, GENERATIONS):
            docs += list(db()[name].find(query).sort("created_at", -1).limit(limit))
    except Exception as exc:
        _warn("ai_store read failed, history unavailable", exc)
        return [], False
    docs.sort(key=lambda d: d["created_at"], reverse=True)
    return [_history_item(d) for d in docs[:limit]], True


def _history_item(doc: dict) -> dict:
    """The small shape the history route returns; a summary shows only the start of its text."""
    text = doc.get("text")
    preview = None
    if text:
        preview = text[:PREVIEW_CHARS] + ("..." if len(text) > PREVIEW_CHARS else "")
    return {
        "created_at": doc["created_at"],
        "feature": doc["feature"],
        "model_version": doc.get("model_version"),
        "probability": doc.get("probability"),
        "band": doc.get("band"),
        "state": doc.get("state"),
        "text_preview": preview,
    }


# ---------------------------------------------------------------- health

def status() -> str:
    """'ok' when the store answers a ping, 'unavailable' otherwise. Used by /health. The ping has
    its own short deadline so a readiness probe is not held for the full two-second timeout."""
    try:
        with pymongo.timeout(HEALTH_PING_SECONDS):
            client().admin.command("ping")
        return "ok"
    except Exception as exc:
        _warn("ai_store ping failed", exc)
        return "unavailable"
