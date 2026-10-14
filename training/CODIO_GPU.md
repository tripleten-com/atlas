# Training the early-warning model on a GPU inside the Codio AWS lab

This is the second lab session of the project, booked for `train_deterioration.py`. It is the
same lab as the CPU run in `CODIO.md` (same login, same one-hour session, same wipe when the
hour ends), with two differences: the instance is a `g4dn.xlarge` (4 vCPUs, 16 GB of memory,
one NVIDIA T4 with 16 GB) and the AMI is a Deep Learning image with PyTorch preinstalled. Read
`CODIO.md` first; only the steps that differ are spelled out here.

Rehearse on your laptop (or in Colab) before the session:

```bash
python training/train_deterioration.py --stride 4 --epochs 3          # a few seconds, any device
```

The real run at stride 1 takes under a minute on a laptop GPU and is budgeted at 15 minutes on
the T4. Have these five files on your laptop before you start the clock:

```
training/data/vitals_hourly.csv
training/data/encounters_static.csv
training/data/deterioration.columns.json
training/deterioration.py
training/train_deterioration.py
```

(`requirements-gpu.txt` is handy too, for the pins, but the AMI already has torch.)

## Before the session

1. Sign in at https://codio.com/auth/tripleten and open the AI/ML Codio course. If this is your
   first lab, enrol with the course token from the lab lesson page (it is behind the TripleTen
   login, never in this repository).
2. Start the EC2 training lab. The hour starts now.

## In the session

1. In the S3 console create a bucket (`atlas-training-<your initials>-<today>`) and upload the
   five files through the console.
2. In the EC2 console launch an instance. In the AMI search pick **Deep Learning OSS Nvidia
   Driver AMI GPU PyTorch** (Amazon Linux 2023 or Ubuntu, whichever the lab lists); instance
   type **g4dn.xlarge**; the lab's IAM instance profile under Advanced details; proceed without
   a key pair. Everything else as in `CODIO.md`.
3. Connect with EC2 Instance Connect. The first command is:

   ```bash
   nvidia-smi
   ```

   A healthy answer is a table naming `Tesla T4` with about 15 GB of memory and a driver and
   CUDA version at the top. If the command is missing or says no devices were found, stop,
   terminate the instance and launch again with the right AMI; a CPU run here wastes the session.
4. Activate the preinstalled PyTorch environment. The login banner names it; on the PyTorch
   AMI it is:

   ```bash
   source activate pytorch
   python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"
   ```

   It must print `True`. Then install the two or three packages the environment lacks:

   ```bash
   python3 -m pip install pandas scikit-learn onnx
   ```

   **Fallback** if the lab only offers the "Deep Learning Base" AMI (drivers and CUDA, no
   PyTorch): install torch from the CUDA 12.4 index, which takes two to four minutes:

   ```bash
   python3 -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
   python3 -m pip install numpy pandas scikit-learn onnx
   ```

5. Fetch the files:

   ```bash
   aws s3 cp s3://<your-bucket>/vitals_hourly.csv .
   aws s3 cp s3://<your-bucket>/encounters_static.csv .
   aws s3 cp s3://<your-bucket>/deterioration.columns.json .
   aws s3 cp s3://<your-bucket>/deterioration.py .
   aws s3 cp s3://<your-bucket>/train_deterioration.py .
   ```

6. Train, with the CPU-versus-GPU timing switched on so the comparison comes from this
   instance:

   ```bash
   python3 train_deterioration.py --data-dir . --out run --device cuda --time-one-epoch
   ```

   The first line must say `on ec2 (i-...)`. The log then shows the `nvidia-smi` table, the
   window counts, `one epoch on cpu: ...s` and `one epoch on cuda: ...s`, the epochs with their
   validation average precision, the metrics, the calibration table and the alert table. The
   script stops itself after 14 minutes (`--max-minutes`) so the session is never lost to a
   slow run; if that happens, the artifact it writes is still the best epoch so far.
7. Take the first screenshot: the terminal with the training output visible.
8. Copy the results up:

   ```bash
   aws s3 cp run/deterioration.onnx s3://<your-bucket>/
   aws s3 cp run/deterioration.json s3://<your-bucket>/
   aws s3 cp run/training_run.json s3://<your-bucket>/
   aws s3 cp run/train.log s3://<your-bucket>/
   aws s3 cp run/onnx_check.npz s3://<your-bucket>/
   ```

9. Take the second screenshot: the S3 console showing the objects in your bucket.
10. Select each object and press Download. Do this before the hour ends; after that the bucket
    is gone.

## After the session

Place the files in your fork:

```
models/deterioration.onnx
models/deterioration.json
training/runs/<YYYY-MM-DD>-deterioration/training_run.json
training/runs/<YYYY-MM-DD>-deterioration/train.log
training/runs/<YYYY-MM-DD>-deterioration/onnx_check.npz
training/runs/<YYYY-MM-DD>-deterioration/ec2-terminal.png
training/runs/<YYYY-MM-DD>-deterioration/s3-objects.png
```

Check the hash matches `artifact_sha256` in `training_run.json`, check the graph against the
torch probabilities, then restart the backend (your laptop, Git Bash):

```bash
sha256sum models/deterioration.onnx
.venv/Scripts/python training/check_onnx.py training/runs/<YYYY-MM-DD>-deterioration
docker compose restart backend
```

`training_run.json` must say `"trained_on": "ec2"`, carry a `gpu_name`, and have
`epoch_seconds.cuda` below `epoch_seconds.cpu`; that is what the automated check reads.

## If something goes wrong

- `torch.cuda.is_available()` prints `False` on the PyTorch AMI: you are not in the conda
  environment (`source activate pytorch`), or the instance is not a `g4dn`. Check `nvidia-smi`.
- `pip install torch` from the default index gives a CPU-only build. Use the `--index-url`
  line above.
- `--device cuda` exits with "torch.cuda.is_available() is False": same cause as the first bullet.
- The session ran out: nothing persists. The cutline in the task page applies: a laptop or
  Colab run of the same script, labelled `trained_on: local`, is accepted and loses the
  "trained on AWS" line for this model only.
- Instance types other than `t2.micro` and `g4dn.xlarge`, longer sessions, SageMaker and a
  persistent bucket are not part of this lab.
