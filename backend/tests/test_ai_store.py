"""The document store: a prediction and a summary leave documents with the caller's tenant id,
history never crosses the tenant boundary, and a store that is down never breaks a route."""
from __future__ import annotations

import logging

import pytest

from app import ai_store, llm
from app.db import connection

RISK = "/api/patients/{}/risk/readmission"
SUMMARY = "/api/patients/{}/summary"
HISTORY = "/api/patients/{}/ai/history"
FAKE_TEXT = "The patient was admitted. Vitals were recorded. Labs were drawn. The patient went home."


@pytest.fixture(scope="module")
def scorable_patient_id(client, demo_facility_id):
    """A Test Facility patient with a finished inpatient or observation stay and nothing open now,
    so the risk route answers 200 whatever the 30-day outcome was."""
    with connection() as conn:
        row = conn.execute(
            """SELECT p.id FROM patients p
               WHERE p.facility_id = %s
                 AND EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id = p.id
                             AND e.admission_type IN ('inpatient', 'observation') AND e.discharge_date IS NOT NULL
                             AND coalesce(e.discharge_disposition, '') <> 'expired')
                 AND NOT EXISTS (SELECT 1 FROM encounters o WHERE o.patient_id = p.id AND o.discharge_date IS NULL)
               ORDER BY p.id LIMIT 1""",
            (demo_facility_id,),
        ).fetchone()
    assert row is not None, "the seeded Test Facility should have a discharged patient"
    return row["id"]


@pytest.fixture
def fake_model(monkeypatch):
    """The same stub test_summary.py uses: a key is configured and chat() answers instantly."""
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda messages, max_tokens=600, temperature=0.2: (FAKE_TEXT, 321, 45))


@pytest.fixture
def store_down(monkeypatch):
    """Point the store at a closed port for one test, without touching Docker."""
    monkeypatch.setattr(ai_store, "_mongo_url", lambda: "mongodb://127.0.0.1:1/atlas_ai?connectTimeoutMS=500")
    ai_store.reset()
    yield
    ai_store.reset()  # the next test gets a client for the real URL again


def _tenant_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["tenant"]["id"]


def _latest_document(collection: str, tenant_id: int, patient_id: int, feature: str) -> dict | None:
    return ai_store.db()[collection].find_one(
        {"tenant_id": tenant_id, "patient_id": patient_id, "feature": feature}, sort=[("created_at", -1)]
    )


def _usage_request_id(tenant_id: int, feature: str) -> str | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT request_id FROM usage_events WHERE tenant_id = %s AND feature = %s ORDER BY id DESC LIMIT 1",
            (tenant_id, feature),
        ).fetchone()
    return row["request_id"] if row else None


# ---------------------------------------------------------------- predictions

