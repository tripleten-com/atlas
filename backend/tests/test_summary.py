"""The stay-summary route, with the language model replaced by a stub so no network is needed:
grounded output, honest refusals, metering, and never across the tenant boundary."""
from __future__ import annotations

import pytest

from app import llm
from app.db import connection
from app.routers import summary

SUMMARY = "/api/patients/{}/summary"
FAKE_TEXT = "The patient was admitted. Vitals were recorded. Labs were drawn. The patient went home."


@pytest.fixture
def fake_model(monkeypatch):
    """Pretend a key is configured and make chat() answer instantly without calling anything."""
    calls = []

    def fake_chat(messages, max_tokens=600, temperature=0.2):
        calls.append(messages)
        return FAKE_TEXT, 321, 45

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


def _usage_rows(tenant_id: int, status: str) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM usage_events WHERE tenant_id = %s AND feature = 'stay-summary' AND status = %s",
            (tenant_id, status),
        ).fetchone()
    return row["n"]


def _tenant_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["tenant"]["id"]


def test_summary_needs_a_token(client, demo_patient_id):
    assert client.post(SUMMARY.format(demo_patient_id)).status_code == 401


def test_summary_happy_path(client, riverside, demo_patient_id, fake_model):
    tenant_id = _tenant_id(client, riverside)
    before = _usage_rows(tenant_id, "ok")

    response = client.post(SUMMARY.format(demo_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == FAKE_TEXT
    assert body["input_tokens"] == 321 and body["output_tokens"] == 45
    assert body["elapsed_ms"] >= 0
    assert body["encounter_id"] > 0

    # The sources are the rows the model saw: the encounter first, then its vitals, labs and orders.
    tables = {s["table"] for s in body["sources"]}
    assert body["sources"][0] == {"table": "encounters", "id": body["encounter_id"]}
    assert tables <= {"encounters", "vitals", "labs", "medications"}

    # The prompt carried those ids and nothing identifying: no patient name.
    prompt = fake_model[0][1]["content"]
    assert f"ENCOUNTER id={body['encounter_id']}" in prompt
    patient = client.get(f"/api/patients/{demo_patient_id}", headers=riverside).json()
    assert patient["last_name"] not in prompt

    # One usage row per call, with the tokens the endpoint reported.
    assert _usage_rows(tenant_id, "ok") == before + 1


def test_summary_without_a_key_is_503_and_metered(client, riverside, demo_patient_id, monkeypatch):
    monkeypatch.setattr(llm, "configured", lambda: False)
    tenant_id = _tenant_id(client, riverside)
    before = _usage_rows(tenant_id, "unconfigured")
    response = client.post(SUMMARY.format(demo_patient_id), headers=riverside)
    assert response.status_code == 503
    assert response.json() == {"detail": "no model configured"}
    assert _usage_rows(tenant_id, "unconfigured") == before + 1  # refusals are metered too


def test_summary_over_the_daily_limit_is_429(client, riverside, demo_patient_id, fake_model):
    """Spend today's allowance in one fake row, then watch the route refuse and record the refusal."""
    tenant_id = _tenant_id(client, riverside)
    with connection() as conn:
        conn.execute(
            """INSERT INTO usage_events (tenant_id, feature, model, input_tokens, output_tokens, status)
               VALUES (%s, 'stay-summary', 'test', %s, 0, 'ok') RETURNING id""",
            (tenant_id, 10 ** 9),
        )
    try:
        before = _usage_rows(tenant_id, "limited")
        response = client.post(SUMMARY.format(demo_patient_id), headers=riverside)
        assert response.status_code == 429
        assert response.json() == {"detail": "daily token limit reached"}
        assert fake_model == []  # the model was never called
        assert _usage_rows(tenant_id, "limited") == before + 1
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM usage_events WHERE tenant_id = %s AND feature = 'stay-summary' AND model = 'test'",
                         (tenant_id,))


def test_summary_other_tenant_looks_missing(client, harborview, demo_patient_id, fake_model):
    """Harborview must get the same 404 for Riverside's patient as for one that does not exist."""
    theirs = client.post(SUMMARY.format(demo_patient_id), headers=harborview)
    missing = client.post(SUMMARY.format(99999999), headers=harborview)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()
    assert fake_model == []


def test_summary_endpoint_failure_is_502_and_metered(client, riverside, demo_patient_id, monkeypatch):
    def broken_chat(messages, max_tokens=600, temperature=0.2):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "chat", broken_chat)
    tenant_id = _tenant_id(client, riverside)
    before = _usage_rows(tenant_id, "error")
    response = client.post(SUMMARY.format(demo_patient_id), headers=riverside)
    assert response.status_code == 502
    assert response.json() == {"detail": "model endpoint error"}
    assert _usage_rows(tenant_id, "error") == before + 1


def test_grounding_lines_carry_ids_and_numbers(client, riverside, demo_patient_id):
    """The text the model reads has one line per row, each with its id, so it can be checked."""
    tenant_id = _tenant_id(client, riverside)
    with connection() as conn:
        grounding = summary.fetch_grounding(conn, tenant_id, demo_patient_id)
    assert grounding is not None
    lines = grounding.lines()
    assert lines[0].startswith("PATIENT: ")
    assert lines[1].startswith(f"ENCOUNTER id={grounding.encounter['id']}:")
    assert len(lines) == 2 + len(grounding.vitals) + len(grounding.labs) + len(grounding.medications)
    assert len(grounding.labs) <= summary.MAX_LABS
    for row in grounding.labs:
        assert f"LAB id={row['id']}:" in "\n".join(lines)
