"""The early-warning route: a forecast for an open inpatient stay, honest states when there is
nothing to forecast, never across the tenant boundary, and the serving code in step with the
artifact the training side wrote."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app import ai_store, ml, ml_deterioration
from app.db import connection

EARLY_WARNING = "/api/patients/{}/risk/deterioration"
FEATURE = "early-warning"


def _patient_where(sql: str, facility_id: int, required: bool = True) -> int | None:
    """Pick one Test Facility patient matching a condition, straight from the database."""
    with connection() as conn:
        row = conn.execute(
            f"SELECT p.id FROM patients p WHERE p.facility_id = %s AND {sql} ORDER BY p.id LIMIT 1", (facility_id,)
        ).fetchone()
    if required:
        assert row is not None, "the seeded Test Facility should have such a patient"
    return row["id"] if row else None


@pytest.fixture(scope="module")
def admitted_patient_id(client, demo_facility_id):
    """An open inpatient stay at the Test Facility with at least 12 hours of vitals on file:
    the plainest case the route answers."""
    return _patient_where(
        """EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id = p.id AND e.admission_type = 'inpatient'
                   AND e.discharge_date IS NULL
                   AND (SELECT max(v.recorded_at) FROM vitals v WHERE v.encounter_id = e.id) >= e.admit_date + interval '12 hours')""",
        demo_facility_id,
    )


@pytest.fixture(scope="module")
def discharged_patient_id(client, demo_facility_id):
    """A patient with stays on file and nothing open now."""
    return _patient_where(
        """EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id = p.id AND e.admission_type = 'inpatient')
           AND NOT EXISTS (SELECT 1 FROM encounters o WHERE o.patient_id = p.id AND o.discharge_date IS NULL)""",
        demo_facility_id,
    )


def _tenant_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["tenant"]["id"]


# ---------------------------------------------------------------- the route

def test_early_warning_needs_a_token(client, admitted_patient_id):
    assert client.get(EARLY_WARNING.format(admitted_patient_id)).status_code == 401


def test_early_warning_for_an_admitted_patient(client, riverside, admitted_patient_id):
    response = client.get(EARLY_WARNING.format(admitted_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in ("scored", "deteriorating")  # 12 hours of vitals rules out insufficient_data
    assert body["model_version"].startswith("deterioration-")
    assert body["trained_on"] in ("ec2", "local")
    assert body["encounter_id"] > 0
    assert body["hours_admitted"] >= 12
    assert 1 <= body["hours_of_vitals"] <= body["hours_admitted"]
    assert body["window_start"] < body["window_end"]
    if body["state"] == "scored":
        assert 0.0 <= body["probability"] <= 1.0
        assert body["band"] == ml.band(body["probability"])
        assert body["alert"] == (body["probability"] >= ml_deterioration.load_model()["alert_threshold"])
        assert body["window_end"] == body["as_of"]
    else:
        assert body["probability"] is None and body["band"] is None and body["alert"] is None


def test_early_warning_scores_the_open_stay(client, riverside, admitted_patient_id):
    body = client.get(EARLY_WARNING.format(admitted_patient_id), headers=riverside).json()
    encounters = client.get(f"/api/patients/{admitted_patient_id}/encounters", headers=riverside).json()
    open_stays = [e for e in encounters if e["admission_type"] == "inpatient" and e["discharge_date"] is None]
    assert body["encounter_id"] in {e["id"] for e in open_stays}


def test_early_warning_not_applicable_when_discharged(client, riverside, discharged_patient_id):
    response = client.get(EARLY_WARNING.format(discharged_patient_id), headers=riverside)
    assert response.status_code == 404
    assert response.json()["detail"] == "not applicable: no open inpatient encounter"


def test_early_warning_other_tenant_looks_missing(client, harborview, admitted_patient_id):
    """Harborview must get the same 404 for Riverside's patient as for one that does not exist."""
    theirs = client.get(EARLY_WARNING.format(admitted_patient_id), headers=harborview)
    missing = client.get(EARLY_WARNING.format(99999999), headers=harborview)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


def test_early_warning_without_a_model_is_503(client, riverside, admitted_patient_id, monkeypatch):
    monkeypatch.setattr(ml_deterioration, "_model_file", lambda: "/app/models/does-not-exist.onnx")
    response = client.get(EARLY_WARNING.format(admitted_patient_id), headers=riverside)
    assert response.status_code == 503
    assert response.json() == {"detail": "model not available"}


