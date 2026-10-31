# Local Kubernetes deployment

Deploy on local **k3d** (macOS) or **k3s** (Ubuntu). From the repo root after the cluster exists:

```bash
./scripts/deploy-k3s.sh k3d   # macOS
./scripts/deploy-k3s.sh k3s   # Ubuntu
```

Remove the app (including Postgres PVC): `./scripts/k8s-teardown.sh k3d|k3s` or `kubectl delete -k k8s`. Redeploy in one step: `./scripts/k8s-redeploy.sh k3d|k3s`.

Optional workload monitoring: [observability.md](observability.md) (`kubectl apply -k k8s/observability` or `OBSERVABILITY=1 ./scripts/deploy-k3s.sh …`).

Optional AWS outbound heartbeat (home → DynamoDB; AWS never calls the node):
[aws-health-monitor.md](aws-health-monitor.md). The CronJob is always applied;
without `k8s/aws-health-heartbeat-secret.yaml` it skips cleanly.

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

k3d cluster create stock-ticker -p "80:80@loadbalancer"
kubectl config use-context k3d-stock-ticker

./scripts/deploy-k3s.sh k3d
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open `http://127.0.0.1:3000`. LAN access without port-forward: NodePort **`http://<node-ip>:30080`**.

Friendly LAN hostname (HTTP Ingress, Traefik): the `-p "80:80@loadbalancer"` flag above publishes Traefik's port 80 on the machine running k3d. Point local DNS at that host, then open `http://stock-ticker.ai` (override with `STOCK_TICKER_HOSTNAME`). Native k3s already listens on the node. NodePort **30080** stays as a fallback. See [Local DNS and Ingress](#local-dns-and-ingress).

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
- `redis:7-alpine` + **1Gi PVC** and the `stock-ticker-redis-config` ConfigMap (#293). Redis saves RDB snapshots to the volume, so the quote, rankings and Taiwan-signal caches, stock-AI narratives and rate-limit counters survive a pod restart. A rollout or graceful restart loses nothing, because Redis saves on SIGTERM; a crash or OOM kill loses up to about 5 minutes, since normal write rates trigger the `save 300 10` snapshot rather than `save 60 1000`. `maxmemory 128mb` with `noeviction` stays under the 256Mi limit so a snapshot fork has headroom. A full Redis refuses new writes instead of deleting keys, so leader locks, refresh locks, daily markers and rate-limit counters stay until their own TTL. A refused cache write is a cache miss; a refused lease write is treated as Redis unavailable and that tick waits until expirations free memory. Expected use is far below the cap (roughly one entry per tracked symbol plus stock-AI narratives; the post-deploy script prints `used_memory`). If `evicted_keys` ever rises, the quote refresher logs `redis.keys_evicted` and `scripts/verify-k3s-post-deploy.sh` fails. The Deployment uses `Recreate` because the volume is ReadWriteOnce. The first rollout with the volume starts from an empty cache once. Editing the ConfigMap does not restart Redis: run `kubectl -n stock-ticker rollout restart deployment/stock-ticker-redis`.
- At start the worker waits up to `WORKER_REDIS_WAIT_SECONDS` (default 30) for Redis before starting jobs, so a worker that comes up a moment before the Redis Service after a node or k3s restart does not begin with every job skipped (#293). Redis client timeouts are settings (`REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS`, `REDIS_SOCKET_TIMEOUT_SECONDS`, both 2 s), with TCP keepalive and a 30 s idle health check.
- `stock-ticker-backend:local`, `stock-ticker-frontend:local`
- Backend API Deployment (`RUN_BACKGROUND_JOBS=false`) with init container `alembic upgrade head`
- Backend worker Deployment (`python -m app.worker`, `RUN_BACKGROUND_JOBS=true`) for quote refresher, alerts, samplers (#276). Schema upgrades stay on the API Deployment's `migrate-db` init container so two pods do not run Alembic at once. `/health` on port 8001 returns 503 when an enabled job has stopped succeeding or its thread has died; readiness uses it, so the pod leaves Ready. Liveness uses `/livez`, which returns 503 only when a job thread has died or is stuck (no completed loop pass for 3 poll intervals, at least an hour), so a Postgres or provider outage does not restart the worker (#289). Both Deployments set probe `timeoutSeconds: 5`. Replica count stays 1; the Redis locks still keep a second replica from repeating the same interval. A job tick or quote cycle skipped for leadership retries within seconds, and the worker releases the locks it owns when it stops (only for jobs whose thread has exited, so a still-running tick is never overlapped), so a restart does not leave jobs idle for a whole interval (#291, #292). A second replica that never wins leadership reports its jobs as stale on `/health` and stays not-ready; it is not restarted.
- NodePort frontend on **30080** for LAN access
- HTTP Ingress (`k8s/frontend-ingress.yaml`) for a LAN hostname (default `stock-ticker.ai`)
- Auth Secret generated locally by `deploy-k3s.sh` (not committed; see `.example`)

Expected pods: `stock-ticker-backend`, `stock-ticker-backend-worker`, `stock-ticker-frontend`, `stock-ticker-postgres`, `stock-ticker-redis` — all `Running`.

Portfolio history: the worker sampler (every 5 minutes) is the sole scheduler. The `stock-ticker-history-capture` CronJob stays in the manifests with `suspend: true` so it can be re-enabled for ops; prefer the capture endpoint for one-off runs (#275).

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

`./scripts/deploy-k3s.sh` creates `k8s/backend-auth-secret.yaml` (gitignored) with a random `SESSION_SECRET` and capture token when the file is missing. To customize, copy `k8s/backend-auth-secret.yaml.example`, fill real values, then apply:

```bash
kubectl apply -n stock-ticker -f k8s/backend-auth-secret.yaml
kubectl apply -k k8s
kubectl -n stock-ticker rollout restart deployment/stock-ticker-backend deployment/stock-ticker-backend-worker deployment/stock-ticker-frontend
```

Keep `AUTH_ENABLED=true` and `LOCAL_AUTH_ENABLED=true` in the backend ConfigMap. The backend refuses a default/placeholder `SESSION_SECRET` or bootstrap password at startup. Placeholder **capture** tokens are cleared and logged instead of crashing the API; those capture endpoints then return 503 until you set a real token (#284). Optional bootstrap: set both `LOCAL_AUTH_USERNAME` and `LOCAL_AUTH_PASSWORD` (8+ characters). Bootstrap is **create-only** — an existing username keeps its stored password unless you set `LOCAL_AUTH_RESET_PASSWORD=true` (#286). Leave both empty to rely on self-registration.

### Upgrade note: placeholder Secrets after #267 / #284

Older example files shipped values such as `replace-with-long-random-token`. After #267 those values crash the API. After #284:

| Value | Startup behaviour | How to rotate |
| --- | --- | --- |
| `SESSION_SECRET` empty, placeholder, or shorter than 16 characters | **Hard fail** (API and worker refuse to start) | `kubectl -n stock-ticker patch secret stock-ticker-backend-auth --type merge -p "{\"stringData\":{\"SESSION_SECRET\":\"$(openssl rand -hex 32)\"}}"` then restart. Rotating signs everyone out; usernames/passwords are unchanged. |
| `LOCAL_AUTH_PASSWORD` placeholder, or only one of `LOCAL_AUTH_USERNAME` / `LOCAL_AUTH_PASSWORD` set | **Hard fail** | Set both, or clear both. Do **not** invent a new password to silence the check. An existing username keeps its stored password unless `LOCAL_AUTH_RESET_PASSWORD=true`. |
| Capture tokens (`PORTFOLIO_HISTORY_*`, `RANKINGS_*`, `TAIWAN_*`, archive) | Soft: logged, treated as empty, capture returns 503 | Patch the Secret with `openssl rand -hex 32` (or leave empty). |

`deploy-k3s.sh` preflights Secrets before rollout and will not silently overwrite a live `stock-ticker-backend-auth` that differs from the local file. Set `FORCE_AUTH_SECRET_APPLY=1` to apply the local file anyway; the preflight then checks that file instead of the live Secret, so a fixed local file can repair a broken live `SESSION_SECRET`. The preflight uses the same placeholder rules as the API. Prefer `kubectl patch` for repairs; do not re-apply a stale local auth Secret during an outage.

Microsoft Entra External ID (#240, #299): nonsecret settings live in `k8s/backend.yaml` (`ENTRA_EXTERNAL_ID_ENABLED`, tenant, authority, redirect URI). Put `ENTRA_CLIENT_ID` and `ENTRA_CLIENT_SECRET` in the optional Secret `stock-ticker-backend-entra` (copy `k8s/backend-entra-secret.yaml.example`; gitignored; last `envFrom` so it overrides the empty ConfigMap client id). Local auth stays on (`LOCAL_AUTH_ENABLED=true`). Incomplete Entra config no longer crashes the API when local auth is enabled; Microsoft login stays off until client id/secret are present.

`deploy-k3s.sh` applies that Secret before the ConfigMap. If the Secret is missing, the script copies a live `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET` off `stock-ticker-backend-env` (or the auth Secret) into `k8s/backend-entra-secret.yaml` and applies it, so the empty client id in git does not erase the one already on the cluster. It does not print the values.

The same script rewrites `ENTRA_REDIRECT_URI` and `ENTRA_POST_LOGOUT_REDIRECT_URI` to the Ingress origin. `stock-ticker.ai` stays `http`. Any other `STOCK_TICKER_HOSTNAME`, including `stock.cloudforge64.com`, uses `https` unless `STOCK_TICKER_PUBLIC_SCHEME` is set. The app registration redirect URI has to be that exact origin plus `/api/auth/callback/entra`. For this site:

```bash
STOCK_TICKER_HOSTNAME=stock.cloudforge64.com ./scripts/deploy-k3s.sh k3s
```

Failed token exchange and a failed authorize redirect log HTTP status or the allowlisted OAuth `error`, the AADSTS numeric code, and correlation/trace/request IDs when Microsoft sends them. They never log `error_description`, codes, secrets, or tokens. HTTP 401 from Microsoft is usually `invalid_client` (wrong or unloaded secret); a bad authorization code is HTTP 400 `invalid_grant`.

`https` origins set `SESSION_COOKIE_SECURE=true` on `stock-ticker-backend-auth` during deploy. The plain HTTP Ingress keeps it `false`.

### Entra login verification (#299)

1. Confirm OIDC discovery: `curl -fsS https://stockticker.ciamlogin.com/4661e227-6bc3-4ff3-b9c2-df41f6420014/v2.0/.well-known/openid-configuration` returns 200.
2. Confirm the app registration redirect URI is exactly the deployed origin plus `/api/auth/callback/entra` (`https://stock.cloudforge64.com/api/auth/callback/entra` when `STOCK_TICKER_HOSTNAME=stock.cloudforge64.com`).
3. Confirm the pod has the intended secret (presence only): `kubectl -n stock-ticker exec deploy/stock-ticker-backend -- sh -c 'test -n "$ENTRA_CLIENT_SECRET" && echo secret_present'`. Do not print the value.
4. Sign in with Microsoft once. Success redirects to `/` and `/api/auth/status` shows `loggedIn: true` after a refresh. The first Entra identity creates a **new** `users.id` via `oauth_identities`; it does not merge onto an existing local username/portfolio.
5. Sign out, then sign in with local username/password. Existing local data is unchanged.
6. If Microsoft login still fails, inspect backend logs for `auth.entra.token_exchange_failed` (`http_status`, `oauth_error`, `aadsts_code`). Rotate the app-registration secret and patch `stock-ticker-backend-entra` when you see `invalid_client` / `AADSTS7000215`.

Do not commit real secrets.

## Local DNS and Ingress

k3s/k3d already ship Traefik. This repo adds a host-based Ingress to the existing ClusterIP Service `stock-ticker-frontend` (port 3000). It does **not** install a second ingress controller.

1. Deploy (sets Ingress host from `STOCK_TICKER_HOSTNAME`, default `stock-ticker.ai`):
   ```bash
   STOCK_TICKER_HOSTNAME=stock-ticker.ai ./scripts/deploy-k3s.sh k3s
   ```
2. On your router/local DNS, create an A record:
   ```text
   stock-ticker.ai -> <k3s-node-lan-ip>
   ```
   Example: `stock-ticker.ai -> 192.168.120.102`.
3. From a LAN client, open `http://stock-ticker.ai` (port 80 via Traefik).
4. Keep NodePort fallback: `http://192.168.120.102:30080`.

HTTPS/TLS and Cloudflare remote access are follow-ups after hostname routing is stable. Internal capture endpoints are not proxied through the frontend `/api` rewrite.

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

`kubectl apply -k k8s` does not create `stock-ticker-backend-auth`. Apply that Secret first or the backend and history CronJob stay in `CreateContainerConfigError`.

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .
# k3d: k3d image import … -c stock-ticker
# k3s: docker save + sudo k3s ctr -n k8s.io images import …
kubectl apply -f k8s/namespace.yaml
umask 077
cp k8s/backend-auth-secret.yaml.example k8s/backend-auth-secret.yaml
# Fill SESSION_SECRET (`openssl rand -hex 32`) and PORTFOLIO_HISTORY_CAPTURE_TOKEN.
# Do not commit the filled file.
kubectl apply -n stock-ticker -f k8s/backend-auth-secret.yaml
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
