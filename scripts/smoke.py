"""Smoke check: is the whole Atlas stack up and answering?

    python scripts/smoke.py
    python scripts/smoke.py --api http://127.0.0.1:8000 --web http://127.0.0.1:3000 --feed http://127.0.0.1:8200

Standard library only, so it runs anywhere Python 3.10+ does. Exits non-zero on the first failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def call(url: str, token: str | None = None, body: dict | None = None, accept: str = "application/json") -> tuple[int, object]:
    headers = {"Accept": accept}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--web", default="http://127.0.0.1:3000")
    parser.add_argument("--feed", default="http://127.0.0.1:8200")
    parser.add_argument("--email", default="clinician@riverside.example")
    parser.add_argument("--password", default="atlas-demo")
    args = parser.parse_args()

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
        status, feed = call(f"{args.feed}/health")
        check("Meridian feed /health", status == 200, f"HTTP {status}")
        status, connections = call(f"{args.api}/api/integrations", token)
        receiving = [c for c in connections if c["high_watermark"] > 0] if status == 200 else []
        check("live facilities receiving events", len(receiving) > 0,
              f"{len(receiving)} of {len(connections) if status == 200 else '?'} connections have data")

    status, _ = call(args.web, accept="text/html")  # the dev server only serves the page to browsers
    check("frontend", status == 200, f"HTTP {status}")
    print("\nsmoke ok")


if __name__ == "__main__":
    main()
