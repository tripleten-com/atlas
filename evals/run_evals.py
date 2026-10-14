"""Run the stay-summary golden set against a live Atlas and grade each answer with code.

    python evals/run_evals.py                          # Compose defaults, prints a table and a pass rate
    python evals/run_evals.py --threshold 0.8          # exit 1 when the pass rate is below 80%
    python evals/run_evals.py --api http://localhost   # the Kubernetes Ingress

Three evaluators, all plain code, no model grading a model:

    numbers      every number in the summary appears in the grounding rows (small tolerance, and a
                 rounded grounding value counts, so "7.3 hours" may become "7 hours")
    medications  every drug name the summary mentions (from the catalogue /api/reference serves)
                 is one of the stay's orders
    sentences    the summary has 4 to 6 sentences

A case passes when all three pass. Each call spends real tokens and counts against the tenant's
daily ceiling. Standard library only for the run itself, so it runs wherever Python 3.10+ does.

When MONGO_URL is set in the environment the run is also written to the document store as an
`eval_runs` document (pass rate, threshold, one entry per case) through backend/app/ai_store.py,
which needs pymongo (`pip install -r backend/requirements.txt` in your venv). Pass --no-store to
skip that. From your machine the Compose store is
    MONGO_URL="mongodb://atlas:atlas-dev-only@localhost:27018/atlas_ai?authSource=admin"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# No minus sign: clinical values here are never negative, and "2026-09-11" must read as 2026, 9, 11.
NUMBER = re.compile(r"\d+(?:\.\d+)?")
SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
TOLERANCE = 0.01


# ---------------------------------------------------------------- talking to Atlas

def call(url: str, token: str | None = None, body: dict | None = None, post: bool = False) -> tuple[int, object]:
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if (post or body is not None) else "GET")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors="replace")
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


# ---------------------------------------------------------------- the evaluators

def numbers_in(text: str) -> list[float]:
    return [float(n) for n in NUMBER.findall(text)]


def number_is_grounded(n: float, grounding: list[float]) -> bool:
    """A number passes when a grounding value is the same (within a hair) or rounds to it."""
    return any(abs(n - g) <= TOLERANCE or n == round(g) for g in grounding)


def eval_numbers(summary: str, grounding: list[float]) -> tuple[bool, list[float]]:
    bad = [n for n in numbers_in(summary) if not number_is_grounded(n, grounding)]
    return not bad, bad


def eval_medications(summary: str, allowed: list[str], catalogue: list[str]) -> tuple[bool, list[str]]:
    """Which catalogue drug names does the text mention that were not among the stay's orders?"""
    lowered = summary.lower()
    allowed_lower = {a.lower() for a in allowed}
    bad = [name for name in catalogue if name.lower() in lowered and name.lower() not in allowed_lower]
    return not bad, bad


def count_sentences(summary: str) -> int:
    text = summary.strip()
    if not text:
        return 0
    # Decimal points ("7.3") are not followed by whitespace, so they do not end a sentence.
    ends = SENTENCE_END.findall(text)
    return len(ends) if ends else 1


def eval_sentences(summary: str) -> tuple[bool, int]:
    n = count_sentences(summary)
    return 4 <= n <= 6, n


# ---------------------------------------------------------------- the document store

