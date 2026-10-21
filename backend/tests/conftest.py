"""Tests run against the seeded development database:

    docker compose exec backend pytest -q

They read the demo data and roll back anything they write.

The daily token ceiling is the one thing in the real data that changes during the day: once the
Riverside tenant has spent its allowance (an eval run, a demo), every stay-summary call answers
429. So the suite runs with a ceiling no day reaches, and the test of the ceiling sets its own.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import summary

PASSWORD = "atlas-demo"
ROOMY_CEILING = 10 ** 15


def set_token_ceiling(monkeypatch, tokens: int) -> None:
    """Swap in a copy of the settings with another daily token ceiling (Settings is frozen)."""
    monkeypatch.setattr(summary, "settings", dataclasses.replace(summary.settings, llm_daily_token_limit=tokens))


@pytest.fixture(autouse=True)
def roomy_token_ceiling(monkeypatch):
    """Today's real usage never decides a test: the ceiling is out of reach unless a test lowers it."""
    set_token_ceiling(monkeypatch, ROOMY_CEILING)


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # runs the lifespan, so the connection pool opens
        yield c


def _login(client: TestClient, email: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="session")
def riverside(client):
    return _login(client, "clinician@riverside.example")


@pytest.fixture(scope="session")
def riverside_admin(client):
    return _login(client, "admin@riverside.example")


@pytest.fixture(scope="session")
def harborview(client):
    return _login(client, "clinician@harborview.example")


@pytest.fixture(scope="session")
def demo_facility_id(client, riverside):
    facilities = client.get("/api/facilities", headers=riverside).json()
    return next(f["id"] for f in facilities if f["code"] == "DEMO")


@pytest.fixture(scope="session")
def demo_patient_id(client, riverside, demo_facility_id):
    page = client.get("/api/patients", params={"facility_id": demo_facility_id, "status": "inpatient", "page_size": 1},
                      headers=riverside).json()
    return page["items"][0]["id"]
