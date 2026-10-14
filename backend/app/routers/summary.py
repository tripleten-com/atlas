"""The generative feature: a short, grounded summary of one patient's latest stay.

    POST /api/patients/{patient_id}/summary

Behind auth and the tenant boundary like every other patient route. The answers it can give:

    200  {summary, sources, model, input_tokens, output_tokens, elapsed_ms, encounter_id}
    404  Patient not found              (missing, or another tenant's; the usual 404)
    404  not applicable: no encounter   (the patient has never had a visit)
    429  daily token limit reached      (this tenant spent LLM_DAILY_TOKEN_LIMIT tokens today)
    502  model endpoint error           (the endpoint timed out or answered with an error)
    503  no model configured            (LLM_API_KEY is empty)

The reference ships the Task 3 cutline: one synchronous call that returns the whole text. The
course asks you to stream over SSE or run a queued job with polling; `routers/activity.py`
(`/api/activity/stream`) is the SSE pattern to copy.

How it stays honest: the backend fetches the rows itself (encounter, last vital per measurement,
up to twenty labs with flagged ones first, the stay's medication orders), writes them out one per
line with their ids, and tells the model to use nothing else. The same ids come back as `sources`
so the UI can show where each fact came from, and `evals/run_evals.py` checks that every number
and medication in the text is in those rows.

Every call is metered with record_usage(), including the ones that fail or are refused, which is
what the Usage page and the daily ceiling count. A successful answer is also written to the AI
store as a `generations` document (text, sources, tokens) under the same request_id as the usage
row; when the store is down the route still answers and `stored` is false.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from .. import ai_store, llm
from ..auth import CurrentUser, get_current_user
from ..config import settings
from ..db import fetch_all, fetch_one, get_db
from ..metering import record_usage
from ..schemas import SourceRef, StaySummary
from ..tenancy import load_patient

log = logging.getLogger("atlas.summary")
router = APIRouter(tags=["summary"])

FEATURE = "stay-summary"
MAX_LABS = 20
MAX_MEDICATIONS = 20

SYSTEM_PROMPT = """You write short clinical stay summaries for the clinician who is reading the record.
Rules:
- Use only the facts in the records you are given. Do not add findings, diagnoses or advice.
- Every number you write must be copied from the records exactly as written. Do not count rows,
  do not round, do not convert units.
