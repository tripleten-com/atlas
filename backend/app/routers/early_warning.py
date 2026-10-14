"""The second predictive feature: early warning of deterioration for an admitted inpatient.

    GET /api/patients/{patient_id}/risk/deterioration

Behind auth and the tenant boundary like every other patient route. The answers it can give:

    200  state "scored"             {probability, band, alert, ...}: the chance the vitals cross
                                    the deterioration line within the next 12 hours
    200  state "insufficient_data"  fewer than 12 hours of the stay have passed with vitals on
                                    file (no probability; the card says to come back later)
    200  state "deteriorating"      the onset rule already fired; `as_of` is the hour it fired
                                    (no forecast: the event has happened)
    404  Patient not found          (missing, or another tenant's; the usual 404)
    404  not applicable: no open inpatient encounter
    503  model not available        (no artifact at MODEL_DETERIORATION_PATH, or it fails the
                                    MODEL_DETERIORATION_SHA256 check)

The readmission card answers for a discharged stay, this card for an open one. Every 200 answer is
metered with record_usage() (predictions=1 only when a probability was produced) and written as a
document to the AI store under the same request_id; `stored` says whether that write happened.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from .. import ai_store, ml, ml_deterioration
from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..metering import record_usage
from ..schemas import EarlyWarning
from ..tenancy import load_patient

router = APIRouter(tags=["risk"])

FEATURE = ml_deterioration.FEATURE
NOT_APPLICABLE = "not applicable: no open inpatient encounter"


@router.get("/api/patients/{patient_id}/risk/deterioration", response_model=EarlyWarning)
def deterioration_risk(
    patient_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)
) -> EarlyWarning:
    started = time.monotonic()
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant

    model = ml_deterioration.load_model()
    if model is None:
        raise HTTPException(status_code=503, detail="model not available")

    encounter = ml_deterioration.open_inpatient_encounter(conn, user, patient_id)
    if encounter is None:
        raise HTTPException(status_code=404, detail=NOT_APPLICABLE)

    rows = ml_deterioration.vitals_rows(conn, user, encounter["encounter_id"])
    answer = ml_deterioration.assess(encounter, rows, model)
    probability = answer["probability"]
    if probability is not None:
        probability = round(probability, 3)
    band = ml.band(probability) if probability is not None else None

    elapsed_ms = int((time.monotonic() - started) * 1000)
    request_id = str(uuid.uuid4())  # shared by the Postgres usage row and the Mongo document
    record_usage(
        conn, user, feature=FEATURE, model=model["model_version"],
        predictions=1 if probability is not None else 0, elapsed_ms=elapsed_ms, request_id=request_id,
    )
    # Fire-and-forget: a store that is down costs the history entry, never the answer.
    stored = ai_store.record_prediction(
        user.tenant_id, request_id, feature=FEATURE, model_version=model["model_version"],
        patient_id=patient_id, encounter_id=answer["encounter_id"], probability=probability, band=band,
        alert=answer["alert"], inputs=answer["inputs"], state=answer["state"],
    )
    return EarlyWarning(
        probability=probability,
        band=band,
        alert=answer["alert"],
        model_version=model["model_version"],
        trained_on=model["trained_on"],
        trained_at=model["trained_at"],
        as_of=answer["as_of"],
        encounter_id=answer["encounter_id"],
        hours_admitted=answer["hours_admitted"],
        hours_of_vitals=answer["hours_of_vitals"],
        window_start=answer["window_start"],
        window_end=answer["window_end"],
        state=answer["state"],
        stored=stored,
    )
