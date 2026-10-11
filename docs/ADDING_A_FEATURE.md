# Adding a feature to Atlas

Every feature takes the same path: a backend route, a client function, and a panel. This page
walks through a small one end to end: a card on the patient record showing how many visits a
patient has had in the last 90 days. Swap the body of the route for a model or a prompt and
the path stays the same.

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
        """SELECT count(*) AS visits FROM encounters
           WHERE patient_id = %s AND tenant_id = %s AND admit_date > now() - interval '90 days'""",
        (patient_id, user.tenant_id),
    )
    return {"patient_id": patient_id, "visits_90d": row["visits"]}
```

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

- **Models**: put trained files in `models/` at the repository root. The backend sees them at
  `/app/models`, and `.env` sets `MODEL_PATH=/app/models/readmission.joblib`. Add the
  libraries you need (scikit-learn, joblib) to `backend/requirements.txt`, pinned, and run
  `docker compose up --build backend worker`.
- **Language models**: read `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` from the
  environment. Keys go in `.env`, which is gitignored. Never commit one.
- **Usage**: call `record_usage()` from `backend/app/metering.py` once per call. It feeds the
  Usage page that admins see.
- **Training data**: connect a notebook to `postgresql://atlas:atlas@localhost:5433/atlas`.
  Train on the Test Facility (`facility_id` of the facility whose code is `DEMO`); watch the
  live facilities to see the feature react to new data.
- **Tests**: add a test next to `backend/tests/test_api.py`, including one that a user from
  the other organisation gets a 404.
