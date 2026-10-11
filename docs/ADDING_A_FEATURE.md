# Adding a feature to Atlas

Every feature takes the same path: a backend route, a client function, and a panel. This page
walks through a small one end to end: a card on the patient record showing how many visits a
patient has had in the 90 days up to their most recent one. Swap the body of the route for a
model or a prompt and the path stays the same.

## 1. The route

Create `backend/app/routers/visits.py`:

```python
from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_one, get_db
from ..tenancy import load_patient

router = APIRouter(tags=["visits"])


@router.get("/api/patients/{patient_id}/recent-visits")
def recent_visits(patient_id: int, user: CurrentUser = Depends(get_current_user),
                  conn: Connection = Depends(get_db)) -> dict:
    load_patient(conn, user, patient_id)  # 404 unless the patient is in the caller's tenant
    row = fetch_one(
        conn,
        """WITH as_of AS (
               SELECT max(admit_date) AS t FROM encounters WHERE patient_id = %(p)s AND tenant_id = %(t)s)
           SELECT count(*) AS visits FROM encounters, as_of
           WHERE patient_id = %(p)s AND tenant_id = %(t)s AND admit_date > as_of.t - interval '90 days'""",
        {"p": patient_id, "t": user.tenant_id},
    )
    return {"patient_id": patient_id, "visits_90d": row["visits"]}
```

Why not `now() - interval '90 days'`? The Test Facility's data has fixed dates: its two-year
history ends on the day it was seeded and never moves, so a window counted back from the clock
would be empty within weeks. Atlas measures windows from `as_of`, the newest record in scope
(here the patient's latest admission; the dashboard uses `_as_of()` in
`backend/app/routers/dashboard.py`). For live facilities `as_of` is effectively now, so the same
query works for both. See "Timekeeping" in `docs/ARCHITECTURE.md`.

Three things every route needs:

- `Depends(get_current_user)`, so the route is not public.
- A tenant check before reading anything (`load_patient`, or `tenant_id = %s` in the SQL).
- Parameters bound with `%s`, never formatted into the SQL string.

Register it in `backend/app/main.py`:

```python
from .routers import visits
app.include_router(visits.router)
```

The backend reloads on save. Check it in http://localhost:8000/docs (press Authorize and
paste a token) or with curl:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"clinician@riverside.example","password":"atlas-demo"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s localhost:8000/api/patients/8/recent-visits -H "Authorization: Bearer $TOKEN"
```

## 2. The client function

Add one line to `frontend/src/api/client.js`:

```javascript
export const getRecentVisits = (id) => get(`/api/patients/${id}/recent-visits`);
```

## 3. The panel

Create `frontend/src/panels/RecentVisitsCard.jsx`, shaped like `VitalsPanel`:

```jsx
import { useEffect, useState } from "react";
import { getRecentVisits } from "../api/client";
import { Card, Retry, Spinner } from "../components";

