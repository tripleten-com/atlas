# REPORT.md (reference)

In a student fork this file is the project report: the data audit (Task 1), the training evidence
and the calibration, alert-rate and lead-time tables (Task 2), the eval pass rate (Task 3), the
Kubernetes transcripts (Task 4) and the evidence each task's Check-list asks for.
The reference keeps only the Task 4 transcripts here, because the Task 4 Check-list asks for the
rollback drill transcript in this file; every measured number behind the reference lives in
`REFERENCE_NOTES.md`, and the task pages say what the rest of your report must contain.

Observed 2026-10-14 on the reference laptop (Windows 11, Docker Desktop, kind v0.33.0, node image
`kindest/node:v1.37.0`, images tagged `sha-d5956e4`). Commands are the ones in `k8s/README.md`.

## Ladder of proof (Task 4)

```
$ kubectl -n atlas get pods
NAME                        READY   STATUS      RESTARTS   AGE
api-74f88967c4-2tp2n        1/1     Running     0          101s
api-74f88967c4-llb48        1/1     Running     0          101s
atlas-seed-qwf46            0/1     Completed   0          101s
db-8747bbb9b-jtw2v          1/1     Running     0          101s
meridian-7ccf597655-tbk6t   1/1     Running     0          101s
mongo-0                     1/1     Running     0          101s
web-6fc7684bf-5t7xk         1/1     Running     0          101s
web-6fc7684bf-9dx6l         1/1     Running     0          101s
worker-9d8c75d59-zj8bw      1/1     Running     0          101s

$ kubectl -n atlas get pvc
NAME                 STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS
meridian-data        Bound    pvc-a567051f-7465-42e8-aa7b-9e1cb88cb807   1Gi        RWO            standard
mongo-data-mongo-0   Bound    pvc-95389836-81c4-4ae6-bc3c-78d153eac10c   1Gi        RWO            standard
pgdata               Bound    pvc-e83eba3e-53d6-471a-980a-3f3bf3cef58f   2Gi        RWO            standard

$ kubectl -n atlas get jobs
NAME         STATUS     COMPLETIONS   DURATION   AGE
atlas-seed   Complete   1/1           100s       101s

$ kubectl -n atlas logs job/atlas-seed | tail -1
[seed] done in 95s: patients=1,234, encounters=8,454, vitals=327,538, labs=197,340, medications=9,969, medication_administrations=77,938

$ curl -s localhost/health
{"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}
$ curl -s localhost/health/live
{"status":"alive"}

$ curl -s -o /dev/null -w '%{http_code}\n' localhost/api/facilities
401
$ TOKEN=$(curl -s -X POST localhost/api/auth/login ... clinician@riverside.example ...)
$ curl -s localhost/api/facilities -H "Authorization: Bearer $TOKEN"
DEMO 1234 patients, RNC 336, RGH 471

$ curl -s localhost/config.js
window.__ATLAS_CONFIG__ = { apiUrl: "" };

$ curl -s localhost/api/patients/199/risk/deterioration -H "Authorization: Bearer $TOKEN"
{"probability":0.0,"band":"low","alert":false,"model_version":"deterioration-20261014-0877aaca","trained_on":"local",
 "as_of":"2026-10-14T04:11:44Z","encounter_id":8391,"hours_admitted":145,"hours_of_vitals":22,"state":"scored","stored":true}
$ curl -s "localhost/api/patients/199/ai/history?feature=early-warning&limit=5" -H "Authorization: Bearer $TOKEN"
{"items":[{"created_at":"2026-10-14T11:42:52.971000Z","feature":"early-warning","model_version":"deterioration-20261014-0877aaca",
 "probability":0.0,"band":"low","state":"scored","text_preview":null}],"stored":true}

$ kubectl -n atlas logs deploy/worker --tail=3
2026-10-14 11:42:13,073 INFO atlas.worker applied batch of 7: {'processed': 7}
2026-10-14 11:42:14,088 INFO atlas.worker applied batch of 5: {'processed': 5}
2026-10-14 11:42:15,113 INFO atlas.worker applied batch of 14: {'processed': 14}

$ python scripts/smoke.py --base http://localhost
smoke ok   (12 ok, 2 skip: Meridian has no Ingress route; stay summary, no LLM_API_KEY in the Secret)
```

## Rollback drill (Task 4)

```
$ kubectl -n atlas set image deploy/api api=atlas-api:sha-bad
deployment.apps/api image updated

$ kubectl -n atlas get pods -l app=api
NAME                   READY   STATUS            RESTARTS   AGE
api-688d7bf8ff-srjg7   1/1     Running           0          38s
api-688d7bf8ff-z4wgv   1/1     Running           0          55s
api-7456d6d4db-995c2   0/1     PodInitializing   0          8s

$ kubectl -n atlas rollout status deploy/api --timeout=30s
Waiting for deployment "api" rollout to finish: 1 out of 2 new replicas have been updated...
error: timed out waiting for the condition

$ kubectl -n atlas get pods -l app=api
NAME                   READY   STATUS             RESTARTS   AGE
api-688d7bf8ff-srjg7   1/1     Running            0          2m42s
api-688d7bf8ff-z4wgv   1/1     Running            0          2m59s
api-7456d6d4db-fpcpk   0/1     ImagePullBackOff   0          41s

$ kubectl -n atlas describe pod api-7456d6d4db-fpcpk | tail -4
  Normal   BackOff    17s               kubelet   Back-off pulling image "atlas-api:sha-bad"
  Warning  Failed     17s               kubelet   Error: ImagePullBackOff
  Normal   Pulling    3s (x3 over 40s)  kubelet   Pulling image "atlas-api:sha-bad"
  Warning  Failed     3s (x3 over 40s)  kubelet   Error: ErrImagePull

$ curl -s localhost/health
{"status":"ok","database":"ok","migrations":1,"ai_store":"ok"}

$ kubectl -n atlas rollout history deploy/api
REVISION  CHANGE-CAUSE
1         <none>
2         <none>
3         <none>

$ kubectl -n atlas rollout undo deploy/api
deployment.apps/api rolled back
$ kubectl -n atlas rollout status deploy/api
deployment "api" successfully rolled out
$ kubectl -n atlas get deploy/api -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
atlas-api:sha-d5956e4
```

What happened: the Deployment started one new Pod with the bad tag and kept both old Pods, because
a rolling update removes an old Pod only once a new one is Ready. The new Pod went
`PodInitializing` (its init container uses the good tag), then `ErrImagePull`, then
`ImagePullBackOff` after about 40 seconds; `/health` kept answering the whole time. `rollout status
--timeout=30s` ended with `error: timed out waiting for the condition`, which is the signal that the
rollout is stuck. `rollout undo` went back to revision 2 and the bad Pod was terminated. Revision 2
is the restart made after the image was rebuilt (same tag; `rollout restart` creates a revision).

## The broken Deployment (Task 4)

Not run in the reference pass. `k8s/exercises/broken-api.yaml` plants three faults (the first build
agent saw `CreateContainerConfigError`, `couldn't find key JWT_SECRETS in Secret
atlas/atlas-secrets`); the student's diagnosis of all three goes here.
