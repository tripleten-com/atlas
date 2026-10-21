# Atlas on Kubernetes

This folder runs the whole Atlas stack on a Kubernetes cluster on your laptop. Compose stays
the way you develop; this is how you deploy. The cluster is `kind` (Kubernetes in Docker):
one Docker container acts as the cluster node, and everything Atlas needs runs inside it.

```
k8s/
├── kind-config.yaml        the cluster: one node, ports 80 and 443 mapped to localhost
├── base/                   the manifests, applied together with kustomize
│   ├── namespace.yaml      Namespace atlas
│   ├── configmap.yaml      ConfigMap atlas-config (non-secret settings)
│   ├── secret.example.env  the keys the Secret needs; copy to k8s/secret.env (gitignored)
│   ├── postgres.yaml       PVC pgdata, Deployment db, Service db
│   ├── mongo.yaml          StatefulSet mongo (Pod mongo-0, its own PVC), headless Service mongo
│   ├── meridian.yaml       PVC meridian-data, Deployment meridian, Service meridian
│   ├── seed-job.yaml       Job atlas-seed: migrations and the Test Facility, once
│   ├── api.yaml            Deployment api (2 replicas, three probes), Service api
│   ├── worker.yaml         Deployment worker (1 replica, heartbeat readiness and liveness)
│   ├── web.yaml            Deployment web (2 replicas, nginx), Service web
│   ├── ingress.yaml        Ingress atlas: / to web; /api /health /docs /openapi.json to api
│   └── kustomization.yaml  the list above plus the image tag
├── optional/hpa.yaml       autoscaling for the API (lesson material, not required)
└── exercises/broken-api.yaml   a Deployment with planted faults for Task 4
```

## Prerequisites

- Docker Desktop with at least 8 GB of memory for Docker (Settings, Resources). The cluster
  node, the ingress controller, the Atlas images, Postgres and MongoDB all live inside Docker;
  6 GB was enough before the document store was added.
- `kubectl`. Docker Desktop installs one. Check with `kubectl version --client`.
- `kind`. Windows: `winget install -e --id Kubernetes.kind`. macOS: `brew install kind`.
  Linux: download the release binary from https://kind.sigs.k8s.io. Check with `kind version`.
  On Windows, winget adds `kind` to your PATH, but only programs started afterwards see it:
  close and reopen the terminal program itself (Windows Terminal, VS Code, Cursor), not just a
  new tab. If `kind version` still fails, sign out and back in, or run it by its full path,
  which `winget list --id Kubernetes.kind` helps you find under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\`.
- Python 3.12 on your machine, for the smoke script and the login one-liner in the ladder of
  proof. If `python` is not found on macOS or Linux, write `python3`.
- Port 80 free on your machine. If something else uses it (IIS, Skype, another nginx), change
  `hostPort: 80` to `hostPort: 8080` in `kind-config.yaml` and use `http://localhost:8080`
  everywhere this page says `http://localhost`. Nothing else needs editing: the web app calls
  the API on the address it was loaded from.
- `git`, so the image tag can be the commit hash.

Everything below is run from the repository root. On Windows, run it in Git Bash (installed
with git), not in PowerShell or cmd: `curl`, `TAG=...`, `$(...)`, `&&`, `tail` and `sha256sum`
below are Unix shell, and PowerShell either lacks them or reads them differently.

## 1. Build the images

Images are tagged with the short git hash, never `latest`. A fixed tag is what makes a
rollback possible, and kind only knows the images you load into it by name and tag.

```bash
TAG=sha-$(git rev-parse --short HEAD)
docker build -f backend/Dockerfile      -t atlas-api:$TAG      .
docker build -f frontend/Dockerfile.prod -t atlas-web:$TAG     ./frontend
docker build -f feed/Dockerfile         -t atlas-meridian:$TAG .
docker images | grep atlas-
```

The first line Docker prints, "transferring context", is it copying the repository to the
Docker daemon; the root `.dockerignore` keeps `.git`, `.venv`, `node_modules`, `.env` and
`k8s/secret.env` out of that copy, which is both faster and how a key never reaches an image.

