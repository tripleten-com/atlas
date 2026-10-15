"""The automated check for Tasks 2, 3 and 4. Run it before you submit; the reviewer platform runs
the same script, and its output goes into docs/REPORT.md.

    python scripts/check_submission.py --task 2                      # training evidence, both risk routes
    python scripts/check_submission.py --task 3                      # generative feature, document store
    python scripts/check_submission.py --task 4                      # Kubernetes manifests and the cluster
    python scripts/check_submission.py --task 2 --base http://localhost:8000 --repo .

One line per check: PASS, FAIL or WARN with the reason. Exit code 0 when no check failed, 1
otherwise. WARN never fails the run: it marks a cutline the spec allows (the readmission run labelled
`trained_on: local`; the GPU run has no cutline), or a check that could not run on this machine (no cluster, no LLM key,
Docker not available). scripts/README.md says what each task's check reads.

What it needs: Python 3.12 (standard library only), the Compose stack running at --base, and
`docker compose` on PATH for the checks that run pytest in the backend container and start a
second API process. Task 4 also uses `kubectl` and `git`. Logins use the two demo clinicians and
the demo admin with the password from DEMO_PASSWORD (default atlas-demo). The script never needs
an AWS login or a GPU, and it never prints a key or a password.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

RISK = "/api/patients/{}/risk/readmission"
EARLY_WARNING = "/api/patients/{}/risk/deterioration"
SUMMARY = "/api/patients/{}/summary"
HISTORY = "/api/patients/{}/ai/history"
MISSING_PATIENT_ID = 999_999_999  # an id the seed never reaches, for "same 404 as a missing patient"
SECRET_KEYS = ["POSTGRES_PASSWORD", "JWT_SECRET", "MERIDIAN_API_KEY", "LLM_API_KEY", "MONGO_USER", "MONGO_PASSWORD"]
EARLY_WARNING_FIELDS = {
    "probability", "band", "alert", "model_version", "trained_on", "trained_at", "as_of", "encounter_id",
    "hours_admitted", "hours_of_vitals", "window_start", "window_end", "state",
}
READMISSION_FIELDS = {"probability", "band", "model_version", "trained_on", "as_of", "encounter_id", "outcome"}
ALT_CONTAINER = "atlas-check-api"  # the second API process the Task 3 "store down" check starts


# ---------------------------------------------------------------- reporting

class Report:
    """Collects PASS / FAIL / WARN lines and decides the exit code."""

    def __init__(self) -> None:
        self.counts = {"PASS": 0, "FAIL": 0, "WARN": 0}

    def line(self, status: str, label: str, detail: str = "") -> None:
        self.counts[status] += 1
        print(f"{status}  {label}" + (f"  ({detail})" if detail else ""), flush=True)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.line("PASS" if ok else "FAIL", label, detail)
        return ok

    def warn(self, label: str, reason: str) -> None:
        self.line("WARN", label, reason)

    def finish(self) -> int:
        c = self.counts
        print(f"\n{c['PASS']} passed, {c['WARN']} warnings, {c['FAIL']} failed")
        return 1 if c["FAIL"] else 0


# ---------------------------------------------------------------- talking to Atlas

def call(url: str, token: str | None = None, body: dict | None = None, method: str | None = None,
         timeout: int = 90) -> tuple[int, object]:
    """One HTTP call. Returns (status, parsed JSON or text); status 0 when nothing answered."""
    headers = {"Accept": "application/json"}
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(getattr(exc, "reason", exc))
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def detail_of(body: object) -> str:
    """The `detail` string FastAPI puts in an error body, or the body itself, shortened."""
    text = body.get("detail", "") if isinstance(body, dict) else str(body)
    return str(text)[:120]


class Atlas:
    """The running API at --base and the three demo logins."""

    def __init__(self, base: str, args: argparse.Namespace) -> None:
        self.base = base.rstrip("/")
        self.args = args
        self.password = os.environ.get("DEMO_PASSWORD", "atlas-demo")
        self._tokens: dict[str, str | None] = {}
        self._demo_facility_id: int | None = None

    def login(self, email: str) -> str | None:
        """A bearer token for one demo user; None when the login fails. Cached per email."""
        if email not in self._tokens:
            status, body = call(f"{self.base}/api/auth/login", body={"email": email, "password": self.password})
            self._tokens[email] = body["access_token"] if status == 200 and isinstance(body, dict) else None
        return self._tokens[email]

    @property
    def clinician(self) -> str | None:
        return self.login(self.args.email)

    @property
    def other_tenant(self) -> str | None:
        return self.login(self.args.other_email)

    @property
    def admin(self) -> str | None:
        return self.login(self.args.admin_email)

    def get(self, path: str, token: str | None = None, **kwargs) -> tuple[int, object]:
        return call(f"{self.base}{path}", token, **kwargs)

    def demo_facility_id(self) -> int | None:
        """The Test Facility (code DEMO): the static data the models were trained on."""
        if self._demo_facility_id is None:
            status, facilities = self.get("/api/facilities", self.clinician)
            if status == 200:
                self._demo_facility_id = next((f["id"] for f in facilities if f.get("code") == "DEMO"), None)
        return self._demo_facility_id

    def demo_patients(self, status_filter: str, page_size: int = 25) -> list[int]:
        """Ids of Test Facility patients with one admission status (inpatient, not_admitted, ...)."""
        facility = self.demo_facility_id()
        if facility is None:
            return []
        status, page = self.get(f"/api/patients?facility_id={facility}&status={status_filter}&page_size={page_size}",
                                self.clinician)
        return [item["id"] for item in page["items"]] if status == 200 else []

    def first_scored(self, route: str, status_filter: str, accept=lambda body: True) -> tuple[int | None, object, str]:
        """Try Test Facility patients until a route answers 200. Returns (patient_id, body, last_error)."""
        last = ""
        for patient_id in self.demo_patients(status_filter):
            status, body = self.get(route.format(patient_id), self.clinician)
            if status == 200 and accept(body):
                return patient_id, body, ""
            last = f"HTTP {status} {detail_of(body)}"
            if status != 404 or "not applicable" not in detail_of(body):
                break  # 503 (no model) or anything unexpected: no point trying more patients
        return None, None, last


# ---------------------------------------------------------------- running other programs

def run(cmd: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str]:
    """Run a command and return (exit code, combined output). Exit -1 when it cannot start."""
    try:
        done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError:
        return -1, f"{cmd[0]} is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout} s"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def compose_available(repo: Path) -> bool:
    code, _ = run(["docker", "compose", "ps", "--quiet"], repo, timeout=60)
    return code == 0


def last_line(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return lines[-1][:160] if lines else "(no output)"


def pytest_in_container(report: Report, repo: Path, label: str, expression: str) -> None:
    """Run part of the backend test suite inside the backend container, against the running stack."""
    if not compose_available(repo):
        report.warn(label, "docker compose is not available here, so pytest did not run")
        return
    # `-o addopts=` drops the -q the repository's pytest.ini adds; two -q would hide the summary line
    code, output = run(["docker", "compose", "exec", "-T", "backend", "pytest", "-o", "addopts=", "-q", "tests", "-k", expression], repo)
    if code == 5:  # pytest: no tests were collected
        report.check(label, False, f'no test matched -k "{expression}"')
        return
    # the "N passed, M deselected in 3.2s" line, not whatever pytest printed after it
    summary = [line.strip() for line in output.splitlines() if re.search(r"\d+ (passed|failed|error)", line)]
    report.check(label, code == 0, summary[-1][:160] if summary else last_line(output))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_env_values(path: Path, keys: list[str]) -> dict[str, str]:
    """Values of a few keys from a KEY=value file. Read quietly; the values are never printed."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            if key.strip() in keys:
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# ================================================================ Task 2

