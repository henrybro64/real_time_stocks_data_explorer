# Postgres and AppArmor on Ubuntu 26.04 (#308)

After a k3s restart on the home node, `stock-ticker-postgres` never started and the site answered "no available server". The cause is AppArmor denying signals inside the container. This page covers what we know, what the repo now does, how to check it, how to roll it back, and when to remove the workaround.

**Data safety rules (apply to every step on this page):** never delete or recreate the Postgres PVC, PV or PGDATA. Never run `initdb` against existing data. Never reinstall k3s. Never disable AppArmor globally. Never change the `spending-tracker` namespace from this repo. Back up before any production change.

## Environment

Ubuntu 26.04.1 LTS, kernel `7.0.0-42-generic`, k3s `v1.34.5+k3s1` (bundled containerd), single node on a Hyper-V VM. Image `postgres:16-alpine`, Deployment `stock-ticker-postgres` in namespace `stock-ticker`, PVC `stock-ticker-postgres-data` (2Gi, RWO, `local-path`).

## What happened

### Confirmed on our node (from #308)

| Observation | Meaning |
| --- | --- |
| PID 1 stuck in `gosu postgres /usr/local/bin/docker-entrypoint.sh postgres`; nothing listening on 5432 | The entrypoint never got to start Postgres |
| `gosu postgres id` and `gosu root id` time out in the confined container; `su postgres -s /bin/sh -c id` works | The hang is in `gosu` itself, not in the target user |
| Container label `cri-containerd.apparmor.d//&unconfined (enforce)` | The containerd profile is **stacked** with `unconfined` |
| Kernel audit log: AppArmor `DENIED` signal operations from `gosu`, including `rtmin+1`, plus profile-stacking messages | AppArmor blocks signals sent inside the container |
| Disposable `postgres:16-alpine` pod with `appArmorProfile.type: Unconfined` (no PVC): `gosu postgres id` returns `uid=70(postgres)`, `/proc/1/attr/current` is `unconfined` | Removing AppArmor confinement from that one container removes the hang |
| Live Deployment patched to `Unconfined`: logs show `Database directory appears to contain a database; Skipping initialization` and `database system is ready to accept connections` | Existing data was kept, no `initdb` ran |
| A similar Postgres failure in the separate `spending-tracker` namespace | Points to the host/runtime, not this app. Not changed here. |

### Upstream reports (outside evidence)