def record_run(tenant_id: int, model: str, golden_path: Path, cases: int, passed: int, threshold: float | None,
               results: list[dict]) -> None:
    """Write one eval_runs document, so pass rates can be compared across model versions later.

    The backend module is imported here, not at the top, so the evaluators above stay standard
    library and a machine without pymongo can still run them with --no-store.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        from app import ai_store
    except ImportError as exc:
        print(f"not stored: {exc} (pip install -r backend/requirements.txt, or pass --no-store)")
        return
    stored = ai_store.record_eval_run(
        tenant_id, request_id=str(uuid.uuid4()), feature="stay-summary", model_version=model,
        golden_file_sha256=hashlib.sha256(golden_path.read_bytes()).hexdigest(),
        cases=cases, passed=passed, threshold=threshold, results=results,
    )
    print("stored in the document store (eval_runs)" if stored else "not stored: the document store is unavailable")


# ---------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="clinician@riverside.example")
    parser.add_argument("--password", default="atlas-demo")
    parser.add_argument("--golden", default=str(Path(__file__).resolve().parent / "golden.jsonl"))
    parser.add_argument("--threshold", type=float, default=None, help="exit 1 when the pass rate is below this (0 to 1)")
    parser.add_argument("--show", action="store_true", help="print each summary in full")
    parser.add_argument("--no-store", action="store_true", help="do not write the run to the document store even if MONGO_URL is set")
    args = parser.parse_args()

    cases = [json.loads(line) for line in Path(args.golden).read_text(encoding="utf-8").splitlines() if line.strip()]

    status, body = call(f"{args.api}/api/auth/login", body={"email": args.email, "password": args.password})
    if status != 200:
        sys.exit(f"login failed: HTTP {status} {body}")
    token = body["access_token"]
    tenant_id = body["user"]["tenant"]["id"]  # the eval_runs document is scoped to this tenant too

    status, reference = call(f"{args.api}/api/reference", token)
    if status != 200:
        sys.exit(f"could not load the drug catalogue: HTTP {status}")
    catalogue = [m["name"] for m in reference["medications"]]

    print(f"{'case':>4}  {'patient':>7}  {'enc':>6}  {'sent':>4}  {'numbers':>8}  {'meds':>5}  {'tokens':>11}  {'ms':>6}  result")
    passed = 0
    total_tokens = 0
    model = None
    results = []  # one entry per case, for the eval_runs document
    for i, case in enumerate(cases, 1):
        status, body = call(f"{args.api}/api/patients/{case['patient_id']}/summary", token, post=True)
        if status != 200:
            detail = body.get("detail") if isinstance(body, dict) else body
            print(f"{i:>4}  {case['patient_id']:>7}  {case['encounter_id']:>6}  {'-':>4}  {'-':>8}  {'-':>5}  {'-':>11}  {'-':>6}  FAIL  HTTP {status}: {detail}")
            results.append({"patient_id": case["patient_id"], "encounter_id": case["encounter_id"], "passed": False, "error": f"HTTP {status}"})
            continue
        summary = body["summary"]
        model = body["model"]
        if body["encounter_id"] != case["encounter_id"]:
            print(f"{i:>4}  {case['patient_id']:>7}  {case['encounter_id']:>6}  FAIL  the API summarised encounter {body['encounter_id']}; regenerate the golden set")
            results.append({"patient_id": case["patient_id"], "encounter_id": case["encounter_id"], "passed": False, "error": "encounter mismatch"})
            continue

        ok_numbers, bad_numbers = eval_numbers(summary, case["grounding_numbers"])
        ok_meds, bad_meds = eval_medications(summary, case["medications"], catalogue)
        ok_sentences, n_sentences = eval_sentences(summary)
        ok = ok_numbers and ok_meds and ok_sentences
        passed += ok
        tokens = f"{body['input_tokens']}+{body['output_tokens']}"
        total_tokens += body["input_tokens"] + body["output_tokens"]
        notes = []
        if bad_numbers:
            notes.append("numbers not in rows: " + ", ".join(f"{n:g}" for n in bad_numbers))
        if bad_meds:
            notes.append("drugs not in rows: " + ", ".join(bad_meds))
        if not ok_sentences:
            notes.append(f"{n_sentences} sentences")
        results.append({
            "patient_id": case["patient_id"], "encounter_id": case["encounter_id"], "passed": ok,
            "sentences": n_sentences, "bad_numbers": bad_numbers, "bad_medications": bad_meds,
            "input_tokens": body["input_tokens"], "output_tokens": body["output_tokens"], "elapsed_ms": body["elapsed_ms"],
        })
        print(f"{i:>4}  {case['patient_id']:>7}  {case['encounter_id']:>6}  {n_sentences:>4}  "
              f"{'ok' if ok_numbers else 'FAIL':>8}  {'ok' if ok_meds else 'FAIL':>5}  {tokens:>11}  {body['elapsed_ms']:>6}  "
              f"{'pass' if ok else 'FAIL'}  {'; '.join(notes)}")
        if args.show:
            print("      " + summary.replace("\n", " ") + "\n")

    rate = passed / len(cases) if cases else 0.0
    print(f"\npass rate {passed}/{len(cases)} = {rate:.0%}   tokens spent {total_tokens}")
    if os.environ.get("MONGO_URL") and not args.no_store:
        record_run(tenant_id, model or "unknown", Path(args.golden), len(cases), passed, args.threshold, results)
    if args.threshold is not None and rate < args.threshold:
        print(f"below the threshold of {args.threshold:.0%}")
        sys.exit(1)


if __name__ == "__main__":
    main()
