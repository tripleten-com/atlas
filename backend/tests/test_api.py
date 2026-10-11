"""The promises the API makes: auth, the tenant boundary, and the shapes the frontend reads."""
import pytest


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/api/patients", "/api/facilities", "/api/dashboard/summary", "/api/auth/me"])
def test_data_routes_need_a_token(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_password_is_rejected(client):
    response = client.post("/api/auth/login", json={"email": "clinician@riverside.example", "password": "nope"})
    assert response.status_code == 401


def test_garbage_token_is_rejected(client):
    assert client.get("/api/patients", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_me_reports_the_tenant(client, riverside):
    body = client.get("/api/auth/me", headers=riverside).json()
    assert body["tenant"]["slug"] == "riverside"
    assert body["role"] == "clinician"


def test_facilities_are_scoped_to_the_tenant(client, riverside, harborview):
    riverside_codes = {f["code"] for f in client.get("/api/facilities", headers=riverside).json()}
    harborview_codes = {f["code"] for f in client.get("/api/facilities", headers=harborview).json()}
    assert "DEMO" in riverside_codes
    assert riverside_codes.isdisjoint(harborview_codes)


def test_demo_facility_is_seeded(client, riverside, demo_facility_id):
    page = client.get("/api/patients", params={"facility_id": demo_facility_id, "page_size": 1}, headers=riverside).json()
    assert page["total"] > 1000


def test_patient_detail_has_the_panels_data(client, riverside, demo_patient_id):
    body = client.get(f"/api/patients/{demo_patient_id}", headers=riverside).json()
    assert body["status"] == "inpatient"
    assert body["current_encounter"]["is_active"] is True
    assert {v["measurement"] for v in body["latest_vitals"]} >= {"heart_rate", "spo2"}
    for path in ("vitals", "labs", "medications", "encounters", "timeline"):
        response = client.get(f"/api/patients/{demo_patient_id}/{path}", headers=riverside)
        assert response.status_code == 200, path
        assert isinstance(response.json(), list)


def test_other_tenants_patient_looks_missing(client, harborview, demo_patient_id):
    """Another customer's patient and a patient that does not exist get the same answer."""
    theirs = client.get(f"/api/patients/{demo_patient_id}", headers=harborview)
    missing = client.get("/api/patients/99999999", headers=harborview)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


def test_other_tenants_facility_filter_is_refused(client, harborview, demo_facility_id):
    assert client.get("/api/patients", params={"facility_id": demo_facility_id}, headers=harborview).status_code == 404


def test_patient_search(client, riverside, demo_patient_id):
    patient = client.get(f"/api/patients/{demo_patient_id}", headers=riverside).json()
    page = client.get("/api/patients", params={"q": patient["mrn"]}, headers=riverside).json()
    assert [p["id"] for p in page["items"]] == [demo_patient_id]


def test_dashboard_summary_shape(client, riverside, demo_facility_id):
    body = client.get("/api/dashboard/summary", params={"facility_id": demo_facility_id}, headers=riverside).json()
    assert body["patients_total"] > 1000
    assert set(body["census"]) == {"inpatient", "observation", "emergency", "outpatient"}
    daily = client.get("/api/dashboard/daily", params={"facility_id": demo_facility_id, "days": 14}, headers=riverside).json()
    assert len(daily) == 14


def test_admin_only_routes(client, riverside, riverside_admin):
    assert client.get("/api/usage", headers=riverside).status_code == 403
    assert client.get("/api/usage", headers=riverside_admin).status_code == 200
    assert client.get("/api/audit", headers=riverside_admin).status_code == 200


def test_webhook_rejects_unsigned_deliveries(client):
    response = client.post(
        "/api/integrations/webhooks/meridian",
        json={"subscription_id": "sub_unknown", "epoch": "x", "events": []},
        headers={"X-Meridian-Signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 401
