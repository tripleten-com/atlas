# Training on EC2 inside the Codio AWS lab

The lab session lasts one hour with no on-screen timer. When it ends, everything in the account
is wiped: the bucket, the instance, every file. Rehearse once with a tiny CSV (the first 50 rows
of `readmission.csv` is enough), then do the real run. Have these four files on your laptop
before you start the clock:

```
training/data/readmission.csv
training/data/readmission.columns.json
training/train.py
training/requirements-train.txt
```

On Windows, run the commands marked "your laptop" in Git Bash (installed with git), not in
PowerShell or cmd. The commands inside the EC2 terminal are Linux and work as written.

## Before the session

1. Sign in at https://codio.com/auth/tripleten and open the AI/ML Codio course. The first time,
   enrol with the course token from the lab lesson (it is on the lesson page, not in this
   repository).
2. Open the EC2 training lab (the "End-to-End Machine Learning on AWS" flow) and start the lab.
   It gives you a temporary AWS console login. The hour starts now.

## In the session

1. In the S3 console create a bucket. The name must be globally unique; something like
   `atlas-training-<your initials>-<today>`.
2. Upload the four files into the bucket through the console (Upload, Add files).
3. In the EC2 console launch an instance: Amazon Linux 2023, `t2.micro`, and under Advanced
   details pick the lab's IAM instance profile (the role the lab provides; it is what lets the
   instance read and write your bucket). If the wizard asks for a key pair, choose "Proceed
   without a key pair": EC2 Instance Connect does not need one. Leave the network settings as
   the lab presents them.
4. Select the instance, press Connect, choose EC2 Instance Connect, and open the browser
   terminal.
5. Check the Python version:

   ```bash
   python3 --version
   ```

   If it is older than 3.9, install a newer one and use it for every command below:

   ```bash
   sudo dnf install -y python3.11 python3.11-pip
   ```

   (then write `python3.11` wherever the steps say `python3`).
6. Fetch the files:

   ```bash
   aws s3 cp s3://<your-bucket>/readmission.csv .
   aws s3 cp s3://<your-bucket>/readmission.columns.json .
   aws s3 cp s3://<your-bucket>/train.py .
   aws s3 cp s3://<your-bucket>/requirements-train.txt .
   ```
7. Install the pins:

   ```bash
   python3 -m pip install -r requirements-train.txt
   ```

   This takes a few minutes on a t2.micro. If the command says pip is missing, install it
   with `sudo dnf install -y python3-pip` and run it again.
8. Train:

   ```bash
   python3 train.py --data readmission.csv --out run
   ```

   It finishes in well under two minutes and prints the metrics and the calibration table.
   The first line must say `on ec2 (i-...)`: the script asks the instance metadata service for
   the instance id (with an IMDSv2 token, which Amazon Linux 2023 requires). If it says
   `on local` instead, the metadata service did not answer; do not continue with that run, ask
   in the course channel with a screenshot.
9. Take the first screenshot: the terminal with the training output visible.
10. Copy the results up:

    ```bash
    aws s3 cp run/readmission.joblib s3://<your-bucket>/
    aws s3 cp run/training_run.json s3://<your-bucket>/
    aws s3 cp run/train.log s3://<your-bucket>/
    ```
11. Take the second screenshot: the S3 console showing the three objects in your bucket.
12. In the S3 console select each of `readmission.joblib`, `training_run.json` and `train.log`
    and press Download. Do this before the hour ends; after that the bucket is gone.

## After the session

Place the files in your fork:

```
models/readmission.joblib
training/runs/<YYYY-MM-DD>/training_run.json
training/runs/<YYYY-MM-DD>/train.log
training/runs/<YYYY-MM-DD>/ec2-terminal.png
training/runs/<YYYY-MM-DD>/s3-objects.png
```

Check the hash matches `artifact_sha256` in `training_run.json`, then restart the backend so it
loads the new artifact (your laptop):

```bash
sha256sum models/readmission.joblib                        # Git Bash, macOS, Linux
Get-FileHash models\readmission.joblib -Algorithm SHA256   # PowerShell
docker compose restart backend
```

## If something goes wrong

- `aws s3 cp` says access denied: the instance was launched without the lab's IAM role. Terminate
  it and launch again with the role selected.
- pip is slow or the instance runs out of memory: a t2.micro has 1 GB. Close other terminals and
  retry; the four pins fit.
- You ran out of time: nothing persists. Start a new session and repeat; the rehearsal is why the
  real run is quick.
- Session length, larger instances, GPUs, SageMaker or a persistent bucket are not part of this
  lab. If the lab provides something extra, treat it as a convenience, not a requirement.