def load_runs(repo: Path) -> list[tuple[Path, dict]]:
    runs = []
    for path in sorted(repo.glob("training/runs/*/training_run.json")):
        try:
            runs.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            runs.append((path, {}))
    return runs


def rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def check_run_evidence(report: Report, repo: Path, runs: list[tuple[Path, dict]]) -> None:
    """The two training_run.json files and the artifacts they describe."""
    gpu_runs = [(p, r) for p, r in runs if r.get("model") == "deterioration_12h"]
    question_runs = [(p, r) for p, r in runs if r.get("model") != "deterioration_12h" and r.get("question")]
    report.check("training_run.json for the student's clinical question",
                 bool(question_runs),
                 ", ".join(f"{rel(repo, p)}: question {r['question']}" for p, r in question_runs)
                 or "no training/runs/*/training_run.json with a `question`")
    report.check("training_run.json for the early-warning model (model: deterioration_12h)",
                 bool(gpu_runs), ", ".join(rel(repo, p) for p, r in gpu_runs) or "none found")

    # The committed artifact is the file in models/ the run names (`artifact_file`); a run without
    # that field falls back to the .onnx for the early-warning model, or to whichever file hashes
    # to artifact_sha256 for the student's own model (the name is theirs, the hash is the proof).
    # Two runs may not point at the same file: one artifact cannot vouch for two trainings.
    claimed: dict[str, str] = {}
    for path, record in question_runs + gpu_runs:
        expected = str(record.get("artifact_sha256", "")).lower()
        artifact = artifact_for(repo, record, expected)
        actual = sha256_of(artifact) if artifact and artifact.exists() else ""
        label = f"committed artifact matches artifact_sha256 in {rel(repo, path)}"
        if artifact is None or not artifact.exists():
            report.check(label, False, f"no file in models/ for this run (artifact_file {record.get('artifact_file')!r}, "
                                       f"sha256 {expected[:12]}...)")
        elif actual != expected:
            report.check(label, False, f"{rel(repo, artifact)} has sha256 {actual[:12]}..., the run says {expected[:12]}...")
        elif actual in claimed:
            report.check(label, False, f"{rel(repo, artifact)} is already the artifact of {claimed[actual]}")
        else:
            claimed[actual] = rel(repo, path)
            report.check(label, True, rel(repo, artifact))
        trained_on = record.get("trained_on")
        if record.get("model") == "deterioration_12h":
            # GPU training is a required part of the project: no cutline for this run.
            instance = str(record.get("instance_hint") or "")
            report.check(f"trained on an AWS GPU instance ({rel(repo, path)})",
                         trained_on == "ec2" and instance.startswith("i-"),
                         f"trained_on {trained_on!r}, instance {instance or None}; a laptop or Colab run does not count")
        elif trained_on == "ec2":
            report.check(f"trained on AWS ({rel(repo, path)})", True,
                         f"instance {record.get('instance_hint')}; the claim itself is checked from the screenshots, not here")
        elif trained_on == "local":
            report.warn(f"trained on AWS ({rel(repo, path)})", "trained_on is local: the cutline, reported not failed")
        else:
            report.check(f"trained on AWS ({rel(repo, path)})", False, f"trained_on is {trained_on!r}, expected ec2")
        check_metrics(report, repo, path, record)

    for path, record in gpu_runs:
        check_gpu_timing(report, repo, path, record)


