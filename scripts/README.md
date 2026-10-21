# scripts/

One check you run from the repository root with the stack up. It uses only the Python standard
library, so the Python you already have is enough (3.12 on the host is the course setting). It
needs no AWS login or GPU, and it never prints a key or a password.

| Script | What it is for |
|---|---|
| `smoke.py` | Is the whole stack up and answering? `python scripts/smoke.py` for Compose, `python scripts/smoke.py --base http://localhost` for the Kubernetes cluster through its Ingress. Exits 0 when every check passed or was skipped, 1 on the first failure, 2 when a route you passed cannot be read. |

## Checking your own routes

With no extra arguments, `smoke.py` checks only the worked examples. Add your own routes with
`--route`, once per route, each written as a method and a path. `{patient_id}` becomes the Test
Facility inpatient the script opened, and `{scored_patient_id}` the patient the readmission-risk
check scored:

```bash
python scripts/smoke.py --route "GET /api/patients/{patient_id}/risk/sepsis" --route "POST /api/patients/{patient_id}/discharge-draft"
```

The same list can live in the `SMOKE_ROUTES` environment variable, separated by semicolons, so
the cluster run uses it too:

```bash
SMOKE_ROUTES="GET /api/patients/{patient_id}/risk/sepsis;POST /api/patients/{patient_id}/discharge-draft" python scripts/smoke.py --base http://localhost
```

A route that reads its input from the request body (an agent that takes the clinician's
question, for example) carries that body as one JSON object after the path. Put the whole entry
in single quotes so the shell keeps the double quotes:

```bash
python scripts/smoke.py --route 'POST /api/patients/{patient_id}/ask {"question": "Why was this patient admitted?"}'
```

A body can go in `SMOKE_ROUTES` too, as long as it holds no semicolon.

Each route is called with the demo login's token and gets its own line. Any 2xx status is `ok`,
so 200 and the 202 of a queued job both pass. The honest states are `skip` lines: 404 with
"not applicable", 503 (no model file or no key) and 429 (daily limit reached). Any other status,
including the 422 FastAPI sends when a required body is missing, is a `FAIL` and the script
exits 1. Always write the method: Git Bash rewrites a lone argument that starts with `/` into a
Windows path.

Tasks 2, 3 and 4 close with the platform's automated check. It tests the lines of each task's
Check-list against your submitted repository and running stack, so the Check-list on the task
page is the full list of what it looks at. The repository ships no copy of it.
