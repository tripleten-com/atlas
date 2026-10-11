"""The predictive feature: 30-day readmission risk for one patient.

    GET /api/patients/{patient_id}/risk/readmission

Behind auth and the tenant boundary like every other patient route. The answers it can give:

    200  {probability, band, model_version, trained_on, trained_at, features_used, as_of, encounter_id,
          window_closes_at, outcome, readmitted_at}
    404  Patient not found                     (missing, or another tenant's; the usual 404)
    404  not applicable: no discharged ...     (patient has no discharged inpatient or observation stay)
    404  not applicable: patient currently admitted   (a later stay is still open; score at its discharge)
    503  model not available                   (no artifact at MODEL_PATH, or it fails the MODEL_SHA256 check)

The score is always about the patient's latest finished stay. That stay may be old, so the
response also says what the record already knows about its 30-day window (`outcome`): "pending"
while the window is open, "readmitted" when a return encounter is on file, "not_readmitted" when
the window closed quietly. The card shows the probability as a forecast only while it is pending.

Every prediction is metered with record_usage(), which is what the Usage page counts.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from .. import ml
from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..metering import record_usage
from ..schemas import ReadmissionRisk
from ..tenancy import load_patient

router = APIRouter(tags=["risk"])

NOT_APPLICABLE = "not applicable: no discharged inpatient or observation encounter"
CURRENTLY_ADMITTED = "not applicable: patient currently admitted"


@router.get("/api/patients/{patient_id}/risk/readmission", response_model=ReadmissionRisk)
def readmission_risk(
    patient_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)
) -> ReadmissionRisk:
    started = time.monotonic()
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant

    model = ml.load_model()
    if model is None:
        raise HTTPException(status_code=503, detail="model not available")

    row = ml.build_features_for_patient(conn, user, patient_id)
    if row is None:
        raise HTTPException(status_code=404, detail=NOT_APPLICABLE)

    outcome = ml.stay_outcome(conn, user.tenant_id, patient_id, row["discharge_date"])
    # A return encounter inside the window settles the question, even if that stay is still
    # open. Otherwise an open later stay means the risk belongs to that stay's discharge, not this one.
    if outcome["outcome"] != "readmitted" and outcome["currently_admitted"]:
        raise HTTPException(status_code=404, detail=CURRENTLY_ADMITTED)

    probability = ml.predict_probability(model, row)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    record_usage(conn, user, feature="readmission-risk", model=model["model_version"], predictions=1, elapsed_ms=elapsed_ms)
    return ReadmissionRisk(
        probability=round(probability, 3),
        band=ml.band(probability),
        model_version=model["model_version"],
        trained_on=model.get("trained_on", "unknown"),
        trained_at=model.get("trained_at"),
        features_used=list(model["feature_columns"]),
        as_of=row["discharge_date"],
        encounter_id=row["encounter_id"],
        window_closes_at=outcome["window_closes_at"],
        outcome=outcome["outcome"],
        readmitted_at=outcome["readmitted_at"],
    )