`atlas-api` serves the API, runs the worker, and runs the seed Job, each with a different
command. It also contains `models/`, so the trained model travels inside the image.
`atlas-web` is the React app built once and served by nginx. The API address is written into
`config.js` when the container starts, from the `VITE_API_URL` environment variable, so the
same image works anywhere.

## 2. Create the cluster and the ingress controller

```bash
kind create cluster --name atlas --config k8s/kind-config.yaml
kubectl config current-context        # kind-atlas
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=180s
```

The first command takes about half a minute and switches `kubectl` to the new cluster. The
ingress controller is what listens on port 80 and routes requests to the right Service. The
manifest URL is pinned to a release (`controller-v1.15.1`) rather than the `main` branch, so
every laptop in the course installs the same controller.

`rollout status` waits even if the controller pod has not been created yet. The often-quoted
alternative, `kubectl wait --for=condition=ready pod --selector=...`, exits at once with
`error: no matching resources found` when you run it before the pod exists, and then step 5
fails with `failed calling webhook "validate.nginx.ingress.kubernetes.io" ... connection
refused` because the controller's admission webhook is not up yet. If you see that error,
nothing is broken: wait a few seconds and run `kubectl apply -k k8s/base` again. Everything
except the Ingress was created the first time, and the second apply adds it.

## 3. Load the images into the cluster

The node has its own image store. Loading copies your images into it.

```bash
kind load docker-image atlas-api:$TAG atlas-web:$TAG atlas-meridian:$TAG --name atlas
```

Repeat this after every rebuild. A pod that says `ErrImagePull` or `ImagePullBackOff` for an
`atlas-*` image almost always means the image was built but not loaded, or the tag in
`kustomization.yaml` does not match the one you loaded.

## 4. Create the Secret

Passwords and keys never go in a manifest or an image. They go in a Secret, created from a
local file that git ignores.

```bash
cp k8s/base/secret.example.env k8s/secret.env
openssl rand -hex 32        # paste the output as JWT_SECRET in k8s/secret.env
kubectl apply -f k8s/base/namespace.yaml
kubectl -n atlas create secret generic atlas-secrets --from-env-file=k8s/secret.env
kubectl -n atlas get secret atlas-secrets      # TYPE Opaque, DATA 6
```

`JWT_SECRET` signs every session token. The placeholder in the example file is in a public
repository, so a cluster that keeps it lets anyone forge a login; replace it before the Secret
is created (the API logs a warning at startup while the placeholder is in use). The other four
placeholders (`POSTGRES_PASSWORD`, `MERIDIAN_API_KEY`, `MONGO_USER`, `MONGO_PASSWORD`) only
travel inside the cluster. Put your model key in `LLM_API_KEY`, or leave it empty to run without
the summary feature.

To change a value later, delete and recreate the Secret, then restart what reads it:
`kubectl -n atlas delete secret atlas-secrets`, create it again, then
`kubectl -n atlas rollout restart deploy/api deploy/worker`.

## 5. Apply the manifests

Set the tag in `k8s/base/kustomization.yaml` to the one you built (the three `newTag`
lines), then apply the whole folder. The committed `newTag` is whatever the reference build
used; it is an example, and it will never match the hash of your fork, so edit it every time
you build. The same goes for the `image:` line in `k8s/exercises/broken-api.yaml`.

```bash
kubectl apply -k k8s/base
kubectl -n atlas get pods -w        # Ctrl+C when every pod is Running and 1/1, except atlas-seed, which ends as Completed 0/1
```

What you will see, in order: `db`, `mongo-0` and `meridian` come up first. `atlas-seed` runs
for 80 to 100 seconds while it writes the Test Facility history. The `api` pods wait for the database in
an init container, then start but stay `0/1` until the seed Job has created the schema and
`/health` answers. `worker` and `web` come up alongside. On the reference laptop the whole
thing went from `apply` to everything Ready in 84 seconds on the first build and 103 seconds
on a rebuild with the Compose stack running at the same time.

## 6. The ladder of proof

Check these in order. Each step depends on the one before it.

