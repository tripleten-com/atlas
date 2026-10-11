"""The readmission-risk route: scored when it applies, honest when it does not, and never across
the tenant boundary."""
from __future__ import annotations

import pytest

from app import ml
from app.db import connection

RISK = "/api/patients/{}/risk/readmission"

# SQL fragments that describe the patient's latest finished stay (alias `l`) and what came after it.
LATEST_STAY = """
    (SELECT e.discharge_date FROM encounters e WHERE e.patient_id = p.id
      AND e.admission_type IN ('inpatient', 'observation') AND e.discharge_date IS NOT NULL
      AND coalesce(e.discharge_disposition, '') <> 'expired'
      ORDER BY e.discharge_date DESC, e.id DESC LIMIT 1)"""
RETURNED_IN_WINDOW = f"""
    EXISTS (SELECT 1 FROM encounters r WHERE r.patient_id = p.id
            AND r.admission_type IN ('inpatient', 'observation', 'emergency')
            AND r.admit_date > {LATEST_STAY} AND r.admit_date <= {LATEST_STAY} + interval '30 days')"""
OPEN_LATER_STAY = f"""
    EXISTS (SELECT 1 FROM encounters o WHERE o.patient_id = p.id
            AND o.admission_type IN ('inpatient', 'observation', 'emergency')
            AND o.admit_date > {LATEST_STAY} AND o.discharge_date IS NULL)"""


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
def discharged_patient_id(client, demo_facility_id):
    """A patient whose latest stay ended long enough ago that the window has closed, who did not
    come back and is not in hospital now: the plainest case the route scores."""
    return _patient_where(
        f"{LATEST_STAY} IS NOT NULL AND {LATEST_STAY} + interval '30 days' < now() "
        f"AND NOT {RETURNED_IN_WINDOW} AND NOT {OPEN_LATER_STAY}",
        demo_facility_id,
    )


@pytest.fixture(scope="module")
def outpatient_only_id(client, demo_facility_id):
    """A patient with visits but never an inpatient or observation stay."""
    return _patient_where(
        """EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id = p.id)
           AND NOT EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id = p.id
                           AND e.admission_type IN ('inpatient', 'observation'))""",
        demo_facility_id,
    )


def test_risk_needs_a_token(client, discharged_patient_id):
    assert client.get(RISK.format(discharged_patient_id)).status_code == 401


def test_risk_for_a_discharged_patient(client, riverside, discharged_patient_id):
    response = client.get(RISK.format(discharged_patient_id), headers=riverside)
    assert response.status_code == 200, response.text
    body = response.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["band"] == ml.band(body["probability"])
    assert body["band"] in ("low", "medium", "high")
    assert body["model_version"].startswith("readmission-")
    assert body["trained_on"] in ("ec2", "local")
    assert body["features_used"] == ml.FEATURE_COLUMNS
    assert body["encounter_id"] > 0
    assert body["as_of"]  # the discharge the score is about
    assert body["window_closes_at"] > body["as_of"]
    assert body["outcome"] == "not_readmitted"  # the fixture picked a closed, quiet window
    assert body["readmitted_at"] is None


def test_risk_scores_the_latest_discharge(client, riverside, discharged_patient_id):
    body = client.get(RISK.format(discharged_patient_id), headers=riverside).json()
    encounters = client.get(f"/api/patients/{discharged_patient_id}/encounters", headers=riverside).json()
    stays = [e for e in encounters if e["admission_type"] in ("inpatient", "observation") and e["discharge_date"]
             and e["discharge_disposition"] != "expired"]
    latest = max(stays, key=lambda e: e["discharge_date"])
    assert body["encounter_id"] == latest["id"]


def test_risk_says_when_the_patient_already_came_back(client, riverside, demo_facility_id):
    """A return encounter inside the window is an outcome the record knows; the card must say so."""
    patient_id = _patient_where(f"{LATEST_STAY} IS NOT NULL AND {RETURNED_IN_WINDOW}", demo_facility_id)
    body = client.get(RISK.format(patient_id), headers=riverside).json()
    assert body["outcome"] == "readmitted"
    assert body["as_of"] < body["readmitted_at"] <= body["window_closes_at"]