- [containerd#14485](https://github.com/containerd/containerd/issues/14485): on Ubuntu 26.04 with kernel 7.0.0-42 and the Ubuntu default `kernel.apparmor_restrict_unprivileged_unconfined=1`, runc runs unconfined without `CAP_MAC_OVERRIDE`, so its change into the container profile is converted to a stack (`change_profile unprivileged unconfined converted to stacking`). Tasks get the label `cri-containerd.apparmor.d//&unconfined`. containerd's default profile only allows `signal` and `ptrace` with `peer=cri-containerd.apparmor.d`, the unstacked name, so processes in the same container cannot signal each other. The reporter's Postgres failed with `could not signal for checkpoint: Permission denied`. Reproduction without Kubernetes, from that issue:

  ```bash
  setpriv --reuid 65534 --regid 65534 --clear-groups \
    aa-exec -p cri-containerd.apparmor.d -- sh -c 'sleep 30 & kill -URG $!'
  # kill: Permission denied
  ```

- [containerd PR #14487](https://github.com/containerd/containerd/pull/14487) adds `signal` and `ptrace` rules for the stacked peer (`{{.Name}}//&*`). As of 2026-11-01 it is **open, not merged, not in any containerd release, and not backported to 2.3.x**, so no k3s release carries it. A reviewer reports it fixes Ubuntu 26.04 with kernel 7.0.0-42.
- [jekyo/jekyo#33](https://github.com/jekyo/jekyo/issues/33): same OS, k3s with containerd 1.7.23-k3s2. `gosu` spins at 100% CPU. When they ran Postgres as uid 999 to skip `gosu`, Postgres then failed on `pg_ctl: could not send stop signal ... Permission denied` and on recovery with `could not signal for checkpoint`. So **running as non-root does not fix this**: Postgres needs signals between its own processes.
- Older, related containerd fixes for Ubuntu 23.10+ (signals from the `runc` profile) are in [containerd#10123](https://github.com/containerd/containerd/pull/10123). They do not cover the stacked label.

### Root cause and confidence

| Claim | Confidence | Basis |
| --- | --- | --- |
| AppArmor denies signals inside the Postgres container, which blocks startup | **High** | Our audit log denials, our `Unconfined` A/B test, matching upstream reports |
| The denial comes from the stacked label `cri-containerd.apparmor.d//&unconfined` not matching containerd's `peer=` rule | **High, not reproduced by us** | Our label matches containerd#14485 exactly; we have not run its `aa-exec` reproduction on our node |
| The stack is caused by `kernel.apparmor_restrict_unprivileged_unconfined=1` | **Medium** | Upstream analysis; we have not read the sysctl or toggled it on our node (and must not toggle it, see below) |
| `gosu` hangs because Go's `setuid`/`setgroups` signals every thread with `SIGRTMIN+1` (the `rtmin+1` in our log) and that signal is denied | **Medium, inference** | Matches the logged signal name and the `su` vs `gosu` difference; not traced |
| Why the problem showed up only after this restart | **Unknown** | Likely the first restart after a kernel or AppArmor package update. Check `/var/log/apt/history.log` and `last reboot` on the node |

## What the repo does now

1. **`k8s/postgres.yaml`** sets this on the `postgres` container only:

   ```yaml
   securityContext:
     appArmorProfile:
       type: Unconfined
   ```

   It is the same field and value as the live patch. With the field in the repo, every apply sets it, and a template edit or rollback cannot quietly drop it before the next restart. The same `securityContext` also sets `allowPrivilegeEscalation: false`: `gosu` only drops from root to `postgres`, which `no_new_privs` still allows. Together with the readiness probe below, this changes the Postgres pod template, so **the first rollout of this change restarts Postgres once** (Recreate: the old pod stops before the new one starts). Plan it as described in [Production rollout](#production-rollout-of-this-change).

   It is container-level, not pod-level, so a future sidecar or init container stays confined. `backend/tests/unit/infra/test_k8s_postgres.py` fails if any other container or pod in `k8s/` sets an AppArmor profile, or if the pod-level security context changes. The `appArmorProfile` field needs Kubernetes 1.30+. Kubelet only checks that the host supports AppArmor when some profile is not `Unconfined` (`isRequired` in [kubernetes `pkg/security/apparmor/helpers.go`](https://github.com/kubernetes/kubernetes/blob/master/pkg/security/apparmor/helpers.go)), so k3d on macOS and nodes without AppArmor still admit the pod. For the same reason, do not replace this with an explicit `RuntimeDefault` when the workaround goes away: delete the block instead. An explicit `RuntimeDefault` is rejected on a node without AppArmor.

2. **Only one Postgres can use the volume.** `replicas: 1` and `strategy: Recreate` are unchanged and now under test. RWO alone does not prevent this: on a single node, two pods can mount the same `local-path` volume. Recreate stops the old pod before starting a new one. The PVC spec is also under test, since a bound PVC's spec is immutable and an edit would fail the apply.

3. **`migrate-db` waits for Postgres.** The backend init container runs `python -m scripts.wait_for_database && alembic upgrade head`. The wait retries `select 1` with backoff of 1, 2, 5, then 10 seconds, for up to `DATABASE_WAIT_SECONDS` (default 300). Each try is capped at 10 seconds. It logs `db.wait.retry` with host, port, database and the driver's first error line, then either `db.wait.ready` or `db.wait.timed_out` with a pointer to this page. It never logs the password. If it times out, Alembic does not run, and kubelet retries the pod as before.

4. **Checks.** `scripts/verify-k3s-post-deploy.sh` (read-only) now checks the Postgres AppArmor field, that exactly one Postgres pod exists, PVC `stock-ticker-postgres-data` is Bound, `pg_isready`, signal or `Permission denied` errors in the Postgres log, migrate-db `Completed`, ready EndpointSlices for Postgres, Redis, backend and frontend, that the frontend Service answers, and that NetworkPolicy `stock-ticker-postgres-ingress` exists. It warns if Postgres ran `initdb` in the current pod. A backend pod whose migrate-db has not finished is reported as `not-finished` rather than skipped. `scripts/ci-k3s-smoke.sh` asserts the field reached the Deployment, that migrate-db logged `db.wait.ready`, and that a pod without an allowed label is `BLOCKED` from Postgres (DNS is resolved first, so a DNS failure cannot pass as blocked).

### Security tradeoff

`Unconfined` removes AppArmor's mandatory access control from the Postgres container. Namespaces, cgroups, the default capability set, the non-privileged container and the lack of host namespaces still apply. Postgres is only reachable through the ClusterIP Service inside the cluster.

What we give up: if Postgres or its entrypoint were compromised, AppArmor would not limit file, mount, or signal access inside the container. The API, worker, frontend, Redis and every other container stay on the runtime default profile.

We rejected these alternatives:

| Option | Why not |
| --- | --- |
| Run Postgres as uid 70 to skip `gosu` | Postgres still needs internal signals (jekyo#33), so the outage would remain |
| `sysctl kernel.apparmor_restrict_unprivileged_unconfined=0` | Host-wide change that weakens Ubuntu's hardening for every process. The issue forbids global AppArmor changes |
| Load a fixed `cri-containerd.apparmor.d` profile on the host before k3s starts | Works upstream, but changes confinement for every container on the node, including `spending-tracker`. Needs host changes outside this repo. Can be a separate, approved change |
| `Localhost` custom profile for Postgres only | Needs a profile file on the node, loaded on every boot. If it is missing, kubelet refuses to start Postgres. More risk than benefit until upstream ships the fix |

Compensating controls in this change:

- **`k8s/postgres-networkpolicy.yaml`:** only pods labelled `app=stock-ticker-backend` (API and its `migrate-db` init container) or `app=stock-ticker-backend-worker` may open TCP 5432 on Postgres. Those are the only manifests that load `stock-ticker-backend-database`, and a test keeps that list in step. Not affected: `kubectl exec` and port-forward (the backup and restore scripts connect over loopback inside the Postgres pod) and kubelet probes from the node. Egress is unchanged. Adding the policy does not restart any pod. On k3s the policy controller admits a **new** pod's IP about a second after the pod starts (measured on k3d; a slower node may take a few seconds), and rejects connections before that. `migrate-db` connects first and retries through it with the wait above. The API container starts after `migrate-db`, the API's own startup bootstrap retries (#290), and the worker needs several seconds of imports plus its Redis wait before any job touches the database. A job that still lands in the window skips one tick, as with any brief database outage. Rollback: `kubectl -n stock-ticker delete networkpolicy stock-ticker-postgres-ingress`. A new Postgres client needs its label added to the policy.
- **`allowPrivilegeEscalation: false`** on the Postgres container.
- **Readiness asks Postgres.** The readiness probe runs `pg_isready -q -U postgres -d postgres` (no shell, local socket, 5s timeout). A TCP check passes while Postgres is still starting or stuck in recovery. In the upstream reports, recovery hung on a denied checkpoint signal while TCP accepted. Startup and liveness stay on TCP. The TCP startup probe keeps the socket-only server that `initdb` runs on first boot from counting as Ready, and a liveness restart in the middle of a long recovery would make it worse. The earlier comment about exec-probe flakiness was about the 1s default timeout and env expansion; this probe has neither.

Still open, as its own issue: the in-cluster credentials are the committed defaults (`postgres`/`postgres` in `k8s/postgres.yaml` and `k8s/backend-database-secret.yaml`). They cannot simply be changed in the repo. `POSTGRES_PASSWORD` is only read when `initdb` creates a new data directory, so changing the Secret would leave the existing database on the old password while the backend uses the new one, and the backend would be locked out. Rotation needs an `ALTER ROLE` on the live database and a coordinated Secret update.

### Removal criteria

Remove `Unconfined` only when **all** of these hold:

1. A containerd release includes the fix for containerd#14485 (PR #14487 or equivalent), and a k3s release bundles that containerd. Check with `sudo k3s ctr version`.
2. The node runs that k3s version.
3. On the node, disposable pods with the runtime default profile and **no PVC** pass both checks. They run in `default`, never in `stock-ticker` or `spending-tracker`.

   The timeouts run on the host on purpose: while the bug is present, a `timeout` inside the container cannot kill `gosu`, because its signal is denied too. A hang or timeout means the bug is still there.

   ```bash
   # gosu and an in-container signal. Must print uid=70(postgres) and SIGNAL-OK.
   sudo timeout 90 k3s kubectl -n default run aa-check --rm -i --restart=Never --image=postgres:16-alpine -- \
     sh -c 'gosu postgres id && { sleep 30 & pid=$!; kill -URG "$pid" && echo SIGNAL-OK; }'
   sudo k3s kubectl -n default delete pod aa-check --ignore-not-found

   # A throwaway Postgres must init, accept connections and stop cleanly. Must print PG-OK.
   sudo k3s kubectl -n default run pg-check --restart=Never --image=postgres:16-alpine \
     --env=POSTGRES_PASSWORD=throwaway
   sudo timeout 240 k3s kubectl -n default exec pg-check -- sh -c \
     'until pg_isready -h 127.0.0.1; do sleep 2; done; su postgres -c "pg_ctl -D /var/lib/postgresql/data stop"'
   sleep 5
   sudo k3s kubectl -n default logs pg-check | grep -E -i 'permission denied|could not (send|signal)' \
     && echo FAILED || echo PG-OK
   sudo k3s kubectl -n default delete pod pg-check --ignore-not-found
   ```
4. Then remove the `securityContext` block from `k8s/postgres.yaml`, update `test_k8s_postgres.py`, `EXPECTED_POSTGRES_APPARMOR` in `scripts/verify-k3s-post-deploy.sh` and the check in `scripts/ci-k3s-smoke.sh`. Roll out in a maintenance window after a backup. That rollout restarts Postgres.

## Diagnose

Use the same kubectl as the deploy (`sudo k3s kubectl` on the node).

```bash
kubectl -n stock-ticker get pods -l app=stock-ticker-postgres -o wide
kubectl -n stock-ticker logs deploy/stock-ticker-postgres -c postgres --tail=80
kubectl -n stock-ticker get deploy stock-ticker-postgres \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="postgres")].securityContext}{"\n"}'
# What PID 1 is, and its AppArmor label:
kubectl -n stock-ticker exec deploy/stock-ticker-postgres -c postgres -- sh -c \
  'tr "\0" " " </proc/1/cmdline; echo; cat /proc/1/attr/apparmor/current 2>/dev/null || cat /proc/1/attr/current'
kubectl -n stock-ticker exec deploy/stock-ticker-postgres -c postgres -- pg_isready -h 127.0.0.1
kubectl -n stock-ticker logs deploy/stock-ticker-backend -c migrate-db --tail=40

# On the node (read-only):
sudo journalctl -k --since "-1h" | grep -i 'apparmor="DENIED"' | tail -20
sysctl kernel.apparmor_restrict_unprivileged_unconfined
sudo aa-status | grep -i containerd
sudo k3s ctr version
```

Signs of this problem: PID 1 is `gosu ...`, the label ends in `//&unconfined`, `DENIED` lines with `operation="signal"`, or `could not signal` in the log.

## Recovery

If Postgres is stuck again after a restart:

1. **Back up the current objects**, even though Postgres is down:

   ```bash
   mkdir -p ~/stock-ticker-backup-$(date +%Y%m%d-%H%M%S) && cd "$_"
   kubectl -n stock-ticker get deploy stock-ticker-postgres -o yaml > deploy-postgres.yaml
   kubectl -n stock-ticker get pvc stock-ticker-postgres-data -o yaml > pvc-postgres.yaml
   kubectl get pv "$(kubectl -n stock-ticker get pvc stock-ticker-postgres-data -o jsonpath='{.spec.volumeName}')" -o yaml > pv-postgres.yaml
   ```

2. **Restore the field** if it is missing. A strategic-merge patch keyed by the container name:

   ```bash
   kubectl -n stock-ticker patch deploy stock-ticker-postgres -p \
     '{"spec":{"template":{"spec":{"containers":[{"name":"postgres","securityContext":{"appArmorProfile":{"type":"Unconfined"}}}]}}}}'
   kubectl -n stock-ticker rollout status deploy/stock-ticker-postgres --timeout=600s
   ```

   Recreate stops the old pod first. If the old pod hangs in `Terminating`, wait. Do **not** use `--force --grace-period=0` on the Postgres pod: that removes the object while its container may still run, and a second Postgres could start against the same PGDATA.

3. **Confirm the data was kept.** The log must show `Database directory appears to contain a database; Skipping initialization`. If it shows `The files belonging to this database system will be owned by...` on a cluster that had data, stop. Do not write to the database. Check that the pod mounted the right PV (`pv-postgres.yaml` above) before doing anything else.

4. **Let the backend recover.** `migrate-db` retries on its own. If the backend pod is in a long `Init:CrashLoopBackOff` backoff, delete that backend pod normally (`kubectl -n stock-ticker delete pod -l app=stock-ticker-backend`) to retry at once.

5. Run the [validation](#validation) steps.

## Production rollout of this change

Only after the PR is reviewed and the operator approves. Nothing here was applied to the live cluster.

1. Back up, and confirm the backup can be read:

   ```bash
   KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-backup-postgres.sh
   tail -n 3 stocks-*.sql            # expect "PostgreSQL database dump complete"
   grep -c '^COPY public\.' stocks-*.sql
   ```

   Also save the Deployment, PVC and PV manifests as in [Recovery](#recovery) step 1.

2. Record the Postgres pod UID and the backup time, then preview the change:

   ```bash
   sudo k3s kubectl -n stock-ticker get pod -l app=stock-ticker-postgres -o jsonpath='{.items[0].metadata.uid}{"\n"}'
   sudo k3s kubectl -n stock-ticker diff -f k8s/postgres.yaml
   ```

   Expected Postgres template changes, and nothing else: `allowPrivilegeEscalation: false` and the readiness probe switching from `tcpSocket` to `exec` `pg_isready`. The `appArmorProfile` value should match the live patch. Also expected: the new `NetworkPolicy`. If the diff shows any other Postgres change (image, volumes, `PGDATA`, replicas, strategy, PVC), stop and find out why.

3. In a maintenance window, deploy: `./scripts/deploy-k3s.sh k3s`. Expect one Postgres restart and a backend rollout (its init command changed). The site is unavailable for roughly the Postgres restart time.
   - Exactly one new Postgres ReplicaSet (`kubectl -n stock-ticker get rs -l app=stock-ticker-postgres`) and a new pod UID. The old pod is gone before the new one starts (Recreate).
   - The new pod's log shows `Skipping initialization`, never `The files belonging to this database system`.
   - If the new pod is not Ready within a few minutes, do not delete anything. Roll Postgres back with `kubectl -n stock-ticker rollout undo deployment/stock-ticker-postgres`. That returns to the previous template, which carries the live `Unconfined` patch. Then collect [Diagnose](#diagnose) output. Before the next deploy, read the readiness note under [Rollback](#rollback).

   Checked on a disposable k3d cluster; see [Local evidence](#local-evidence). It has not been run on the Ubuntu 26.04 node.

4. Validate (below). Then, in a planned window, restart k3s (`sudo systemctl restart k3s`) or reboot the node and validate again. That is the test for "starts after a node restart".

## Local evidence

Disposable k3d cluster, k3s v1.33.6 with containerd 2.1.5, isolated kubeconfig, deleted afterwards. The node is not Ubuntu 26.04, so this proves the Kubernetes mechanics, not the AppArmor fix itself.

| Check | Result |
| --- | --- |
| `main` manifest, then write a row, then the strategic-merge live patch | Patched pod Ready |
| Apply this branch (kustomize-rendered) | Exactly one restart (new ReplicaSet, new pod UID, 1 pod). `Skipping initialization`, row kept |
| Live container | `allowPrivilegeEscalation: false`, `Unconfined`, PID 1 runs as `postgres`, `NoNewPrivs: 1`, `pg_isready` readiness |
| Policy: `app=stock-ticker-backend` / `-worker` | Connect, once the pod is a few seconds old |
| Policy: `app=intruder` / `app=stock-ticker-frontend` | Blocked. Control without the policy: connects |
| Policy admission delay for a new allowed pod | About 1s, with immediate rejects before that |
| Kubelet TCP startup/liveness under the policy | Ready, 0 restarts after 75s |
| `k8s-backup-postgres.sh` under the policy | Dump written, contains the test table |
| `rollout undo` | Back to the patched template, row kept. Re-applying the unchanged branch is rejected (see [Rollback](#rollback)). Both ways out work |
| Fresh install (`initdb`) with this branch | Ready in 9s, 0 restarts. No `FATAL` or role lines from the probe after 40s |

Earlier round (before the probe and `allowPrivilegeEscalation` were added): applying the `Unconfined` field alone over the live patch kept the same pod and revision.

## Validation

After any deploy, OS, kernel, AppArmor or k3s upgrade, or node restart:

```bash
KUBECTL_CMD="sudo k3s kubectl" ./scripts/verify-k3s-post-deploy.sh
```

It must pass, including:

- Postgres AppArmor profile `Unconfined`, exactly one Postgres pod, `stock-ticker-postgres-data` Bound, `pg_isready`, no signal errors.
- Backend `migrate-db` `Completed`, `alembic current` at head, backend and worker ready.
- Ready EndpointSlices for Postgres, Redis, backend and frontend, and the frontend Service answers.
- Row counts match the pre-change backup.

Then open the site in a browser and sign in. See [post-deploy-verification.md](post-deploy-verification.md) for the full checklist.

## Rollback

- **Backend wait only:** `sudo k3s kubectl -n stock-ticker rollout undo deployment/stock-ticker-backend`, or revert the `k8s/backend.yaml` and `backend/scripts/wait_for_database.py` change and redeploy. No data is affected; it only changes how migrate-db starts.
- **NetworkPolicy only:** `sudo k3s kubectl -n stock-ticker delete networkpolicy stock-ticker-postgres-ingress`. No pod restarts. A later `deploy-k3s.sh` re-creates it unless `k8s/postgres-networkpolicy.yaml` is removed from `k8s/kustomization.yaml`.
- **Readiness probe or `allowPrivilegeEscalation`:** `sudo k3s kubectl -n stock-ticker rollout undo deployment/stock-ticker-postgres` (one Postgres restart, back to the previous template with `Unconfined`).

  **After an undo, do not re-run `deploy-k3s.sh` with this branch's `k8s/postgres.yaml` unchanged.** Client-side apply then adds the `exec` handler without removing the live `tcpSocket` one. The API server rejects the Deployment with `readinessProbe.tcpSocket: Forbidden: may not specify more than 1 handler type`, and the rest of the deploy stops there. Nothing is changed or lost, but the deploy fails. Pick one of these first (both tested on k3d, data kept):

  - **Stay rolled back:** revert the readiness probe and `allowPrivilegeEscalation` lines in `k8s/postgres.yaml` (keep `appArmorProfile: Unconfined`), then deploy.
  - **Roll forward again:** replace the live probe, then deploy the branch as is:

    ```bash
    sudo k3s kubectl -n stock-ticker patch deploy stock-ticker-postgres --type=json -p \
      '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe","value":{"exec":{"command":["pg_isready","-q","-U","postgres","-d","postgres"]},"periodSeconds":10,"timeoutSeconds":5,"failureThreshold":3}}]'
    ```
- **The AppArmor field:** do not roll it back on Ubuntu 26.04 until the [removal criteria](#removal-criteria) are met. Removing it brings back the outage on the next Postgres start. If it has to go anyway, remove it in a maintenance window after a backup, and keep the patch command from [Recovery](#recovery) ready.
- Never use `k8s-teardown.sh` or `k8s-redeploy.sh` as a rollback. Both delete the Postgres PVC.
