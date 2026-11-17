# Training on EC2 inside the Codio AWS lab

The lab session lasts one hour. The Sandbox panel beside the lab counts down the minutes left,
and its "Request 10 more minutes" button adds ten minutes at a time (the lab's first page says
up to two hours in total). When the session ends, everything in the account is wiped: the bucket, the role, the network, the instance, every file. Rehearse once with a tiny
CSV (the first 50 rows of `readmission.csv` is enough), then do the real run. Have these four
files on your laptop before you start the clock:

```
training/data/readmission.csv
training/data/readmission.columns.json
training/train.py
training/requirements-train.txt
```

These are the worked example's files (question A). For your own question, the lab pages and the
Task 2 lesson use `<question>.csv`, `<question>.columns.json` and `train_<question>.py` in the
same places.

On Windows, run the commands marked "your laptop" in Git Bash (installed with git), not in
PowerShell or cmd. The commands inside the EC2 terminal are Linux and work as written.

## Before the session

1. Sign in at https://codio.com/auth/tripleten and open the AAI/ML Project 5 course. The first
   time, join it with the link or the course token from the lab lesson (they are on the lesson
   page, not in this repository).
2. Open the "Atlas Machine Learning on AWS" lab and start the lab. It has no dataset or script
   of its own; you bring the four files. It gives you a temporary AWS console login. The hour
   starts now. If Codio says "Prerequisite failed: Working with EC2 Instances should be
   STARTED", open that lab in the same course once, then come back.
3. Open the AWS console only through the lab's link. If your browser is still signed in to AWS
   from an earlier lab, the console opens that lab's account instead, and the lab's checks find
   nothing you make there. Sign out of AWS first, or open the link in a private window.

The lab's pages walk you through the console in order, and its checks look for the exact names
below. Every session starts from an empty account, so the bucket, the role, the network and the
instance are made again each time.

## In the session

1. Lab page "Create Your Project Bucket in S3": create a bucket named lowercase letters, then
   `-ml-project-`, then four digits (for example `jsmith-ml-project-1116`), with two folders,
   `datasets/` and `models/`. The lab's check accepts only that pattern. If the name is taken,
   change the digits.
2. Lab page "Upload Your Files to S3": upload the four files into `datasets/` through the
   console (open the folder, Upload, Add files).
3. Lab page "Create an IAM Role for EC2": create the role the instance uses to read and write
   your bucket. AWS service, use case EC2, attach `AmazonS3FullAccess`, name it exactly
   `EC2-S3-Access-Role`. The lab does not come with a role; without this step `aws s3 cp`
   answers Access Denied.
4. Lab page "Create a VPC": build the network exactly as the page describes. Its check looks for
   `my-project-vpc` with one public subnet named `my-project-subnet-public1...`, the internet
   gateway `my-project-igw` and the route table `my-project-rtb-public`.
5. Lab page "Launch an EC2 Instance with the IAM Role": name the instance
   `ml-training-instance`, Amazon Linux 2023, `t2.micro`. If the wizard asks for a key pair,
   choose "Proceed without a key pair": EC2 Instance Connect does not need one. In Network
   settings press Edit, pick `my-project-vpc` and its public subnet, and set Auto-assign public
   IP to Enable. Under Advanced details set IAM instance profile to `EC2-S3-Access-Role`.
6. Select the instance, press Connect, choose EC2 Instance Connect, and open the browser
   terminal.
7. Lab page "Connect and Set Up the Environment": check the Python version and install pip,
   which Amazon Linux 2023 does not ship with:

   ```bash
   python3 --version
   sudo dnf install -y python3 python3-pip
   ```

   If the version is older than 3.9, install a newer one and use it for every command below:

   ```bash
   sudo dnf install -y python3.11 python3.11-pip
   ```

   (then write `python3.11` wherever the steps say `python3`).
8. Lab page "Download Your Files from S3 to EC2": make the folders the lab checks and fetch the
   files into them:

   ```bash
   mkdir -p ~/ml-project/data ~/ml-project/models
   cd ~/ml-project
   aws s3 cp s3://<your-bucket>/datasets/readmission.csv data/
   aws s3 cp s3://<your-bucket>/datasets/readmission.columns.json data/
   aws s3 cp s3://<your-bucket>/datasets/train.py .
   aws s3 cp s3://<your-bucket>/datasets/requirements-train.txt .
   ```
9. Install the pins (they include pandas and scikit-learn, which the lab's check imports):

   ```bash
   python3 -m pip install -r requirements-train.txt
   ```

   This takes a few minutes on a t2.micro. pip may warn that awscli wants an older
   `python-dateutil`; the `aws` command keeps working.
10. Lab page "Run Your Training Script": train.

    ```bash
    python3 train.py --data data/readmission.csv --out models
    ```

    It finishes in well under two minutes and prints the metrics and the calibration table.
    The first line must say `on ec2 (i-...)`: the script asks the instance metadata service for
    the instance id (with an IMDSv2 token, which Amazon Linux 2023 requires). If it says
    `on local` instead, the metadata service did not answer; do not continue with that run, ask
    in the course channel with a screenshot.
11. Take the first screenshot: the terminal with the training output visible.
12. Lab page "Upload Your Results to S3": copy the results up.

    ```bash
    aws s3 cp models/ s3://<your-bucket>/models/ --recursive
    ```
13. Lab page "Verify Results in the S3 Console": take the second screenshot, the three objects in
    the bucket's `models/` folder.
14. In the S3 console select each of `readmission.joblib`, `training_run.json` and `train.log`
    in `models/` and press Download. Do this before the hour ends; after that the bucket is gone.
15. Lab page "Lab Summary and Final Submission": run the final check while the instance is still
    running. Do not mark the lab as completed yet: that makes the lab read-only, and the GPU
    session (`CODIO_GPU.md`) runs in the same lab.

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

- `aws s3 cp` says access denied: the instance was launched without `EC2-S3-Access-Role`.
  Create the role if this session has none yet, terminate the instance and launch again with
  the role set under IAM instance profile.
- The bucket check finds no bucket although you made one: the console was still signed in to an
  earlier lab's AWS account. Sign out of AWS, open the console again through the lab's link and
  make the bucket there.
- A lab check fails on a name: compare it letter by letter with the names above (bucket
  pattern, `EC2-S3-Access-Role`, `my-project-vpc`, `ml-training-instance`, `~/ml-project/`).
- pip is slow or the instance runs out of memory: a t2.micro has 1 GB. Close other terminals and
  retry; the four pins fit.
- Time is running short: press "Request 10 more minutes" in the Sandbox panel before the
  countdown ends. If the session ends anyway, nothing persists. Start a new session and repeat;
  the rehearsal is why the real run is quick.
- Larger instances, GPUs, SageMaker or a persistent bucket are not part of this run. If the lab provides something extra, treat it as a convenience, not a requirement.