- Name a medication only if it appears in the MED lines.
- Write 4 to 6 sentences of plain prose: no headings, no bullet points, no preamble.
- Use the past tense for a discharged stay and the present tense for an ongoing one."""


# ---------------------------------------------------------------- the grounding rows

@dataclass
class Grounding:
    """Everything the model is allowed to know, as rows (for sources) and lines (for the prompt)."""

    encounter: dict
    vitals: list[dict] = field(default_factory=list)
    labs: list[dict] = field(default_factory=list)
    medications: list[dict] = field(default_factory=list)

    @property
    def sources(self) -> list[SourceRef]:
        refs = [SourceRef(table="encounters", id=self.encounter["id"])]
        refs += [SourceRef(table="vitals", id=r["id"]) for r in self.vitals]
        refs += [SourceRef(table="labs", id=r["id"]) for r in self.labs]
        refs += [SourceRef(table="medications", id=r["id"]) for r in self.medications]
        return refs

    def lines(self) -> list[str]:
        """One plain-text line per row, ids included, so the model can be checked against them."""
        e = self.encounter
        out = [f"PATIENT: {e['age']}-year-old {e['sex']}"]
        stay = f"ENCOUNTER id={e['id']}: {e['admission_type']} visit, admitted {_when(e['admit_date'])} from {e['admit_source'].replace('_', ' ')}"
        if e["discharge_date"]:
            stay += f", discharged {_when(e['discharge_date'])} to {(e['discharge_disposition'] or 'unknown').replace('_', ' ')}"
            stay += f", length of stay {e['length_of_stay_hours']} hours"
        else:
            stay += ", still admitted"
        out.append(stay)
        for v in self.vitals:
            out.append(f"VITAL id={v['id']}: {v['measurement'].replace('_', ' ')} {_num(v['value'])} {v['unit']} at {_when(v['recorded_at'])} (last reading)")
        for lab in self.labs:
            flag = {"H": "HIGH", "L": "LOW"}.get(lab["flag"], "normal")
            out.append(
                f"LAB id={lab['id']}: {lab['test_name']} ({lab['panel']}) {_num(lab['value'])} {lab['unit']}, {flag}, "
                f"reference {_num(lab['ref_low'])} to {_num(lab['ref_high'])}, collected {_when(lab['collected_at'])}"
            )
        for m in self.medications:
            line = f"MED id={m['id']}: {m['drug_name']} {_num(m['dose'])} {m['dose_unit']} {m['route']} {m['frequency']}, ordered {_when(m['ordered_at'])}"
            # say when an order ended, or the model guesses ("discontinued on admission")
            line += f", discontinued {_when(m['ended_at'])}" if m["ended_at"] else ", active"
            out.append(line)
        return out


def _when(value) -> str:
    """Timestamps to the minute, in UTC, so the model has fewer digits to get wrong."""
    return value.strftime("%Y-%m-%d %H:%M") if value else "unknown"


def _num(value) -> str:
    """Numbers without a trailing .0, matching how the UI shows them."""
    if value is None:
        return "?"
    value = float(value)
    return str(int(value)) if value == int(value) else f"{value:g}"


def fetch_grounding(conn: Connection, tenant_id: int, patient_id: int) -> Grounding | None:
    """The latest encounter (ongoing or most recent) and its rows. None when the patient has no visit.

    `evals/make_golden.py` calls this too, so the golden set holds exactly what the route sends.
    The caller has already checked the patient belongs to `tenant_id`; every query repeats the
    tenant filter anyway.
    """
    encounter = fetch_one(
        conn,
        """SELECT e.id, e.admission_type, e.admit_source, e.admit_date, e.discharge_date, e.discharge_disposition,
                  round((extract(epoch FROM coalesce(e.discharge_date, now()) - e.admit_date) / 3600)::numeric, 1)::float
                      AS length_of_stay_hours,
                  p.age, p.sex
           FROM encounters e JOIN patients p ON p.id = e.patient_id
           WHERE e.patient_id = %s AND e.tenant_id = %s
           ORDER BY e.admit_date DESC, e.id DESC LIMIT 1""",
        (patient_id, tenant_id),
    )
    if encounter is None:
        return None
    p = {"e": encounter["id"], "t": tenant_id, "labs": MAX_LABS, "meds": MAX_MEDICATIONS}
    vitals = fetch_all(
        conn,
        """SELECT DISTINCT ON (v.measurement) v.id, v.measurement, v.value, v.unit, v.recorded_at
           FROM vitals v WHERE v.encounter_id = %(e)s AND v.tenant_id = %(t)s
           ORDER BY v.measurement, v.recorded_at DESC""",
        p,
    )
    labs = fetch_all(
        conn,
        """SELECT l.id, l.test_name, l.panel, l.value, l.unit, l.ref_low, l.ref_high, l.flag, l.collected_at
           FROM labs l WHERE l.encounter_id = %(e)s AND l.tenant_id = %(t)s
           ORDER BY (l.flag <> 'N') DESC, l.collected_at DESC, l.id LIMIT %(labs)s""",
        p,
    )
    # Orders are discontinued at discharge, so for a finished stay "active" would be empty: take
    # the stay's orders with the active ones first and say the status on each line.
    medications = fetch_all(
        conn,
        """SELECT m.id, m.drug_name, m.dose, m.dose_unit, m.route, m.frequency, m.status, m.ordered_at, m.ended_at
           FROM medications m WHERE m.encounter_id = %(e)s AND m.tenant_id = %(t)s
           ORDER BY (m.status = 'active') DESC, m.ordered_at DESC, m.id LIMIT %(meds)s""",
        p,
    )
    return Grounding(encounter=encounter, vitals=vitals, labs=labs, medications=medications)


# ---------------------------------------------------------------- the daily ceiling

def tokens_used_today(conn: Connection, tenant_id: int) -> int:
    """Input plus output tokens this tenant has spent since midnight UTC, across every feature.

    The ceiling is per tenant, not per feature: a second generative feature that calls this
    shares the same allowance. Predictions record zero tokens, so they add nothing here.

    This is a read-then-call check with no lock, so requests running at the same moment can
    each pass it and together overshoot the limit by up to one answer per request. For a
    laptop demo that is fine; the README lists it as a known gap.
    """
    row = fetch_one(
        conn,
        """SELECT coalesce(sum(input_tokens + output_tokens), 0) AS used
           FROM usage_events
           WHERE tenant_id = %s AND ts >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
        (tenant_id,),
    )
    return int(row["used"])


