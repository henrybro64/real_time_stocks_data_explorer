# Local Kubernetes deployment

Deploy on local **k3d** (macOS) or **k3s** (Ubuntu). From the repo root after the cluster exists:

```bash
./scripts/deploy-k3s.sh k3d   # macOS
./scripts/deploy-k3s.sh k3s   # Ubuntu
```

Remove the app (including Postgres PVC): `./scripts/k8s-teardown.sh k3d|k3s` or `kubectl delete -k k8s`. Redeploy in one step: `./scripts/k8s-redeploy.sh k3d|k3s`.

Optional workload monitoring: [observability.md](observability.md) (`kubectl apply -k k8s/observability` or `OBSERVABILITY=1 ./scripts/deploy-k3s.sh …`).

## Prerequisites

| Platform | Cluster | Notes |
| --- | --- | --- |
| macOS | **k3d** (not native k3s) | `colima + docker + k3d`; Docker Desktop not required |
| Ubuntu | native **k3s** | Docker Engine for local image builds |

The deploy script does **not** create the cluster — create it first, then deploy.

## macOS (k3d) quick path

```bash
brew install colima docker docker-buildx kubectl k3d
colima start --cpu 4 --memory 8 --disk 30
docker context use colima && docker buildx use colima

k3d cluster create stock-ticker
kubectl config use-context k3d-stock-ticker

./scripts/deploy-k3s.sh k3d
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open `http://127.0.0.1:3000`. LAN access without port-forward: NodePort **`http://<node-ip>:30080`**.

Delete app: `./scripts/k8s-teardown.sh k3d`. Delete cluster: `k3d cluster delete stock-ticker`.

## Ubuntu (k3s) quick path