def artifact_for(repo: Path, record: dict, expected_sha256: str) -> Path | None:
    """The models/ file a run record stands for (see check_run_evidence for the three rules)."""
    models = repo / "models"
    named = record.get("artifact_file")
    if named:
        return models / Path(str(named)).name  # the run writes a bare file name; never walk out of models/
    if record.get("model") == "deterioration_12h":
        onnx = sorted(models.glob("*.onnx"))
        return onnx[0] if onnx else None
    for candidate in sorted(models.glob("*")):
        if candidate.suffix in (".joblib", ".pkl", ".pt") and sha256_of(candidate) == expected_sha256:
            return candidate
    return None


def check_metrics(report: Report, repo: Path, path: Path, record: dict) -> None:
    metrics, train = record.get("metrics") or {}, record.get("train_metrics") or {}
    calibration = record.get("calibration")
    problems = []
    for key in ("auroc", "average_precision", "brier"):
        if not isinstance(metrics.get(key), int | float):
            problems.append(f"metrics.{key} missing")
    if not isinstance(train.get("auroc"), int | float):
        problems.append("train_metrics.auroc missing")
    if not (isinstance(calibration, list) and len(calibration) == 10):
        problems.append(f"calibration has {len(calibration) if isinstance(calibration, list) else 'no'} bins, expected 10")
    detail = ", ".join(problems) or f"test AUROC {metrics.get('auroc')}, train AUROC {train.get('auroc')}, 10 calibration bins"
    report.check(f"test metrics, train AUROC and a ten-bin calibration table in {rel(repo, path)}", not problems, detail)


def check_gpu_timing(report: Report, repo: Path, path: Path, record: dict) -> None:
    label = f"GPU run shows a GPU and a faster CUDA epoch ({rel(repo, path)})"
    gpu_name = record.get("gpu_name")
    seconds = record.get("epoch_seconds") or {}
    cpu, cuda = seconds.get("cpu"), seconds.get("cuda")
    if not gpu_name or record.get("device") != "cuda":
        report.check(label, False, f"gpu_name {gpu_name!r}, device {record.get('device')!r}: the run did not train on a GPU")
    elif cpu is None or cuda is None:
        report.check(label, False, "epoch_seconds has no cpu and cuda pair: run train_deterioration.py with --time-one-epoch")
    else:
        report.check(label, cuda < cpu, f"{gpu_name}: one epoch cpu {cpu} s, cuda {cuda} s")