# ---------------------------------------------------------------- the route

@router.post("/api/patients/{patient_id}/summary", response_model=StaySummary)
def stay_summary(
    patient_id: int, user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)
) -> StaySummary:
    started = time.monotonic()
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant

    if not llm.configured():
        # metered like every other refusal, so the Usage page shows demand for a feature that is off
        record_usage(conn, user, feature=FEATURE, model=settings.llm_model, status="unconfigured", elapsed_ms=0)
        conn.commit()  # the error response below would otherwise roll this row back
        raise HTTPException(status_code=503, detail="no model configured")

    if tokens_used_today(conn, user.tenant_id) >= settings.llm_daily_token_limit:
        record_usage(conn, user, feature=FEATURE, model=settings.llm_model, status="limited", elapsed_ms=0)
        conn.commit()  # the error response below would otherwise roll this row back
        raise HTTPException(status_code=429, detail="daily token limit reached")

    grounding = fetch_grounding(conn, user.tenant_id, patient_id)
    if grounding is None:
        raise HTTPException(status_code=404, detail="not applicable: no encounter to summarise")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Records:\n" + "\n".join(grounding.lines()) + "\n\nWrite the summary."},
    ]
    try:
        text, input_tokens, output_tokens = llm.chat(messages)
    except Exception as exc:  # the SDK has many exception classes; all of them are "no answer"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.warning("summary for patient %s failed after %d ms: %s", patient_id, elapsed_ms, exc.__class__.__name__)
        record_usage(conn, user, feature=FEATURE, model=settings.llm_model, status="error", elapsed_ms=elapsed_ms)
        conn.commit()  # keep the failure on the Usage page even though we answer with an error
        raise HTTPException(status_code=502, detail="model endpoint error") from None

    elapsed_ms = int((time.monotonic() - started) * 1000)
    request_id = str(uuid.uuid4())  # shared by the Postgres usage row and the Mongo document
    record_usage(
        conn, user, feature=FEATURE, model=settings.llm_model,
        input_tokens=input_tokens, output_tokens=output_tokens, status="ok", elapsed_ms=elapsed_ms, request_id=request_id,
    )
    # Fire-and-forget: a store that is down costs the history entry, never the summary.
    stored = ai_store.record_generation(
        user.tenant_id, request_id, feature=FEATURE, model_version=settings.llm_model,
        patient_id=patient_id, encounter_id=grounding.encounter["id"], text=text,
        sources=[s.model_dump() for s in grounding.sources], input_tokens=input_tokens, output_tokens=output_tokens,
        status="ok", user_id=user.id,
    )
    return StaySummary(
        summary=text,
        sources=grounding.sources,
        model=settings.llm_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        elapsed_ms=elapsed_ms,
        encounter_id=grounding.encounter["id"],
        stored=stored,
    )