Install Docker Engine and k3s ([k3s install docs](https://docs.k3s.io/installation)). Add your user to the `docker` group and verify `docker ps`.

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes

./scripts/deploy-k3s.sh k3s
sudo k3s kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Default: script uses `sudo k3s kubectl`. For plain `kubectl`, copy kubeconfig to `~/.kube/config` (see **kubectl as your user** below).

Remove app: `./scripts/k8s-teardown.sh k3s`. Uninstall k3s: `sudo /usr/local/bin/k3s-uninstall.sh`.

## What gets deployed

- `postgres:16-alpine` + **2Gi PVC** (durable while cluster exists)
- `redis:7-alpine`
- `stock-ticker-backend:local`, `stock-ticker-frontend:local`
- Backend init container: `alembic upgrade head`; API seeds `default-user` when DB is reachable
- NodePort frontend on **30080** for LAN access

Expected pods: `stock-ticker-backend`, `stock-ticker-frontend`, `stock-ticker-postgres`, `stock-ticker-redis` — all `Running`.

## Helper scripts

| Script | Purpose |
| --- | --- |
| `deploy-k3s.sh` | Build/import images, `kubectl apply -k k8s`, wait for rollouts |
| `k8s-teardown.sh` | Delete all `k8s/` resources + Postgres PVC |
| `k8s-redeploy.sh` | Teardown then deploy |
| `k8s-backup-postgres.sh` | Logical `pg_dump` to `stocks-YYYYMMDD-HHMMSS.sql` |
| `k8s-restore-postgres.sh` | Restore from SQL dump |
| `k8s-diagnose.sh` | Collect deploy/PVC/pod state for troubleshooting |
| `k8s-unstick-namespace.sh` | Clear stuck `Terminating` namespace |
| `k8s-test-host-postgres.sh` | Test host Postgres reachability from cluster |
| `ci-k3s-smoke.sh` | Local k3s smoke entrypoint |

**Deploy script steps:** build backend/frontend `:local` images; import into k3d/k3s; pre-pull `postgres:16-alpine` and `redis:7-alpine`; apply manifests; wait for Redis, Postgres, backend, frontend.

## Postgres backup and restore

Before teardown, back up if you need the data. Backup/restore scripts auto-detect `kubectl` vs `sudo k3s kubectl`, or set `KUBECTL_CMD` explicitly (same as `k8s-teardown.sh` / `k8s-diagnose.sh`).

```bash
./scripts/k8s-backup-postgres.sh
# k3s when plain kubectl cannot reach the cluster:
KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-backup-postgres.sh
```

**Restore after a fresh deploy:** the backend init container runs `alembic upgrade head`, so a **full** dump (schema + data) fails on duplicate objects. For repeated dev cycles, back up **data only** once schema is stable, deploy, wait for migrations, then restore:

```bash
./scripts/k8s-backup-postgres.sh stock-ticker stocks-data.sql --data-only
# after deploy + backend init (migrations) finished:
./scripts/k8s-restore-postgres.sh stocks-data.sql
```

A **full** dump is for disaster recovery into an **empty** database (wipe the PVC or first boot before restore). Deleting the namespace or PVC **without** backup removes database files.

## Multi-user auth in k8s

Edit `k8s/backend-auth-secret.yaml`: set `SESSION_SECRET`. Optional bootstrap: `LOCAL_AUTH_USERNAME` / `LOCAL_AUTH_PASSWORD`. Keep `AUTH_ENABLED=true` and `LOCAL_AUTH_ENABLED=true` in the backend ConfigMap. Apply and restart:

```bash
kubectl apply -k k8s
kubectl -n stock-ticker rollout restart deployment/stock-ticker-backend deployment/stock-ticker-frontend
```

Do not commit real secrets.

## Host Postgres (optional)

Point `k8s/backend-database-secret.yaml` at Mac/host Postgres via `host.lima.internal` or `host.docker.internal` (see `k8s-test-host-postgres.sh`). Remove `postgres.yaml` from `k8s/kustomization.yaml` to avoid two Postgres instances.

## k3s smoke validation

```bash
ARTIFACT_DIR=artifacts/k3s-smoke KUBECTL_CMD="sudo k3s kubectl" \
  pytest backend/tests/smoke/test_k3s_smoke.py
```

Records deploy, pod/service/PVC state, migration init, `/health`, frontend `/api/markets` proxy, Postgres `SELECT 1`, Redis `PING`. Artifacts under `artifacts/k3s-smoke/`. CI runs the same in `.github/workflows/test-and-smoke.yml`.

## Manual deploy (without helper script)

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .
# k3d: k3d image import … -c stock-ticker
# k3s: docker save + sudo k3s ctr -n k8s.io images import …
kubectl apply -k k8s
kubectl -n stock-ticker rollout status deployment/stock-ticker-backend
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

## Troubleshooting

Run `./scripts/k8s-diagnose.sh k3s` (or `k3d`) first and share the output when asking for help.

**Common issues**

- **Wrong cluster / namespace not found:** `kubectl config current-context`; create k3d cluster if missing.
- **k3s kubeconfig permission denied:** use `sudo k3s kubectl` or copy config to `~/.kube/config`.
- **Postgres PVC Pending / VolumeBinding timeout:** check disk space; restart `local-path-provisioner` in `kube-system`; delete stuck PVC and re-apply.
- **Postgres slow first boot:** `initdb` on new volume can take several minutes — normal.
- **Postgres Recreate strategy:** manifest uses `Recreate` so ReadWriteOnce volume is not attached by two pods during rollout.
- **Backend Init:CrashLoopBackOff:** check `kubectl logs … -c migrate-db`; ensure Postgres is Ready and `DATABASE_URL` matches in-cluster or host Postgres setup.
- **Stale `:local` image after rebuild:** `kubectl rollout restart deployment/stock-ticker-backend`.
- **buildx too old (macOS):** `brew install docker-buildx` and configure `cliPluginsExtraDirs` in `~/.docker/config.json`.

**Stuck namespace Terminating**

```bash
./scripts/k8s-unstick-namespace.sh stock-ticker
# k3s: KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-unstick-namespace.sh stock-ticker --force
```

**Force-delete hanging pods:** `kubectl -n stock-ticker delete pod --all --grace-period=0 --force`

## kubectl as your user (k3s)

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
kubectl get nodes
```

## API URL and optional Finnhub

Same-origin `/api` via the frontend Service is preferred — see [local-development.md](local-development.md#api-url-shape). For split origins, set `CORS_ORIGINS` and `NEXT_PUBLIC_API_BASE_URL`.

Optional key statistics (market cap, P/E, etc.): set `FINNHUB_API_KEY` on the backend and restart.