def check_route_auth(report: Report, atlas: Atlas) -> None:
    """Both risk routes refuse anonymous calls and hide the other tenant's patients."""
    for name, route in (("readmission", RISK), ("deterioration", EARLY_WARNING)):
        status, _ = atlas.get(route.format(1))
        report.check(f"{name} route answers 401 without a token", status == 401, f"HTTP {status}")

    if atlas.clinician is None or atlas.other_tenant is None:
        report.check("two demo logins", False, "a demo clinician login failed; check DEMO_PASSWORD")
        return
    patients = atlas.demo_patients("inpatient", 1) or atlas.demo_patients("any", 1)
    if not patients:
        report.check("a Test Facility patient to test with", False, "no patient in the DEMO facility")
        return
    status_missing, missing = atlas.get(RISK.format(MISSING_PATIENT_ID), atlas.other_tenant)
    for name, route in (("readmission", RISK), ("deterioration", EARLY_WARNING)):
        status, body = atlas.get(route.format(patients[0]), atlas.other_tenant)
        same_as_missing = status == 404 and status_missing == 404 and detail_of(body) == detail_of(missing)
        report.check(f"{name} route answers 404 for the other tenant's patient", same_as_missing,
                     f"HTTP {status} {detail_of(body)}" + ("" if same_as_missing else f"; missing patient gives HTTP {status_missing} {detail_of(missing)}"))


def check_route_answers(report: Report, atlas: Atlas) -> None:
    """A Test Facility patient gets a score, or one of the documented honest answers."""
    def shaped(fields: set[str]):
        return lambda body: isinstance(body, dict) and fields <= set(body)

    patient_id, risk, last = atlas.first_scored(RISK, "not_admitted", shaped(READMISSION_FIELDS))
    label = "readmission route scores a Test Facility patient"
    if risk is not None:
        ok = 0.0 <= risk["probability"] <= 1.0 and risk["band"] in ("low", "medium", "high")
        report.check(label, ok, f"patient {patient_id}: {risk['probability']} {risk['band']}, {risk['model_version']}, "
                                f"outcome {risk['outcome']}, stored {risk.get('stored', '?')}")
    elif "503" in last or "not applicable" in last:
        report.warn(label, f"documented answer only, no score: {last}")
    else:
        report.check(label, False, last or "no discharged Test Facility patient to try")

    patient_id, warning, last = atlas.first_scored(EARLY_WARNING, "inpatient", shaped(EARLY_WARNING_FIELDS))
    label = "deterioration route answers for an admitted Test Facility patient"
    if warning is not None:
        state = warning["state"]
        ok = state in ("scored", "insufficient_data", "deteriorating")
        if state == "scored":
            ok = ok and warning["probability"] is not None and 0.0 <= warning["probability"] <= 1.0 and warning["band"] in ("low", "medium", "high")
        report.check(label, ok, f"patient {patient_id}: state {state}, probability {warning['probability']}, {warning['model_version']}, "
                                f"stored {warning.get('stored', '?')}")
    elif "503" in last or "not applicable" in last:
        report.warn(label, f"documented answer only, no score: {last}")
    else:
        report.check(label, False, last or "no admitted Test Facility patient to try")


def check_bundle_has_no_secrets(report: Report, repo: Path) -> None:
    """The built frontend must not contain the LLM key or the MongoDB password."""
    label = "frontend bundle contains neither LLM_API_KEY nor MONGO_PASSWORD"
    dist = repo / "frontend" / "dist"
    build_note = "existing frontend/dist"
    if compose_available(repo):
        # Build fresh so the check reads the code that is in the repository now.
        code, output = run(["docker", "compose", "exec", "-T", "frontend", "npx", "vite", "build"], repo, timeout=600)
        build_note = "fresh vite build" if code == 0 else f"existing frontend/dist; vite build did not run: {last_line(output)}"
    if not dist.exists():
        report.warn(label, "frontend/dist is missing: run `docker compose exec frontend npx vite build` first")
        return
    # The values come from the student's own .env (read quietly, never printed) and from the
    # committed placeholders, so a bundle that leaks either is caught.
    secrets: set[str] = set()
    for env_file in (repo / ".env", repo / ".env.example", repo / "k8s" / "secret.env", repo / "k8s" / "base" / "secret.example.env"):
        secrets.update(v for v in read_env_values(env_file, ["LLM_API_KEY", "MONGO_PASSWORD"]).values() if len(v) >= 6)
    leaked: set[str] = set()
    scanned = 0
    for file in dist.rglob("*"):
        if file.suffix not in (".js", ".html", ".css", ".json", ".map"):
            continue
        scanned += 1
        text = file.read_text(encoding="utf-8", errors="replace")
        if any(value in text for value in secrets):
            leaked.add(f"a secret value in {file.name}")
        if "LLM_API_KEY=" in text or "MONGO_PASSWORD=" in text:
            leaked.add(f"an env assignment line in {file.name}")
    report.check(label, not leaked, f"{scanned} bundle files scanned against {len(secrets)} values, {build_note}"
                 if not leaked else f"found {', '.join(sorted(leaked))}")


def task2(report: Report, repo: Path, atlas: Atlas) -> None:
    check_run_evidence(report, repo, load_runs(repo))
    pytest_in_container(report, repo, 'pytest backend/tests -k "risk or early_warning"', "risk or early_warning")
    check_route_auth(report, atlas)
    check_route_answers(report, atlas)
    check_bundle_has_no_secrets(report, repo)