1. Every pod `Running` and `1/1`, except the seed pod, which shows `Completed 0/1` because
   it has finished its work and will not run again. The document store is the one pod with a
   fixed name, `mongo-0`: it belongs to a StatefulSet, so its name and its disk
   (`kubectl -n atlas get pvc` shows `mongo-data-mongo-0`) stay the same across restarts.

   ```bash
   kubectl -n atlas get pods
   kubectl -n atlas get pvc            # pgdata, meridian-data, mongo-data-mongo-0, all Bound
   ```

2. The seed Job `Complete`. Do this within ten minutes of the seed finishing: the Job removes
   itself and its pod after that (`ttlSecondsAfterFinished: 600`), and `get jobs` then says
   `No resources found`. If you missed it, the proof is the seed pod's `Completed` line above
   (while it is still listed) and the row counts the API returns in step 4.

   ```bash
   kubectl -n atlas get jobs
   kubectl -n atlas logs job/atlas-seed | tail -3
   ```

   To seed again: `kubectl -n atlas delete job atlas-seed --ignore-not-found` and apply the
   base again.

3. The API answers through the Ingress:

   ```bash
   curl -s localhost/health          # {"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}
   curl -s localhost/health/live     # {"status":"alive"}
   ```

   `ai_store` is the document store. `unavailable` means the api cannot reach `mongo-0`; the
   clinical record is still served and `status` stays `ok`, because readiness must not fail for
   a dependency the API can live without. `kubectl -n atlas get pod mongo-0` and
   `kubectl -n atlas logs mongo-0 --tail=20` say why.