def test_early_warning_refuses_an_artifact_with_the_wrong_hash(client, riverside, admitted_patient_id, monkeypatch):
    monkeypatch.setattr(ml_deterioration, "_expected_sha256", lambda: "0" * 64)
    ml_deterioration._cache.clear()  # the real artifact was loaded at startup; force the check to run
    try:
        response = client.get(EARLY_WARNING.format(admitted_patient_id), headers=riverside)
        assert response.status_code == 503
    finally:
        ml_deterioration._cache.clear()


def test_prediction_document_carries_the_callers_tenant_id(client, riverside, admitted_patient_id):
    tenant_id = _tenant_id(client, riverside)
    body = client.get(EARLY_WARNING.format(admitted_patient_id), headers=riverside).json()
    assert body["stored"] is True
    doc = ai_store.db()[ai_store.PREDICTIONS].find_one(
        {"tenant_id": tenant_id, "patient_id": admitted_patient_id, "feature": FEATURE}, sort=[("created_at", -1)]
    )
    assert doc is not None
    assert doc["tenant_id"] == tenant_id
    assert doc["encounter_id"] == body["encounter_id"]
    assert doc["state"] == body["state"]
    assert doc["probability"] == body["probability"]
    assert doc["band"] == body["band"]
    assert doc["alert"] == body["alert"]
    assert doc["inputs"]["as_of_hour"] == body["hours_admitted"]
    assert doc["inputs"]["hours_of_vitals"] == body["hours_of_vitals"]
    with connection() as conn:
        row = conn.execute(
            "SELECT request_id, predictions FROM usage_events WHERE tenant_id = %s AND feature = %s ORDER BY id DESC LIMIT 1",
            (tenant_id, FEATURE),
        ).fetchone()
    assert row["request_id"] == doc["request_id"]
    assert row["predictions"] == (1 if body["probability"] is not None else 0)


# ---------------------------------------------------------------- the artifact and the serving code

def test_artifact_matches_the_serving_code():
    """train_deterioration.py writes the feature order and the onset rule it used into
    deterioration.json; this catches the two sides drifting apart."""
    model = ml_deterioration.load_model()
    assert model is not None, "models/deterioration.onnx and .json should be present for the tests"
    card = model["card"]
    assert card["model"] == ml_deterioration.QUESTION
    assert tuple(card["sequence_features"]) == ml_deterioration.SEQUENCE_FEATURES
    assert tuple(card["static_features"]) == ml_deterioration.STATIC_FEATURES
    assert card["sex_codes"] == ml_deterioration.SEX_CODES
    assert card["admission_type_codes"] == ml_deterioration.ADMISSION_TYPE_CODES
    assert card["window_hours"] == ml_deterioration.WINDOW_HOURS
    assert card["horizon_hours"] == ml_deterioration.HORIZON_HOURS
    assert card["first_as_of_hour"] == ml_deterioration.FIRST_AS_OF_HOUR
    assert card["onset_rule"] == ml_deterioration.ONSET_RULE
    assert len(card["standardisation"]["sequence_means"]) == len(ml_deterioration.VITALS)
    assert len(card["standardisation"]["static_means"]) == len(ml_deterioration.STATIC_FEATURES)
    # the graph takes what the window builder produces
    inputs = {i.name: i.shape for i in model["session"].get_inputs()}
    assert inputs["sequence"][1:] == [24, len(ml_deterioration.SEQUENCE_FEATURES)]
    assert inputs["static"][1:] == [len(ml_deterioration.STATIC_FEATURES)]
    assert [o.name for o in model["session"].get_outputs()] == ["probability"]
    assert ml_deterioration.VITALS == ml.VITALS


def _encounter(admit: datetime) -> dict:
    return {"encounter_id": 1, "admit_date": admit, "admission_type": "inpatient", "age": 70, "sex": "female"}


def _normal_rows(admit: datetime, hours: int, every: int = 4) -> list[tuple]:
    """Unremarkable vitals every few hours from admission."""
    normal = {"heart_rate": 80, "systolic_bp": 125, "diastolic_bp": 78, "respiratory_rate": 16, "temperature": 36.9, "spo2": 97}
    rows = []
    for h in range(0, hours, every):
        when = admit + timedelta(hours=h, minutes=10)
        rows += [(when, name, value) for name, value in normal.items()]
    return rows