# ================================================================ Task 3

def usage_events(atlas: Atlas) -> tuple[int | None, list]:
    """How many usage_events rows the tenant has today, from the Usage page's route (admin only)."""
    if atlas.admin is None:
        return None, []
    status, usage = atlas.get("/api/usage?days=1", atlas.admin)
    return (usage["total_events"], usage["recent"]) if status == 200 else (None, [])


def check_generative_route(report: Report, atlas: Atlas) -> int:
    """One summary call: the route answers and leaves exactly one metering row. Returns its status."""
    patients = atlas.demo_patients("not_admitted", 1) or atlas.demo_patients("any", 1)
    if not patients:
        report.check("generative route answers", False, "no Test Facility patient to summarise")
        return 0
    before, _ = usage_events(atlas)
    status, body = atlas.get(SUMMARY.format(patients[0]), atlas.clinician, method="POST", timeout=120)
    after, recent = usage_events(atlas)

    label = "generative route answers"
    if status == 200:
        tokens = body.get("input_tokens", 0) + body.get("output_tokens", 0)
        report.check(label, bool(body.get("summary")) and tokens > 0 and len(body.get("sources", [])) > 0,
                     f"{tokens} tokens, {len(body.get('sources', []))} source rows, {body.get('elapsed_ms')} ms, stored {body.get('stored')}")
    elif status == 429:
        report.warn(label, "429: today's token ceiling is already spent, so the answer itself was not checked")
    elif status == 503:
        report.warn(label, f"503 {detail_of(body)}: this server has no LLM key, so the answer itself was not checked")
    else:
        report.check(label, False, f"HTTP {status} {detail_of(body)}")

    label = "usage_events grows by one row with tokens or a refusal status"
    if before is None or after is None:
        report.warn(label, "could not read /api/usage as the demo admin; check DEMO_PASSWORD and --admin-email")
    else:
        newest = recent[0] if recent else {}
        metered = (newest.get("input_tokens", 0) + newest.get("output_tokens", 0)) > 0 or newest.get("status") != "ok"
        report.check(label, after == before + 1 and metered,
                     f"{before} -> {after} rows; newest: {newest.get('feature')} status {newest.get('status')}, "
                     f"{newest.get('input_tokens')}+{newest.get('output_tokens')} tokens")
    return status


def check_golden_and_evals(report: Report, repo: Path, atlas: Atlas, summary_status: int, threshold: float) -> None:
    golden = repo / "evals" / "golden.jsonl"
    lines = [line for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()] if golden.exists() else []
    report.check("evals/golden.jsonl has at least ten cases", len(lines) >= 10, f"{len(lines)} lines")

    label = f"evals/run_evals.py exits 0 at threshold {threshold}"
    runner = repo / "evals" / "run_evals.py"
    if not runner.exists():
        report.check(label, False, "evals/run_evals.py is missing")
    elif summary_status in (429, 503):
        report.warn(label, "skipped: the generative route cannot answer on this server (see above)")
    else:
        code, output = run([sys.executable, str(runner), "--api", atlas.base, "--email", atlas.args.email,
                            "--password", atlas.password, "--threshold", str(threshold), "--no-store"], repo, timeout=1200)
        report.check(label, code == 0, last_line(output))