4. Login works and protected routes need the token:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' localhost/api/facilities        # 401
   TOKEN=$(curl -s -X POST localhost/api/auth/login -H 'Content-Type: application/json' \
     -d '{"email":"clinician@riverside.example","password":"atlas-demo"}' \
     | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
   curl -s localhost/api/facilities -H "Authorization: Bearer $TOKEN"
   ```

5. The web app at http://localhost. Log in with `clinician@riverside.example` and
   `atlas-demo`. `curl -s localhost/config.js` shows `apiUrl: ""`, which means the page calls
   the API on its own address (same origin); the Ingress sends `/api` on to the api Service.

6. The Integrations page (log in as `admin@riverside.example`) shows the live facilities
   with a rising watermark and recent deliveries.

7. The worker is moving:

   ```bash
   kubectl -n atlas logs deploy/worker --tail=20
   ```

   Lines like `applied batch of 500: {'processed': 491, 'deferred': 9}` during the backfill,
   then smaller batches as events arrive in real time.

8. The document store holds what the cards wrote. Ask inside the pod, with the credentials it
   already has as environment variables:

   ```bash
   kubectl -n atlas exec mongo-0 -- sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin atlas_ai --eval "db.predictions.countDocuments()"'
   ```

   A count above zero once a patient has been opened; `db.predictions.getIndexes()` asked the
   same way lists the TTL index `created_at_ttl` beside `tenant_patient_created`.
   Keep to this one-shot `--eval` form: an interactive `mongosh` inside the Pod shares its 1Gi
   memory limit with the database. For an interactive shell, use
   `kubectl -n atlas port-forward pod/mongo-0 27019:27017` and the `mongosh` on your machine
   (`docs/AI_STORE.md` has the connection string).

The smoke script works against the cluster too. With `--base` the API and the web app share
the Ingress address; Meridian has no Ingress route, so its own health check is skipped unless
you forward its port and pass `--feed`:

```bash
python scripts/smoke.py --base http://localhost
# or, to include Meridian's own health check:
kubectl -n atlas port-forward svc/meridian 18200:8100 &
python scripts/smoke.py --base http://localhost --feed http://127.0.0.1:18200
```

The script scores one Test Facility patient with the readmission model and asks for a stay
summary; the summary is reported as `skip` while the Secret's `LLM_API_KEY` is empty.

## 7. The rollback drill

Deploy a version that cannot work and watch Kubernetes refuse to take the working one down.

```bash
kubectl -n atlas set image deploy/api api=atlas-api:sha-bad
kubectl -n atlas get pods -l app=api
kubectl -n atlas rollout status deploy/api --timeout=30s
```

A third `api` pod appears in `ErrImagePull`, then `ImagePullBackOff`: no image has that tag.
The two old pods keep running and `curl -s localhost/health` keeps answering, because a
rolling update only removes an old pod once a new one is Ready, and the new one never is.
`rollout status` prints "Waiting for deployment "api" rollout to finish: 1 out of 2 new
replicas have been updated..." and then, after the 30 seconds, `error: timed out waiting for
the condition`. That error is the point: the rollout is stuck. Without `--timeout` the command
waits for the Deployment's own deadline, ten minutes, and looks hung; press Ctrl+C if you
forgot the flag. `kubectl -n atlas describe pod <the new pod>` shows the pull errors at the
bottom.

Now roll back:

```bash
kubectl -n atlas rollout history deploy/api
kubectl -n atlas rollout undo deploy/api
kubectl -n atlas rollout status deploy/api
kubectl -n atlas get deploy/api -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

The bad pod is terminated and the image is back on the previous tag. The same drill with a
tag that exists but whose code fails `/health` looks slightly different: the new pod starts,
the startup probe fails for up to 150 seconds, and the pod is restarted, again without the
old pods being removed.

## 8. The broken Deployment exercise

`k8s/exercises/broken-api.yaml` is an `api` Deployment that does not work. Set its `image:`
tag to the one you loaded, apply it over the running cluster, and work out what is wrong
using `get pods`, `describe pod`, `logs` and the contents of the ConfigMap and Secret:

```bash
kubectl -n atlas apply -f k8s/exercises/broken-api.yaml
kubectl -n atlas get pods -l app=api -w
```

Fixing the first problem reveals the next one. When you are done, restore the working
Deployment (the `delete job` line is explained in the next section):

```bash
kubectl -n atlas delete job atlas-seed --ignore-not-found
kubectl apply -k k8s/base
```

Your write-up of the faults goes in `docs/REPORT.md`.

## Updating after a code change

```bash
TAG=sha-$(git rev-parse --short HEAD)       # commit first, so the tag changes
docker build -f backend/Dockerfile -t atlas-api:$TAG .
kind load docker-image atlas-api:$TAG --name atlas
# edit newTag in k8s/base/kustomization.yaml, then
kubectl -n atlas delete job atlas-seed --ignore-not-found
kubectl apply -k k8s/base
kubectl -n atlas rollout status deploy/api --timeout=180s
```

The `delete job` line matters when the seed Job from the last apply is still there (it removes
itself ten minutes after finishing). A Job's pod template is immutable, so applying the base
with a new image tag while the old Job exists fails with
`Job.batch "atlas-seed" is invalid: spec.template: Invalid value: ... field is immutable`;
everything else in the apply still goes through, which is why the message is easy to miss.
Deleting the Job first means it is created again with the new tag and runs again, which is
harmless: the seed skips a database that is already seeded.

Changing a value in `configmap.yaml` and applying does not restart anything by itself; run
`kubectl -n atlas rollout restart deploy/api deploy/worker` afterwards.

## Known gotchas

- A wrong `MERIDIAN_CALLBACK_URL` in the ConfigMap means Meridian posts deliveries to an
  address nothing listens on. The watermark never moves and the only way data arrives is the
  worker's resync. Inside the cluster the API is `http://api:8000`, not `localhost` and not
  `backend` (that is the Compose name).
- `/health` returns 500 while the database is down. That is correct for readiness (stop
  sending traffic) and wrong for liveness (do not restart a healthy process because a
  dependency is down). The liveness probe therefore uses `/health/live`, which never touches
  the database.
- The live-updates stream (`/api/activity/stream`) is a long-lived response. ingress-nginx
  cuts responses off after 60 seconds unless the Ingress carries
  `nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"`, which `ingress.yaml` does.
- `kind load docker-image` must be repeated after every image build. The cluster does not
  see your local Docker images otherwise.
- Docker Desktop needs at least 8 GB of memory for the cluster plus the images now that
  MongoDB runs beside Postgres (6 GB was enough before). With less, pods sit in `Pending` or
  get `OOMKilled`.
- A StatefulSet's PersistentVolumeClaim is not deleted with the StatefulSet. After
  `kubectl -n atlas delete statefulset mongo` (or `kubectl delete namespace atlas`) the claim
  `mongo-data-mongo-0` may remain, and the next apply reuses it, old data included. A clean
  reset of the AI store is `kubectl -n atlas delete pvc mongo-data-mongo-0` after the
  StatefulSet is gone; `kind delete cluster` removes everything anyway.
- The api pod log shows `ai_store indexes not created, the store is unavailable` once at
  startup. The api Pods and `mongo-0` start together, and the api is usually first (its init
  container waits for Postgres only). Nothing to do: the first score or summary that reaches the
  store creates the indexes, and `mongo-0`'s log shows the `Index build: done building` lines.
- `/health` says `"ai_store":"unavailable"` while everything else works: the api cannot reach
  `mongo-0`. Scores and summaries still work and say `stored: false`; the cards show "history
  unavailable". `kubectl -n atlas get pod mongo-0` and its logs say why (most often the pod is
  still starting, or the Secret is missing `MONGO_USER` or `MONGO_PASSWORD`).
