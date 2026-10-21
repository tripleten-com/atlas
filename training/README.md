# training/

Everything needed to train the two predictive models, the 30-day readmission model that
`backend/app/routers/risk.py` serves and the early-warning model that
`backend/app/routers/early_warning.py` serves, and the evidence of each run. The readmission
model is described first; the early-warning model has its own section at the end.

```
export_training_set.py   Postgres -> training/data/readmission.csv + readmission.columns.json (Test Facility only)
train.py                 CSV -> <out>/readmission.joblib + <out>/training_run.json + <out>/train.log
requirements-train.txt   the four pinned packages train.py needs
CODIO.md                 the step list for running train.py on EC2 inside the Codio AWS lab
data/readmission.csv     the committed snapshot: one row per discharged inpatient or observation stay
data/readmission.columns.json   which columns are categorical and the feature order, for train.py
runs/<YYYY-MM-DD>/       training_run.json and train.log from each run (plus two screenshots for a lab run);
                         the .joblib written there is gitignored, the copy in models/ is committed

export_vitals_hourly.py  Postgres -> data/vitals_hourly.csv + encounters_static.csv + deterioration.columns.json
deterioration.py         shared definitions for the early-warning model: onset rule, hourly grid, window
                         builder, standardisation, GRUNet (torch imported lazily, so the API can import the rest)
train_deterioration.py   CSVs -> <out>/deterioration.onnx + deterioration.json + training_run.json + train.log
check_onnx.py            runs the saved test windows through the .onnx with onnxruntime and compares with torch
                         (the run folder's .onnx when it is there, else models/deterioration.onnx; --onnx to choose)
requirements-gpu.txt     the training pins for the GPU session (torch, numpy, pandas, scikit-learn, onnx)
CODIO_GPU.md             the step list for the GPU session (g4dn.xlarge, Deep Learning AMI)
runs/<YYYY-MM-DD>-deterioration/   the early-warning run record; the .onnx there is gitignored, models/ holds the copy
```

## What the model predicts

For a discharged inpatient or observation encounter: will the same patient come back within 30
days of `discharge_date`? "Come back" is defined in `backend/app/ml.py` (`READMISSION_TYPES`) as
another inpatient, observation or emergency encounter. The emergency visits matter: on the
committed snapshot they are roughly a third of the positives, and the positive rate is 26% with
them against about 17% without. So the probability the card shows is "readmission or emergency
visit within 30 days", and the card says so. If you drop emergency visits from the label for
your own question, say so in your proposal and retrain.

Features are what the record holds at discharge: age, sex, admission type and source, length of
stay, prior stays in the previous year (`prior_admissions_365d`, inpatient and observation only),
the last 24 hours of vital signs (count, last, min, max of each of the six), how many lab results
were flagged, how many drug classes were ordered, and the discharge disposition. The feature SQL
lives in `backend/app/ml.py` and is shared with the API, so the training frame and the
request-time frame have the same columns. The export also writes the column lists to
`readmission.columns.json`, and `train.py` reads them from there, so the lab run (which has no
`ml.py`) encodes the same columns; `backend/tests/test_risk.py` checks the committed artifact
against `ml.py` as well.

Encounters discharged in the final 30 days of the data are excluded, because their label cannot
be known yet (right-censoring). Encounters that ended in death are excluded too. Rows discharged
in the first year of the two-year window have a truncated `prior_admissions_365d`, because
history from before the data starts is invisible (left-censoring); the reference keeps those rows
and documents it. On the committed snapshot the first-year rows have a lower mean history count
and a lower positive rate than the second-year rows, so this is a real, if modest, weakness.

## What the scores mean on the live facilities

`prior_admissions_365d` is the model's strongest feature (removing it costs more AUROC than
removing all the vital signs). The Test Facility has two years of history, so it is non-zero for
about half the rows there. The live facilities start with a fourteen-day backfill, so for them
the feature is almost always zero: measured on the reference database on 2026-10-11, RGH had
107 eligible stays with a mean of 0.028 and 97% zeros, HMC 71 stays with 100% zeros, RNC none
(outpatient only), against a Test Facility mean of 1.37 and 47% zeros. The model therefore reads
every live patient as a first-time admission. Scores on live facilities demonstrate the plumbing
(the feature reacts to new discharges); they do not demonstrate the model's accuracy, and your
report should say so.

## Run it locally

