"""Smoke check: is the whole Atlas stack up and answering?

    python scripts/smoke.py                         # Compose: API on 8000, web on 3000, Meridian on 8200
    python scripts/smoke.py --base http://localhost  # Kubernetes: everything behind the one Ingress
    python scripts/smoke.py --api http://127.0.0.1:8000 --web http://127.0.0.1:3000 --feed http://127.0.0.1:8200

What it checks, in order: /health, that patient data needs a token, the demo login, the
facilities list, a patient record, the dashboard, the Meridian feed (when reachable), live
deliveries, the readmission-risk route for a Test Facility patient, the stay-summary route
(skipped when the server has no LLM key), and the web app.

With --base, the API and the web app share one address. Meridian has no route through the
Ingress, so its own /health is skipped unless you pass --feed as well (for example after
`kubectl -n atlas port-forward svc/meridian 18200:8100`). The deliveries check still runs,
because it reads the API.

Your own routes: pass each with --route (repeat it), or list them in the SMOKE_ROUTES
environment variable separated by semicolons. A route is a method and a path, optionally
followed by a JSON object that is sent as the request body (a route that reads a question from
the body needs one). {patient_id} becomes the Test Facility inpatient the record check opened,
and {scored_patient_id} the patient the readmission-risk check scored:

    python scripts/smoke.py --route "GET /api/patients/{patient_id}/risk/sepsis"                             --route "POST /api/patients/{scored_patient_id}/discharge-draft"
    python scripts/smoke.py --route 'POST /api/patients/{patient_id}/ask {"question": "Why was she admitted?"}'
    SMOKE_ROUTES="GET /api/patients/{patient_id}/risk/sepsis;POST /api/patients/{patient_id}/discharge-draft"

Each one is called with the demo token. Any 2xx (200, or 202 for a queued job) is ok; the
honest states 404 "not applicable", 503 (no model or no key) and 429 (daily limit reached) are
skips; anything else fails. A body cannot contain a semicolon inside SMOKE_ROUTES; use --route
for that one. With no --route and no SMOKE_ROUTES, only the worked examples are checked.

Standard library only, so it runs anywhere Python 3.10+ does. Exits 0 when every check passed or
was skipped, 1 on the first failure, 2 when a --route or SMOKE_ROUTES entry cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(url: str, token: str | None = None, body: dict | None = None, accept: str = "application/json",
         method: str | None = None) -> tuple[int, object]:
    headers = {"Accept": accept}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if method is None:
        method = "POST" if body is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{f'  ({detail})' if detail else ''}")
    if not ok:
        print("\nsmoke check failed")
        sys.exit(1)


def skip(label: str, detail: str) -> None:
    """A check that could not run here, which is not a failure (for example no LLM key)."""
    print(f"  skip  {label}  ({detail})")


def detail_of(body: object) -> str:
    """The `detail` string FastAPI puts in an error body, or the body itself."""
    return body.get("detail", "") if isinstance(body, dict) else str(body)


METHODS = ("GET", "POST", "PUT", "PATCH")


def parse_routes(entries: list[str]) -> list[tuple[str, str, dict | None]]:
    """'POST /api/x {"question": "..."}' into ("POST", "/api/x", {"question": "..."}). The JSON
    body is optional. Exits 2 on an entry it cannot read. The method is required: Git Bash
    rewrites a lone argument that starts with / into a Windows path."""
    routes = []
    for entry in entries:
        parts = entry.strip().split(maxsplit=2)
        if not parts:
            continue
        if len(parts) < 2 or parts[0].upper() not in METHODS or not parts[1].startswith("/"):
            print(f"cannot read route {entry!r}: use 'GET /api/...' or 'POST /api/... {{\"question\": \"...\"}}'")
            sys.exit(2)
        body = None
        if len(parts) == 3:
            try:
                body = json.loads(parts[2])
            except ValueError:
                body = None
            if not isinstance(body, dict):
                print(f"cannot read the body of route {entry!r}: it must be one JSON object")
                sys.exit(2)
        routes.append((parts[0].upper(), parts[1], body))
    return routes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=None, help="one address for the API and the web app (the Kubernetes Ingress)")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--web", default="http://127.0.0.1:3000")
    parser.add_argument("--feed", default=None, help="Meridian's address; default 8200 on Compose, skipped with --base")
    parser.add_argument("--email", default="clinician@riverside.example")
    parser.add_argument("--password", default="atlas-demo")
    parser.add_argument("--no-summary", action="store_true", help="do not call the stay-summary route (it spends tokens)")
    parser.add_argument("--route", action="append", default=[], metavar='"METHOD /path"',
                        help="one of your own routes to check after the worked examples; repeat for more")
    args = parser.parse_args()
    extra = parse_routes(args.route + os.environ.get("SMOKE_ROUTES", "").split(";"))

    if args.base:
        base = args.base.rstrip("/")
        args.api, args.web = base, base
    elif args.feed is None:
        args.feed = "http://127.0.0.1:8200"

    print("Atlas smoke check")
    status, body = call(f"{args.api}/health")
    check("API /health", status == 200, f"HTTP {status}")

    status, body = call(f"{args.api}/api/patients")
    check("patient data requires a token", status == 401, f"HTTP {status}")

    status, body = call(f"{args.api}/api/auth/login", body={"email": args.email, "password": args.password})
    check("demo login", status == 200 and isinstance(body, dict) and "access_token" in body, f"HTTP {status}")
    token = body["access_token"]

    status, facilities = call(f"{args.api}/api/facilities", token)
    check("facilities", status == 200 and len(facilities) > 0, f"{len(facilities) if status == 200 else status} found")
    demo = next((f for f in facilities if f["kind"] == "demo"), None)
    check("Test Facility is seeded", bool(demo and demo["patient_count"] > 0),
          f"{demo['patient_count']} patients" if demo else "missing")

    status, page = call(f"{args.api}/api/patients?facility_id={demo['id']}&status=inpatient&page_size=1", token)
    check("patient list", status == 200 and page["total"] > 0, f"{page.get('total') if status == 200 else status} inpatients")
    patient_id = page["items"][0]["id"]

    status, patient = call(f"{args.api}/api/patients/{patient_id}", token)
    check("patient record", status == 200 and len(patient["latest_vitals"]) > 0, patient.get("name", "") if status == 200 else str(status))

    status, summary = call(f"{args.api}/api/dashboard/summary?facility_id={demo['id']}", token)
    check("dashboard", status == 200 and summary["patients_total"] > 0, f"HTTP {status}")

    live = [f for f in facilities if f["data_source"] == "meridian"]
    if live:
        if args.feed:
            status, feed = call(f"{args.feed}/health")
            check("Meridian feed /health", status == 200, f"HTTP {status}")
        else:
            skip("Meridian feed /health", "no --feed address; Meridian has no Ingress route")
        status, connections = call(f"{args.api}/api/integrations", token)
        receiving = [c for c in connections if c["high_watermark"] > 0] if status == 200 else []
        check("live facilities receiving events", len(receiving) > 0,
              f"{len(receiving)} of {len(connections) if status == 200 else '?'} connections have data")

    # The predictive feature. Not every patient has a discharged inpatient or observation stay
    # (the route answers 404 "not applicable" for the others), so try a page of Test Facility
    # patients who are not currently admitted until one is scored.
    status, page = call(f"{args.api}/api/patients?facility_id={demo['id']}&status=not_admitted&page_size=25", token)
    check("patients to score", status == 200 and page["total"] > 0, f"HTTP {status}")
    scored, tried, last = None, 0, ""
    for item in page["items"]:
        tried += 1
        status, risk = call(f"{args.api}/api/patients/{item['id']}/risk/readmission", token)
        if status == 200:
            scored = risk
            break
        last = f"HTTP {status} {detail_of(risk)}"
        if status != 404 or "not applicable" not in detail_of(risk):
            break  # 503 (no model file) or anything unexpected: stop and report it
    check("readmission risk", scored is not None and 0.0 <= scored["probability"] <= 1.0 and scored["band"] in ("low", "medium", "high"),
          f"patient {item['id']}: {scored['probability']} {scored['band']}, {scored['model_version']}" if scored
          else f"{last} after {tried} patients")

    # The generative feature. 503 means the server has no LLM_API_KEY, which is fine for a
    # fresh clone, so it is a skip rather than a failure. 429 means today's token ceiling is
    # spent; the feature is wired up, so that is a skip too.
    if args.no_summary:
        skip("stay summary", "--no-summary")
    else:
        status, result = call(f"{args.api}/api/patients/{patient_id}/summary", token, method="POST")
        if status == 503:
            skip("stay summary", f"server says: {detail_of(result)}")
        elif status == 429:
            skip("stay summary", f"server says: {detail_of(result)}")
        else:
            check("stay summary", status == 200 and bool(result.get("summary")) and len(result.get("sources", [])) > 0,
                  f"{result['input_tokens']}+{result['output_tokens']} tokens, {result['elapsed_ms']} ms, {len(result['sources'])} sources"
                  if status == 200 else f"HTTP {status} {detail_of(result)}")

    # The student's own routes, after the worked examples so a broken platform fails first.
    for method, path, route_body in extra:
        url = args.api + path.replace("{patient_id}", str(patient_id)).replace("{scored_patient_id}", str(item["id"]))
        status, result = call(url, token, body=route_body, method=method)
        label = f"{method} {path}" + (" (with body)" if route_body is not None else "")
        if status == 404 and "not applicable" in detail_of(result).lower():
            skip(label, f"server says: {detail_of(result)}")
        elif status in (429, 503):
            skip(label, f"HTTP {status}, server says: {detail_of(result)}")
        else:
            ok = 200 <= status < 300
            check(label, ok, f"HTTP {status}" + ("" if ok else f" {detail_of(result)}"))

    status, _ = call(args.web, accept="text/html")  # the dev server only serves the page to browsers
    check("web app", status == 200, f"HTTP {status}")
    print("\nsmoke ok")


if __name__ == "__main__":
    main()
