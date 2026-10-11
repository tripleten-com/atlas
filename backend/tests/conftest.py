"""Tests run against the seeded development database:

    docker compose exec backend pytest -q

They read the demo data and roll back anything they write.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "atlas-demo"


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
