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

### Docker Hub pull timeouts

`deploy-k3s.sh` pre-pulls `python:3.11-slim-bookworm` and `node:20-bookworm-slim` with retries, then falls back to `mirror.gcr.io` and `docker.m.daocloud.io` when Docker Hub times out. If pulls still fail:

1. Pull and tag manually (Google mirror):
   ```bash
   docker pull mirror.gcr.io/library/python:3.11-slim-bookworm
   docker tag mirror.gcr.io/library/python:3.11-slim-bookworm python:3.11-slim-bookworm
   docker pull mirror.gcr.io/library/node:20-bookworm-slim
   docker tag mirror.gcr.io/library/node:20-bookworm-slim node:20-bookworm-slim
   ./scripts/deploy-k3s.sh k3s
   ```
2. Add a registry mirror in `/etc/docker/daemon.json`, then `sudo systemctl restart docker`.
3. Point at a mirror directly:
   `BACKEND_BASE_IMAGE=mirror.gcr.io/library/python:3.11-slim-bookworm FRONTEND_BASE_IMAGE=mirror.gcr.io/library/node:20-bookworm-slim ./scripts/deploy-k3s.sh k3s`
4. Tune mirrors/retries: `DOCKER_PULL_MIRRORS=mirror.gcr.io DOCKER_PULL_RETRIES=2 ./scripts/deploy-k3s.sh k3s`

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

**Deploy script steps:** build backend/frontend `:local` images; import into k3d/k3s; pre-pull `postgres:16-alpine` and `redis:7-alpine`; apply manifests; wait for Redis, Postgres, backend, frontend. Registry images skip the local build/import and are pulled by the cluster instead — see [GitHub Container Registry](#github-container-registry).

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

## GitHub Container Registry

Publishing is additive. Local development still uses `stock-ticker-backend:local` and `stock-ticker-frontend:local` with `imagePullPolicy: Never`. You do not need GHCR to develop or run k3s.

### Where the packages appear

After a successful publish, the two packages show up under GitHub **Packages** (not at `ghcr.io` in a browser):

- [github.com/henrybro64?tab=packages](https://github.com/henrybro64?tab=packages)
- This repo’s right-hand **Packages** list
- Direct package pages:
  - `https://github.com/users/henrybro64/packages/container/real_time_stocks_data_explorer-backend`
  - `https://github.com/users/henrybro64/packages/container/real_time_stocks_data_explorer-frontend`

Because this repository is private, new packages inherit **private** visibility. Private GHCR storage counts against the GitHub Packages quota. After the first push you can change visibility on each package page if you want anonymous pulls.

The first Actions publish links each package to this repo (`GITHUB_TOKEN` + `org.opencontainers.image.source`). If you previously pushed the same package name from a laptop, `GITHUB_TOKEN` cannot write until you **link the package to this repository** on the package’s **Package settings → Manage Actions access** page.

### How tags are generated

Workflow: `.github/workflows/publish-ghcr.yml`.

- **`main` / `master` push:** Test And Smoke runs first. Publish starts via `workflow_run` only if `backend-pytest`, `frontend-playwright`, and `k3s-smoke` are green. A failing Allure/report job does **not** block images. Both images are built to a staging `build-<run_id>` tag first; SHA / `latest` / semver are applied only after both succeed. `latest` is written only if that SHA is still the default-branch tip (a slower older run cannot retag `latest`).
- **`v*.*.*` git tag:** Test And Smoke runs on the tag, then publish adds `1.2.3` / `1.2` (from `v1.2.3`) plus the commit SHA. It does not wait on a separate untested tag push.
- **`workflow_dispatch`:** allowed only on the default branch (manual republish; does not wait for CI).
- Pull requests, feature-branch dispatch, and failed required test jobs do **not** push images. Publish is not started for PR Test And Smoke runs.

| Tag | When |
| --- | --- |
| `<full-git-sha>` | Every publish. Prefer this for deployments (immutable). |
| `latest` | Default branch only (`main`). Convenience, not a pin. |
| `1.2.3` / `1.2` | When a `v1.2.3` git tag is pushed |
| `buildcache` | Buildx layer cache on the same package. Not a runnable app image — do not deploy it. |

Example references:

```text
ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:<git-sha>
ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:latest
ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:<git-sha>
ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:latest
```

Images include OCI labels `org.opencontainers.image.source` and `org.opencontainers.image.revision`.

Published images are **`linux/amd64` only**. That matches Ubuntu k3s. `k3d` on Apple Silicon will not run them without emulation — keep using locally built `:local` images on the Mac.

### Pull an image

Public package (or after you change visibility to public):

```bash
docker pull ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:<git-sha>
```

Private package — use a classic PAT with `read:packages` (not your GitHub password):

```bash
echo "$CR_PAT" | docker login ghcr.io -u henrybro64 --password-stdin
docker pull ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:<git-sha>
```

GitHub Actions publish uses `GITHUB_TOKEN` with `packages: write`. That token cannot be reused on your home cluster.

### Optional k3s/k3d pull from GHCR

Default deploy is unchanged (`:local` + import). To run published images instead:

```bash
export BACKEND_IMAGE=ghcr.io/henrybro64/real_time_stocks_data_explorer-backend:<git-sha>
export FRONTEND_IMAGE=ghcr.io/henrybro64/real_time_stocks_data_explorer-frontend:<git-sha>
# Private packages only — create the namespace, then the secret once (do not commit the PAT):
kubectl create namespace stock-ticker
kubectl -n stock-ticker create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=henrybro64 \
  --docker-password="$CR_PAT"
export IMAGE_PULL_SECRET=ghcr-pull
./scripts/deploy-k3s.sh k3s   # Ubuntu amd64. On Mac k3d keep :local images.
```

Digest pins also work: `ghcr.io/.../real_time_stocks_data_explorer-backend@sha256:<digest>`.

The script creates `stock-ticker` if needed, then fails with the same commands if `IMAGE_PULL_SECRET` is set but the secret is missing. It skips local app builds/imports, rewrites the Deployment images, and sets `imagePullPolicy` to `IfNotPresent` (or `Always` for `:latest`; digest refs stay `IfNotPresent`). Postgres and Redis still come from Docker Hub as today. Registry-only k3s deploys do not require Docker; k3d still does. Mixed local+GHCR pulls only the base image for the side that is built locally.

### Verify the image in use

```bash
kubectl -n stock-ticker get deploy stock-ticker-backend stock-ticker-frontend \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.template.spec.containers[0].image}{"\n"}{end}'
kubectl -n stock-ticker get pod -l 'app in (stock-ticker-backend,stock-ticker-frontend)' \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}'
```

`imageID` is the registry digest. Compare it to the digest on the GitHub package version page.

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