def check_history(report: Report, atlas: Atlas) -> int | None:
    """A fresh prediction shows up in the caller's history and nowhere else. Returns the patient id."""
    patient_id, risk, last = atlas.first_scored(RISK, "not_admitted")
    if risk is None:
        report.check("ai/history returns the prediction just written", False, f"no scored patient: {last}")
        return None
    status, history = atlas.get(HISTORY.format(patient_id) + "?feature=readmission-risk&limit=5", atlas.clinician)
    history = history if isinstance(history, dict) else {}
    newest = history["items"][0] if status == 200 and history.get("items") else {}
    fresh = False
    if newest.get("created_at"):
        created = datetime.fromisoformat(newest["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        fresh = datetime.now(UTC) - created < timedelta(minutes=5)
    matches = (newest.get("feature") == "readmission-risk" and newest.get("model_version") == risk["model_version"]
               and newest.get("probability") is not None and abs(newest["probability"] - risk["probability"]) < 1e-6)
    # `stored: true` on the score itself is part of the contract (spec 12): a route that writes the
    # document but reports false, or reports true without writing, fails here.
    report.check("ai/history returns the prediction just written",
                 status == 200 and risk.get("stored") is True and history.get("stored") is True and matches and fresh,
                 f"HTTP {status}, score stored {risk.get('stored', '?')}, history stored {history.get('stored', '?')}, "
                 f"{len(history.get('items', []))} items, newest {newest.get('created_at', '-')}")

    status, body = atlas.get(HISTORY.format(patient_id) + "?feature=readmission-risk", atlas.other_tenant)
    report.check("ai/history answers 404 for the other tenant", status == 404, f"HTTP {status} {detail_of(body)}")
    return patient_id


def start_alt_api(repo: Path, port: int) -> tuple[bool, str]:
    """Start a second API process in a fresh backend container with the store unreachable and
    the daily ceiling at one token. Returns (started, reason). The caller stops it."""
    if not compose_available(repo):
        return False, "docker compose is not available here"
    run(["docker", "rm", "-f", ALT_CONTAINER], repo, timeout=60)  # a leftover from an interrupted run
    cmd = ["docker", "compose", "run", "-d", "--no-deps", "--name", ALT_CONTAINER, "-p", f"{port}:8000",
           "-e", "MONGO_URL=mongodb://127.0.0.1:1/atlas_ai",  # port 1 is closed: the store is "down"
           "-e", "LLM_DAILY_TOKEN_LIMIT=1",
           "backend", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    code, output = run(cmd, repo, timeout=180)
    if code != 0:
        return False, f"docker compose run failed: {last_line(output)}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, _ = call(f"http://127.0.0.1:{port}/health", timeout=5)
        if status == 200:
            return True, ""
        time.sleep(1)
    return False, "the second API process did not answer /health within 90 s"


def stop_alt_api(repo: Path) -> None:
    run(["docker", "rm", "-f", ALT_CONTAINER], repo, timeout=60)


def check_with_alt_api(report: Report, repo: Path, atlas: Atlas, args: argparse.Namespace, patient_id: int | None) -> None:
    """The checks that need their own API process: the store-down fallback and the 429 ceiling."""
    labels = ("risk route answers 200 with stored: false while the store is down",
              "daily ceiling returns 429 with LLM_DAILY_TOKEN_LIMIT=1")
    started, reason = start_alt_api(repo, args.alt_port)
    if not started:
        for label in labels:
            report.warn(label, reason)
        stop_alt_api(repo)
        return
    try:
        alt = Atlas(f"http://127.0.0.1:{args.alt_port}", args)
        status, health = alt.get("/health")
        store = health.get("ai_store") if isinstance(health, dict) else "?"
        if patient_id is None:
            report.check(labels[0], False, "no scored patient from the history check")
        else:
            status, risk = alt.get(RISK.format(patient_id), alt.clinician)
            risk = risk if isinstance(risk, dict) else {}
            report.check(labels[0], status == 200 and risk.get("stored") is False,
                         f"HTTP {status}, stored {risk.get('stored', '?')}; /health ai_store {store}")
        check_ceiling(report, alt, labels[1])
    finally:
        stop_alt_api(repo)


def check_ceiling(report: Report, alt: Atlas, label: str) -> None:
    """With a one-token ceiling the first call may still go through (nothing spent yet today);
    the second must be refused."""
    patients = alt.demo_patients("not_admitted", 1) or alt.demo_patients("any", 1)
    if not patients:
        report.check(label, False, "no Test Facility patient to summarise")
        return
    statuses: list[int] = []
    body: object = None
    for _ in range(2):
        status, body = alt.get(SUMMARY.format(patients[0]), alt.clinician, method="POST", timeout=120)
        statuses.append(status)
        if status in (429, 503):
            break
    if 429 in statuses:
        report.check(label, True, f"HTTP {' then '.join(map(str, statuses))}")
    elif 503 in statuses:
        report.warn(label, f"503 {detail_of(body)}: this server has no LLM key, so the ceiling was not reached")
    else:
        report.check(label, False, f"HTTP {' then '.join(map(str, statuses))}, expected 429")


def task3(report: Report, repo: Path, atlas: Atlas, args: argparse.Namespace) -> None:
    if atlas.clinician is None or atlas.other_tenant is None:
        report.check("two demo logins", False, "a demo clinician login failed; check DEMO_PASSWORD")
        return
    summary_status = check_generative_route(report, atlas)
    check_golden_and_evals(report, repo, atlas, summary_status, args.eval_threshold)
    patient_id = check_history(report, atlas)
    status, health = atlas.get("/health")
    report.check("/health reports ai_store: ok", status == 200 and isinstance(health, dict) and health.get("ai_store") == "ok",
                 f"HTTP {status}, ai_store {health.get('ai_store') if isinstance(health, dict) else '?'}")
    check_with_alt_api(report, repo, atlas, args, patient_id)
    pytest_in_container(report, repo, 'tenant-isolation tests pass (pytest -k "tenant")', "tenant")


# ================================================================ Task 4

def strip_comments(text: str) -> str:
    """Drop every comment line. The checks below are text searches, so a commented-out
    `readinessProbe:` (or a header comment that mentions one) must not count as a probe."""
    return re.sub(r"(?m)^\s*#.*\n?", "", text)


def split_documents(text: str) -> list[str]:
    return [doc for doc in re.split(r"(?m)^---\s*$", strip_comments(text)) if doc.strip()]


def yaml_documents(path: Path) -> list[str]:
    """Split a manifest file into documents, comments removed. No YAML parser: standard library only."""
    return split_documents(path.read_text(encoding="utf-8", errors="replace"))


def doc_kind_and_name(doc: str) -> tuple[str, str]:
    kind = re.search(r"(?m)^kind:\s*(\S+)", doc)
    name = re.search(r"(?m)^metadata:\n(?:  .*\n)*?  name:\s*(\S+)", doc)
    return (kind.group(1) if kind else "?", name.group(1) if name else "?")


def check_manifests(report: Report, repo: Path) -> None:
    base = repo / "k8s" / "base"
    manifests = sorted(base.glob("*.yaml"))
    report.check("k8s/base holds manifests", bool(manifests), f"{len(manifests)} files")

    label = "kubectl kustomize k8s/base renders"
    code, rendered = run(["kubectl", "kustomize", str(base)], repo, timeout=120)
    if code == -1:
        report.warn(label, "kubectl is not installed; the files were checked directly")
        rendered = ""
    else:
        report.check(label, code == 0, f"{rendered.count('kind:')} objects" if code == 0 else last_line(rendered))

    # The probe checks read what the cluster would get: the rendered output when kustomize ran,
    # otherwise the source files with their comments removed (a Deployment split over two files
    # or patched by an overlay is only complete after rendering).
    if rendered and code == 0:
        docs = [("kustomize output", *doc_kind_and_name(doc), doc) for doc in split_documents(rendered)]
    else:
        docs = [(path.name, *doc_kind_and_name(doc), doc) for path in manifests for doc in yaml_documents(path)]
    for source, kind, name, doc in docs:
        if kind != "Deployment":
            continue
        has = {probe: re.search(rf"(?m)^\s+{probe}Probe:", doc) is not None for probe in ("readiness", "liveness")}
        report.check(f"Deployment {name} has readiness and liveness probes", all(has.values()),
                     f"{source}: missing {', '.join(k for k, v in has.items() if not v)}" if not all(has.values()) else source)
        if name == "api":
            report.check("Deployment api has a startup probe", re.search(r"(?m)^\s+startupProbe:", doc) is not None, source)
    if not any(kind == "Deployment" and name == "api" for _, kind, name, _ in docs):
        report.check("Deployment api has a startup probe", False, "no Deployment named api")

    # an `image: name:latest` line, or a kustomize `newTag: latest` (comments do not count)
    latest = re.compile(r"(?m)^\s*(?:-\s+)?image:\s*\S+:latest\b|^\s*newTag:\s*latest\b")
    offenders = [path.name for path in manifests if latest.search(strip_comments(path.read_text(encoding="utf-8", errors="replace")))]
    if rendered and re.search(r"(?m)^\s*image:\s*\S+:latest\b", rendered):
        offenders.append("rendered output")
    report.check("no manifest uses :latest", not offenders, ", ".join(offenders) or f"{len(manifests)} files")

    mongo = base / "mongo.yaml"
    mongo_docs = [doc_kind_and_name(d) + (d,) for d in yaml_documents(mongo)] if mongo.exists() else []
    stateful = [d for k, _, d in mongo_docs if k == "StatefulSet"]
    headless = [d for k, _, d in mongo_docs if k == "Service" and re.search(r"(?m)^\s*clusterIP:\s*None\b", d)]
    report.check("mongo.yaml is a StatefulSet with volumeClaimTemplates and a headless Service",
                 bool(stateful) and "volumeClaimTemplates:" in stateful[0] and bool(headless),
                 "ok" if stateful and headless else
                 f"StatefulSet {'found' if stateful else 'missing'}, volumeClaimTemplates {'found' if stateful and 'volumeClaimTemplates:' in stateful[0] else 'missing'}, headless Service {'found' if headless else 'missing'}")


def check_secret_handling(report: Report, repo: Path) -> None:
    label = "k8s/secret.env is not committed"
    # git exits 0 when the path is tracked, 1 when it is not, and 128 for "not a git repository"
    # or a bad path: only 1 is the answer we want, anything else is reported, not passed.
    code, output = run(["git", "ls-files", "--error-unmatch", "k8s/secret.env"], repo, timeout=60)
    if code == -1:
        report.warn(label, "git is not installed")
    elif code in (0, 1):
        report.check(label, code == 1, "tracked by git!" if code == 0 else "absent from git")
    else:
        report.warn(label, f"git exited {code}: {last_line(output)}")

    example = repo / "k8s" / "base" / "secret.example.env"
    values = read_env_values(example, SECRET_KEYS)
    missing = [k for k in SECRET_KEYS if k not in values]
    ok = example.exists() and not missing and values.get("LLM_API_KEY", "x") == ""
    detail = "six keys, LLM_API_KEY empty" if ok else (
        "file missing" if not example.exists() else
        f"missing {', '.join(missing)}" if missing else "LLM_API_KEY is not empty")
    report.check("secret.example.env has the six keys with an empty LLM_API_KEY", ok, detail)


def check_cluster(report: Report, repo: Path, ingress: str) -> None:
    labels = ("every Pod in namespace atlas is Ready and mongo-0 is present", "scripts/smoke.py --base passes through the Ingress")
    code, output = run(["kubectl", "-n", "atlas", "get", "pods", "-o", "json", "--request-timeout=15s"], repo, timeout=60)
    if code != 0:
        for label in labels:
            report.warn(label, "no cluster reachable" if code != -1 else "kubectl is not installed")
        return
    try:
        pods = json.loads(output)["items"]
    except (ValueError, KeyError):
        report.check(labels[0], False, "kubectl returned no pod list")
        return
    not_ready = []
    for pod in pods:
        phase = pod["status"].get("phase")
        if phase == "Succeeded":  # the seed Job's pod: finished is as good as ready
            continue
        conditions = {c["type"]: c["status"] for c in pod["status"].get("conditions", [])}
        if conditions.get("Ready") != "True":
            not_ready.append(f"{pod['metadata']['name']} ({phase})")
    has_mongo = any(pod["metadata"]["name"] == "mongo-0" for pod in pods)
    report.check(labels[0], bool(pods) and not not_ready and has_mongo,
                 f"{len(pods)} pods" + (f"; not ready: {', '.join(not_ready)}" if not_ready else "") + ("" if has_mongo else "; mongo-0 missing"))

    smoke = repo / "scripts" / "smoke.py"
    code, output = run([sys.executable, str(smoke), "--base", ingress, "--password", os.environ.get("DEMO_PASSWORD", "atlas-demo")],
                       repo, timeout=600)
    report.check(labels[1], code == 0, last_line(output))


def check_rollback_drill(report: Report, repo: Path) -> None:
    label = "docs/REPORT.md holds the rollback drill transcript"
    path = repo / "docs" / "REPORT.md"
    if not path.exists():
        report.check(label, False, "docs/REPORT.md is missing")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    undo = "rollout undo" in text
    stall = "ImagePullBackOff" in text or re.search(r"[Ss]tartup ?[Pp]robe", text) is not None
    report.check(label, undo and stall,
                 "rollout undo and the stall are both there" if undo and stall else
                 f"missing: {', '.join(m for m, ok in (('rollout undo', undo), ('ImagePullBackOff or the startup-probe stall', stall)) if not ok)}")


def task4(report: Report, repo: Path, ingress: str) -> None:
    check_manifests(report, repo)
    check_secret_handling(report, repo)
    check_cluster(report, repo, ingress)
    check_rollback_drill(report, repo)


# ================================================================ main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", type=int, choices=(2, 3, 4), required=True)
    parser.add_argument("--base", default="http://localhost:8000", help="the running API (Compose default)")
    parser.add_argument("--repo", default=".", help="the repository root (default: the current directory)")
    parser.add_argument("--email", default="clinician@riverside.example", help="a clinician in the tenant that owns the Test Facility")
    parser.add_argument("--other-email", default="clinician@harborview.example", help="a clinician in the other tenant")
    parser.add_argument("--admin-email", default="admin@riverside.example", help="an admin of the first tenant, for /api/usage")
    parser.add_argument("--alt-port", type=int, default=8001, help="host port for the second API process (Task 3)")
    parser.add_argument("--eval-threshold", type=float, default=0.7, help="the pass rate evals/run_evals.py must reach (Task 3); use the one in your REPORT.md")
    parser.add_argument("--ingress", default="http://localhost", help="the cluster's Ingress address (Task 4)")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    report = Report()
    print(f"Atlas submission check, Task {args.task}: {repo}, API {args.base}")
    if args.task in (2, 3):
        status, _ = call(f"{args.base.rstrip('/')}/health", timeout=10)
        if not report.check("API answers /health", status == 200, f"HTTP {status}"):
            return report.finish()
    atlas = Atlas(args.base, args)
    if args.task == 2:
        task2(report, repo, atlas)
    elif args.task == 3:
        task3(report, repo, atlas, args)
    else:
        task4(report, repo, args.ingress)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