- The worker's liveness probe checks the age of `/tmp/worker-heartbeat`. It has a 60 second
  initial delay because the worker waits for the seed Job before its loop starts. If seeding
  takes much longer than that on your machine, the worker may restart once; it recovers on
  its own.
- The seed Job waits up to a minute for Postgres and tries three times (`backoffLimit: 2`).
  If it fails, `kubectl -n atlas logs job/atlas-seed` says why, and
  `kubectl -n atlas delete job atlas-seed` plus `kubectl apply -k k8s/base` runs it again.
- Every request through `http://localhost` is slow by a constant amount (about 0.2 s on the
  reference Windows laptop), even the static page from nginx. That is the Docker Desktop port
  mapping into the kind node, not Atlas: `curl -w '%{time_total}\n' localhost/health` and the
  same call on Compose (`localhost:8000/health`, about 0.02 s) show the difference. Measure
  inside the cluster when the number matters.
- The pods are `Running` but the browser says it cannot reach the API: look at
  `curl -s localhost/config.js`. It should say `apiUrl: ""`, same origin, set by an empty
  `VITE_API_URL` in `web.yaml`. If it names a host and port, something put a value there and
  the browser is calling that address instead of the Ingress.
- `kubectl apply -k k8s/base` prints `field is immutable` for `atlas-seed`: the old seed Job
  is still present and you changed the image tag. `kubectl -n atlas delete job atlas-seed`
  and apply again.
- The seed and worker pods do not get `JWT_SECRET` or `LLM_API_KEY`; only the api pods do
  (`worker.yaml` and `seed-job.yaml` name the one or two Secret keys they need). If you add a
  feature to the worker that needs another key, add a `secretKeyRef` for it there rather than
  giving the worker the whole Secret.

## Docker Desktop's built-in Kubernetes instead of kind

Turn it on in Docker Desktop (Settings, Kubernetes, Enable) and select the `docker-desktop`
context with `kubectl config use-context docker-desktop`. Skip the `kind create cluster` and
`kind load docker-image` steps: this cluster shares Docker's image store, so a built image is
already visible. Install the ingress controller with the cloud provider manifest instead of
the kind one:

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/cloud/deploy.yaml
```

Everything else, including the manifests and `http://localhost`, is the same. minikube also
works with the same manifests once its ingress addon is enabled, but reaching it on
`localhost` needs `minikube tunnel`, which is why this course uses kind.

## Teardown

```bash
kind delete cluster --name atlas
```

This removes the node container and with it every pod, volume and the Secret. Your images
stay in Docker, so bringing the cluster back is steps 2 to 5 again. To remove only Atlas and
keep the cluster: `kubectl delete namespace atlas`, then `kubectl -n atlas get pvc` to check
that the StatefulSet's claim `mongo-data-mongo-0` went with it (a PVC is namespaced, so
deleting the namespace removes it; deleting only the StatefulSet does not).