def test_prediction_document_carries_the_callers_tenant_id(client, riverside, scorable_patient_id):
    tenant_id = _tenant_id(client, riverside)
    response = client.get(RISK.format(scorable_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored"] is True

    doc = _latest_document(ai_store.PREDICTIONS, tenant_id, scorable_patient_id, "readmission-risk")
    assert doc is not None
    assert doc["tenant_id"] == tenant_id
    assert doc["probability"] == body["probability"]
    assert doc["band"] == body["band"]
    assert doc["model_version"] == body["model_version"]
    assert doc["encounter_id"] == body["encounter_id"]
    assert doc["state"] == body["outcome"]
    assert set(doc["inputs"]) == set(body["features_used"])
    # The same request_id sits on the Postgres usage row, so the two stores can be reconciled.
    assert doc["request_id"] == _usage_request_id(tenant_id, "readmission-risk")


def test_history_lists_the_prediction_newest_first(client, riverside, scorable_patient_id):
    body = client.get(RISK.format(scorable_patient_id), headers=riverside).json()
    response = client.get(HISTORY.format(scorable_patient_id), params={"feature": "readmission-risk"}, headers=riverside)
    assert response.status_code == 200, response.text
    history = response.json()
    assert history["stored"] is True
    assert 1 <= len(history["items"]) <= 5
    newest = history["items"][0]
    assert newest["feature"] == "readmission-risk"
    assert newest["probability"] == body["probability"]
    assert newest["band"] == body["band"]
    assert newest["text_preview"] is None
    stamps = [item["created_at"] for item in history["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_history_needs_a_token(client, scorable_patient_id):
    assert client.get(HISTORY.format(scorable_patient_id)).status_code == 401


def test_history_other_tenant_looks_missing(client, harborview, scorable_patient_id):
    """Harborview must get the same 404 for Riverside's patient as for one that does not exist,
    and never a list of Riverside's scores."""
    theirs = client.get(HISTORY.format(scorable_patient_id), headers=harborview)
    missing = client.get(HISTORY.format(99999999), headers=harborview)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


# ---------------------------------------------------------------- generations

def test_generation_document_carries_the_sources(client, riverside, demo_patient_id, fake_model):
    tenant_id = _tenant_id(client, riverside)
    response = client.post(SUMMARY.format(demo_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored"] is True

    doc = _latest_document(ai_store.GENERATIONS, tenant_id, demo_patient_id, "stay-summary")
    assert doc is not None
    assert doc["tenant_id"] == tenant_id
    assert doc["text"] == FAKE_TEXT
    assert doc["sources"] == body["sources"]
    assert doc["input_tokens"] == 321 and doc["output_tokens"] == 45
    assert doc["status"] == "ok"
    assert doc["user_id"] == client.get("/api/auth/me", headers=riverside).json()["id"]
    assert doc["request_id"] == _usage_request_id(tenant_id, "stay-summary")

    history = client.get(HISTORY.format(demo_patient_id), params={"feature": "stay-summary"}, headers=riverside).json()
    assert history["items"][0]["text_preview"] == FAKE_TEXT  # shorter than the preview cut
    assert history["items"][0]["probability"] is None


# ---------------------------------------------------------------- the store is down

def test_store_down_risk_still_answers_and_health_says_so(client, riverside, scorable_patient_id, store_down):
    response = client.get(RISK.format(scorable_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    assert response.json()["stored"] is False

    health = client.get("/health").json()
    assert health["status"] == "ok"  # the clinical record is served; readiness must not fail
    assert health["ai_store"] == "unavailable"

    history = client.get(HISTORY.format(scorable_patient_id), headers=riverside).json()
    assert history == {"items": [], "stored": False}


def test_health_reports_the_store(client):
    assert client.get("/health").json()["ai_store"] == "ok"


def test_warning_is_logged_at_most_once_a_minute(caplog, monkeypatch):
    monkeypatch.setattr(ai_store, "_last_warning_at", 0.0)
    with caplog.at_level(logging.WARNING, logger="atlas.ai_store"):
        ai_store._warn("first")
        ai_store._warn("second")
    assert [r.getMessage() for r in caplog.records] == ["first (no store)"]


def test_indexes_are_created_on_the_first_write_when_startup_missed_them(monkeypatch):
    """On a cluster the api can start before mongo-0 answers, so the lifespan's ensure_indexes()
    fails. The first write that reaches the store must create the indexes instead."""
    monkeypatch.setattr(ai_store, "_indexes_ready", False)
    collection = ai_store.db()[ai_store.PREDICTIONS]
    collection.drop_indexes()
    assert "created_at_ttl" not in collection.index_information()

    assert ai_store.record_prediction(
        1, "test-index-recovery", feature="readmission-risk", model_version="test",
        patient_id=0, encounter_id=None, probability=0.1, band="low",
    )
    names = collection.index_information()
    assert "created_at_ttl" in names and "tenant_patient_created" in names
    assert ai_store._indexes_ready is True
    collection.delete_many({"request_id": "test-index-recovery"})
