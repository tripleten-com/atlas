"""Earlier AI outputs for one patient, read from the document store.

    GET /api/patients/{patient_id}/ai/history?feature=readmission-risk&limit=5

Behind auth and the tenant boundary like every other patient route. The answers it can give:

    200  {items: [{created_at, feature, model_version, probability, band, state, text_preview}], stored}
    404  Patient not found   (missing, or another tenant's; the usual 404)

The Postgres lookup runs first (tenancy.load_patient), so another organisation's patient id is a
404 before the store is touched. Predictions, generations and agent runs are all listed; an agent
run shows its status ("completed" or "stopped") as `state` and the start of its answer as
`text_preview`, so any feature name works as the filter without a router change. Then the store is queried with the caller's tenant_id from the
token. `stored` is false when the store could not be read; the items are then empty and the card
says "history unavailable" instead of showing an empty list as if nothing had ever been scored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from .. import ai_store
from ..auth import CurrentUser, get_current_user
from ..db import get_db
from ..schemas import AiHistory
from ..tenancy import load_patient

router = APIRouter(tags=["ai history"])


@router.get("/api/patients/{patient_id}/ai/history", response_model=AiHistory)
def ai_history(
    patient_id: int,
    feature: str | None = Query(None, description="Any stored feature name, e.g. readmission-risk, early-warning, stay-summary or an agent feature; omit for all"),
    limit: int = Query(5, ge=1, le=50),
    user: CurrentUser = Depends(get_current_user),
    conn: Connection = Depends(get_db),
) -> AiHistory:
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant
    items, ok = ai_store.history(user.tenant_id, patient_id, feature=feature, limit=limit)
    return AiHistory(items=items, stored=ok)