def test_synthetic_crossing_window_is_deteriorating():
    """Hour 30 has three crossings (heart rate, respiratory rate, SpO2): the onset rule fires and
    the answer is the state, not a forecast, with as_of the hour it fired."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    rows = _normal_rows(admit, 36)
    when = admit + timedelta(hours=30, minutes=5)
    rows += [(when, "heart_rate", 128.0), (when, "respiratory_rate", 28.0), (when, "spo2", 86.0)]
    answer = ml_deterioration.assess(_encounter(admit), rows, model, now=admit + timedelta(hours=40))
    assert answer["state"] == "deteriorating"
    assert answer["probability"] is None and answer["alert"] is None
    assert answer["as_of"] == admit + timedelta(hours=30)
    assert answer["inputs"]["onset_hour"] == 30


def test_one_crossing_is_not_an_onset():
    """A single crossing (SpO2 alone) is not enough: the rule wants two, so the stay is scored."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    rows = _normal_rows(admit, 36) + [(admit + timedelta(hours=30, minutes=5), "spo2", 86.0)]
    answer = ml_deterioration.assess(_encounter(admit), rows, model, now=admit + timedelta(hours=40))
    assert answer["state"] == "scored"
    assert 0.0 <= answer["probability"] <= 1.0


def test_young_stay_is_insufficient_data():
    """Eight hours of vitals: the model never saw an as-of hour before 12, so no forecast."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    answer = ml_deterioration.assess(_encounter(admit), _normal_rows(admit, 8), model, now=admit + timedelta(hours=8))
    assert answer["state"] == "insufficient_data"
    assert answer["probability"] is None
    assert answer["hours_of_vitals"] == 2
    # and a stay with no vitals at all, however long
    answer = ml_deterioration.assess(_encounter(admit), [], model, now=admit + timedelta(hours=30))
    assert answer["state"] == "insufficient_data"
    assert answer["hours_of_vitals"] == 0


def test_first_hour_of_a_stay_counts_no_vitals_hour():
    """A stay 20 minutes old with one set of vitals: hour 0 has not finished, so hours_admitted is
    0 and hours_of_vitals must be 0 too (never more than hours_admitted)."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    answer = ml_deterioration.assess(_encounter(admit), _normal_rows(admit, 1), model, now=admit + timedelta(minutes=20))
    assert answer["state"] == "insufficient_data"
    assert answer["hours_admitted"] == 0
    assert answer["hours_of_vitals"] == 0
    assert answer["hours_of_vitals"] <= answer["hours_admitted"]


def test_scored_probability_is_rounded_before_the_alert_decision():
    """The response, the stored document and the alert flag must agree, so assess() rounds the
    probability to three decimals and decides the alert on the rounded value."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    answer = ml_deterioration.assess(_encounter(admit), _normal_rows(admit, 36), model, now=admit + timedelta(hours=40))
    assert answer["state"] == "scored"
    assert answer["probability"] == round(answer["probability"], 3)
    assert answer["alert"] == (answer["probability"] >= model["alert_threshold"])


def test_clinical_clock_follows_the_latest_measurement():
    """Vitals stop at hour 40 while the wall clock says hour 300 (a frozen Test Facility stay):
    the window ends at hour 41, so the model reads the last 24 hours of data, not 24 empty hours."""
    model = ml_deterioration.load_model()
    admit = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    answer = ml_deterioration.assess(_encounter(admit), _normal_rows(admit, 41), model, now=admit + timedelta(hours=300))
    assert answer["state"] == "scored"
    assert answer["hours_admitted"] == 41
    assert answer["window_end"] == answer["as_of"] == admit + timedelta(hours=41)
    assert answer["window_start"] == admit + timedelta(hours=17)
    assert answer["inputs"]["measured_hours_in_window"] == 6  # hours 20, 24, ..., 40


def test_window_builder_only_reads_hours_before_as_of():
    """The first leakage rule: a measurement in the as-of hour itself stays out of the window."""
    grid = np.full((30, 6), np.nan)
    grid[29, 0] = 999.0  # the as-of hour
    grid[28, 0] = 80.0
    values, mask = ml_deterioration.build_window(grid, 29)
    assert values[-1, 0] == 80.0 and mask[-1, 0] == 1.0
    assert not (values == 999.0).any()
    assert mask.shape == values.shape == (24, 6)
    assert mask.sum() == 1.0