export function RecentVisitsCard({ patientId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setData(null);
    setError(null);
    getRecentVisits(patientId).then(setData).catch(setError);
  }, [patientId]); // re-run when the patient changes

  return (
    <Card title="Visits, last 90 days">
      {error ? <Retry error={error} /> : !data ? <Spinner /> : <p className="text-3xl font-semibold">{data.visits_90d}</p>}
    </Card>
  );
}
```

Then add it to the panel list in `frontend/src/pages/PatientDetail.jsx`:

```jsx
<RecentVisitsCard patientId={patient.id} />
```

## 4. For AI features

The two features that ship with Atlas are the worked example. Read them in this order and copy
their shape rather than starting from the sketch above.

| Piece | Predictive: readmission risk | Generative: stay summary |
|---|---|---|
| Route | `backend/app/routers/risk.py` | `backend/app/routers/summary.py` |
| Shared logic | `backend/app/ml.py` (feature SQL, `load_model()`, bands) | `backend/app/llm.py` (`configured()`, `chat()`) |
| Client function | `getReadmissionRisk` in `frontend/src/api/client.js` | `postStaySummary` in the same file |
| Panel | `frontend/src/panels/RiskCard.jsx` | `frontend/src/panels/SummaryCard.jsx` |
| Tests | `backend/tests/test_risk.py` | `backend/tests/test_summary.py` (stubbed `llm.chat`) |
| Evidence | `training/` and `models/readmission.joblib` | `evals/golden.jsonl`, `evals/run_evals.py` |

Both panels are listed in the `AiInsights` section of `frontend/src/pages/PatientDetail.jsx`
and exported from `frontend/src/panels/index.js`.

### A model you trained

- Put the trained file in `models/` at the repository root. The backend sees the folder at
  `/app/models` (mounted by Compose, copied into the image for Kubernetes) and reads the file
  named by `MODEL_PATH`. Load it once per process and cache it, as `ml.load_model()` does;
  answer 503 when the file is missing instead of crashing at import.
- Define the feature frame once and use it twice: the training export imports the same
  function the route calls (`ml.encounter_features()` and `ml.build_features_for_patient()`),
  so the model never meets a column it was not trained on.
- Train on the Test Facility only (`facility_id` of the facility whose code is `DEMO`). Its
  dates are fixed, so derive labels and windows from the dates in the rows, never from today,
  and use only data available at the prediction time. Split grouped by `patient_id`.
- Define "not applicable" in words and return it as a 404 with a clear `detail` (for the risk
  route: the patient has no finished inpatient or observation stay). Define your bands or
  thresholds in one place with a comment.
- Pin the training packages to the same versions as `backend/requirements.txt`
  (`training/requirements-train.txt`) and run `docker compose up --build backend worker` after
  changing the backend pins.
- Record each prediction with `record_usage(conn, user, feature=..., model=..., predictions=1,
  elapsed_ms=...)`.

### A language model

- Read `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` through `settings`; never hard-code or log
  a key. Keys go in `.env` (Compose) or `k8s/secret.env` (cluster), both gitignored. Check
  `llm.configured()` first and answer 503 "no model configured" when there is no key.
- Fetch the rows yourself, for the caller's tenant, and put them in the prompt one per line
  with their ids (`summary.fetch_grounding()` and `Grounding.lines()`). No patient name. Return
  the same ids as `sources` so the UI can show where each fact came from.
- Check the tenant's token total for the day before calling (`tokens_used_today()` against
  `LLM_DAILY_TOKEN_LIMIT`) and answer 429 when it is spent.
- Record every call with `record_usage()`, including refusals (`status="limited"`) and
  failures (`status="error"`), with the token counts the endpoint reports. Call `conn.commit()`
  before raising an `HTTPException`, or FastAPI's rollback removes the row.
- Write a golden set from the same grounding function (`evals/make_golden.py`) and evaluators
  as code (`evals/run_evals.py`): every number in the output appears in the rows, no
  medication is named that is not in the rows, the required shape is present. Set a pass
  threshold and record the pass rate.

### The states a panel must show

A clinician will press the button or open the record when the model is down, the patient does
not qualify, or the budget is spent. The panel has to say so in words, not spin or show a
blank card. `RiskCard.jsx` and `SummaryCard.jsx` handle every row of this table:

| State | When | What the panel shows |
|---|---|---|
| Loading | the request is in flight | `Spinner` with a label ("Scoring this patient", "Summarising") |
| Result | 200 | the value with its context: band and probability plus "model trained on Test Facility data" and the model version; or the summary with a Sources toggle, model name, tokens and time |
| Low confidence | a result the model is unsure about (the medium band) | the result plus a note saying so |
| Not applicable | 404 with a `detail` that starts with "not applicable" | `Empty` explaining why, in plain words |
| Model unavailable | 503 (no model file, or no `LLM_API_KEY`) | `Empty` naming the cause and what would fix it |
| Daily limit reached | 429 | `Empty` saying the organisation's budget for today is spent |
| Any other error | network, 500, 502 | `Retry` with the error and a button that calls again |
| Idle (generative only) | before the clinician asks | a button and one sentence on what pressing it does; nothing is called until then |

Every card also carries the same cross-tenant guarantee as the rest of the record: another
organisation's patient id is a 404 before any model runs.

### Tests

Add a test next to `backend/tests/test_risk.py` or `test_summary.py` for every new route: the
happy path, each "not applicable" or refusal path, the model-missing or key-missing path, and
one that a user from the other organisation gets the same 404 as for a missing id. Stub the
model call in tests; never call a real endpoint from pytest.