def test_risk_pending_while_the_window_is_open(client, riverside, demo_facility_id):
    """Discharged within the last 30 days and not back yet: the only state where the score is a forecast."""
    patient_id = _patient_where(
        f"{LATEST_STAY} IS NOT NULL AND {LATEST_STAY} + interval '30 days' >= now() "
        f"AND NOT {RETURNED_IN_WINDOW} AND NOT {OPEN_LATER_STAY}",
        demo_facility_id,
        required=False,
    )
    if patient_id is None:
        pytest.skip("the Test Facility was seeded more than 30 days ago, so no window is still open")
    body = client.get(RISK.format(patient_id), headers=riverside).json()
    assert body["outcome"] == "pending"
    assert body["readmitted_at"] is None


def test_risk_not_applicable_while_admitted(client, riverside, demo_facility_id):
    """An open later stay means the risk belongs to that stay's discharge, which has not happened."""
    patient_id = _patient_where(
        f"{LATEST_STAY} IS NOT NULL AND {OPEN_LATER_STAY} AND NOT {RETURNED_IN_WINDOW}", demo_facility_id, required=False
    )
    if patient_id is None:
        pytest.skip("no Test Facility patient is currently admitted after a finished stay")
    response = client.get(RISK.format(patient_id), headers=riverside)
    assert response.status_code == 404
    assert response.json()["detail"] == "not applicable: patient currently admitted"


def test_risk_not_applicable_without_a_stay(client, riverside, outpatient_only_id):
    response = client.get(RISK.format(outpatient_only_id), headers=riverside)
    assert response.status_code == 404
    assert response.json()["detail"] == "not applicable: no discharged inpatient or observation encounter"


def test_risk_other_tenant_looks_missing(client, harborview, discharged_patient_id):
    """Harborview must get the same 404 for Riverside's patient as for one that does not exist."""
    theirs = client.get(RISK.format(discharged_patient_id), headers=harborview)
    missing = client.get(RISK.format(99999999), headers=harborview)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


def test_risk_without_a_model_is_503(client, riverside, discharged_patient_id, monkeypatch):
    monkeypatch.setattr(ml, "_model_file", lambda: "/app/models/does-not-exist.joblib")
    response = client.get(RISK.format(discharged_patient_id), headers=riverside)
    assert response.status_code == 503
    assert response.json() == {"detail": "model not available"}


def test_risk_refuses_an_artifact_with_the_wrong_hash(client, riverside, discharged_patient_id, monkeypatch):
    """A pinned MODEL_SHA256 that does not match means the file is not the one that was trained."""
    monkeypatch.setattr(ml, "_expected_sha256", lambda: "0" * 64)
    ml._cache.clear()  # the real artifact was loaded at startup; force the check to run
    try:
        response = client.get(RISK.format(discharged_patient_id), headers=riverside)
        assert response.status_code == 503
    finally:
        ml._cache.clear()


def test_artifact_columns_match_the_serving_code():
    """train.py reads the column lists from the export; this catches the two drifting apart."""
    model = ml.load_model()
    assert model is not None, "models/readmission.joblib should be present for the tests"
    assert model["feature_columns"] == ml.FEATURE_COLUMNS
    assert model["categorical_columns"] == ml.CATEGORICAL_COLUMNS


def test_predict_handles_missing_values():
    """A missing category (None or NaN) and missing vitals must score, not crash."""
    model = ml.load_model()
    assert model is not None
    row = dict.fromkeys(ml.FEATURE_COLUMNS)
    row.update(sex="F", admission_type="inpatient", admit_source="emergency", discharge_disposition=None,
               age=70, length_of_stay_hours=48.0, prior_admissions_365d=1, abnormal_lab_count=2, drug_class_count=3)
    assert 0.0 <= ml.predict_probability(model, row) <= 1.0
    row["discharge_disposition"] = float("nan")  # what pandas makes of a blank CSV cell
    assert 0.0 <= ml.predict_probability(model, row) <= 1.0


def test_band_thresholds():
    assert ml.band(0.0) == "low"
    assert ml.band(0.149) == "low"
    assert ml.band(0.15) == "medium"
    assert ml.band(0.349) == "medium"
    assert ml.band(0.35) == "high"
    assert ml.band(1.0) == "high"