From the repository root, with the Compose stack running (Postgres on `localhost:5433`):

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt      # Windows; use .venv/bin/pip elsewhere
.venv/Scripts/python training/export_training_set.py
.venv/Scripts/python training/train.py
cp training/runs/$(date +%F)/readmission.joblib models/readmission.joblib
docker compose restart backend
```

`export_training_set.py` prints the row count, the positives and the positive rate.
`train.py` prints the grouped split, the number of boosting rounds, the training-set and
test-set metrics (AUROC, average precision, Brier) and a ten-bin calibration table, then writes
into `--out` (default `training/runs/<today>`):

- `readmission.joblib`, the artifact; copy it to `models/readmission.joblib`, which is what the
  API loads from `MODEL_PATH`;
- `training_run.json`, the run record (model version, data hash, metrics, calibration, artifact
  hash, where it ran);
- `train.log`, a copy of the printed output.

`training_run.json` says `"trained_on": "local"` for a run on your laptop, and the script prints
a note saying so. After a new artifact, restart the backend (`docker compose restart backend`)
so it loads the new file.

Flags: `--data` (default `training/data/readmission.csv`), `--out` (default
`training/runs/<today>`).

## Why the hyperparameters look the way they do

`HistGradientBoostingClassifier` defaults to `early_stopping='auto'`, which only switches on
above 10,000 rows. With under 2,000 rows the first reference run let 300 boosting rounds run
unchecked and memorised the training set: train AUROC 1.00, test AUROC 0.73, and the "low" band
held more readmissions than its own threshold allows. `build_pipeline()` in `train.py` now turns
early stopping on explicitly (a fifth of the training rows held out, stop after 20 rounds without
improvement), caps tree depth at 3 and requires 40 rows per leaf. On the same split this gave
train AUROC 0.83 and test AUROC 0.76, with the low band observed below 0.15. The training-set
metrics are printed next to the test-set ones so the gap is visible in every `train.log`.

## What the lab run adds

The course requires the real artifact to be trained on AWS, inside the Codio-hosted lab, on a
t2.micro EC2 instance. The script is the same; what changes is the evidence:

- `training_run.json` says `"trained_on": "ec2"` and carries the instance id in `instance_hint`
  (the script asks the EC2 instance metadata service, with an IMDSv2 token first, which only
  answers from inside an instance);
- `training/runs/<date>/` also holds two screenshots: the EC2 terminal after training, and the
  S3 object in the console;
- the `artifact_sha256` in the JSON matches `sha256sum models/readmission.joblib`
  (`Get-FileHash models\readmission.joblib -Algorithm SHA256` in PowerShell) for the file you
  committed.

The step list is in `CODIO.md`. The reference run in `runs/2026-10-11/` was made locally, on
Windows, and is labelled as such.

## The early-warning model

The question is fixed for everyone: for an inpatient who is admitted right now, how likely is
it that their vitals cross the deterioration line within the next 12 hours? The onset rule
(`training/deterioration.py`): the first hour at or after hour 12 of the stay whose vitals meet
at least two of heart rate above 120, respiratory rate above 24, SpO2 below 90, systolic blood
pressure below 90, temperature above 38.5 °C. Nothing in the schema labels this; the export
derives it from the vitals.

One training row is an (encounter, as-of hour) window: the last 24 hours of the six vitals on
an hourly grid (forward-filled inside the window, standardised with training-set statistics),
a 24 x 6 mask channel (1 in an hour with a measurement) and four static features (age, sex,
admission type, hours since admission). Windows start at hour 12 and repeat every `--stride`
hours until discharge or the onset hour; later windows are dropped. The label is 1 when the
onset falls within the next 12 hours. The split is grouped by patient, the standardiser is fit
on training rows only, and only measurements before the as-of hour enter a window.

```bash
.venv/Scripts/python training/export_vitals_hourly.py
<python with torch> training/train_deterioration.py --stride 4 --epochs 3                 # rehearsal
<python with torch> training/train_deterioration.py --device cuda --time-one-epoch        # reference
cp training/runs/$(date +%F)-deterioration/deterioration.{onnx,json} models/
.venv/Scripts/python training/check_onnx.py training/runs/$(date +%F)-deterioration
docker compose restart backend
```

Flags: `--data-dir`, `--out`, `--device auto|cpu|cuda`, `--stride` (1 is the reference, 4 the
quick setting), `--epochs` (upper bound, 40), `--no-mask` (the cadence ablation: zeroes the mask
channel, same tensor shape), `--time-one-epoch` (one epoch on cpu and one on cuda, same data and
batch size, recorded as `epoch_seconds`), `--seed` (42), `--max-minutes` (14, a safety stop for
the one-hour lab).

Two things the reference run shows and REPORT.md should say. First, the mask channel also
encodes nursing cadence, which rises after the simulated onset begins and before the threshold
is crossed, so part of the signal is "someone is worried"; `--no-mask` measures it. Second,
`pos_weight` (about 65 negatives per positive) inflates every probability: the ranking is good
(average precision is the headline number, because prevalence is about 1.6%), but the
calibration table shows observed rates far below the predicted ones, and at the reference
alert threshold of 0.20 the alert rate is high. The alert-rate and lead-time table in
`training_run.json` (`alert_table`) gives the same numbers at several thresholds so a student
can pick a threshold and defend it. The numbers from the laptop reference run are in
`REFERENCE_NOTES.md`; they come from an RTX 4090, not from the lab's T4.
