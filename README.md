# Stock Ticker MVP

Starter monorepo for a stock ticker MVP with:

- `Next.js + TypeScript` frontend (Node **20+**; see `frontend/package.json` / `frontend/.nvmrc`)
- `FastAPI` backend
- mixed-language display rules
  - U.S. stocks in English
  - Taiwan stocks in Traditional Chinese

## Structure

```text
backend/   FastAPI backend
  redis/   Redis config (local dev)
frontend/  React frontend
k8s/       Kubernetes manifests for local k3s/k3d deployment
packages/
  shared-contracts/  shared TypeScript contracts
scripts/   local deployment helpers
docs/
  mvp.md
```

## Run

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Database bootstrap (Postgres)

```bash
cd backend
export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/stocks"

# 1) migrate schema
alembic upgrade head

# 2) seed single default user (id: default-user)
python -m scripts.seed_default_user

# 3) start app
python -m uvicorn app.main:app --reload
```

### Redis (local cache process)

Run Redis as a separate process managed from the backend folder:

```bash
cd backend
docker compose -f docker-compose.redis.yml up -d
```

Stop it:

```bash
cd backend
docker compose -f docker-compose.redis.yml down
```

Configure backend to use it (in `backend/.env`):

```bash
REDIS_URL=redis://127.0.0.1:6379/0
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Run In Local Kubernetes (`k3d` on macOS, `k3s` on Ubuntu)

This repo supports a no-registry local Kubernetes flow:

1. build the backend and frontend images locally
2. import those images into the cluster runtime
3. apply the manifests in `k8s/`
4. expose the frontend locally with `port-forward`

To **remove the app**, use `./scripts/k8s-teardown.sh k3d|k3s` or `kubectl delete -k k8s` (this also removes the Postgres PVC and its data). To **remove the cluster itself**, delete the k3d cluster or uninstall k3s — see **macOS (k3d)** and **Ubuntu Linux (k3s)** below.

The helper script for this flow is `scripts/deploy-k3s.sh`.

Important:

- macOS should use `k3d`, not native `k3s`
- Ubuntu Linux can use native `k3s`
- the deploy script does **not** create the cluster for you — create the cluster first, then run it
- Docker Desktop is **not** required on macOS; `colima + docker + k3d` is enough

### Clone the repo

```bash
git clone https://github.com/henrybro64/Real_Time_Stocks_Data_Explorer.git
cd Real_Time_Stocks_Data_Explorer
```

### macOS (k3d): create cluster → deploy → remove app → delete cluster

**1. Install tooling and start Colima (one-time per machine)**

```bash
brew install colima docker docker-buildx kubectl k3d
colima start --cpu 4 --memory 8 --disk 30
docker context use colima
docker buildx use colima
```

**2. Create the k3d cluster** (once; repeat only if you removed the cluster)

```bash
k3d cluster create stock-ticker
kubectl config use-context k3d-stock-ticker
kubectl get nodes
```

**3. Deploy the app** (builds `stock-ticker-*:local`, imports into k3d, `kubectl apply -k k8s`)

```bash
./scripts/deploy-k3s.sh k3d
kubectl -n stock-ticker get pods
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open the UI at `http://127.0.0.1:3000`.

#### Backup Postgres before you delete the namespace (recommended)

**Common practice** is a **logical backup** with PostgreSQL’s **`pg_dump`** (SQL file or custom format). That works across teardown/redeploy and does not depend on copying the raw PVC disk. For large production DBs, storage-level **volume snapshots** (CSI / your cloud) are also used; for local k3d/k3s, `pg_dump` is usually enough.

From the repo root, while `stock-ticker-postgres` is running:

```bash
./scripts/k8s-backup-postgres.sh
# writes stocks-YYYYMMDD-HHMMSS.sql in the current directory
```

Manual equivalent:

```bash
kubectl exec -n stock-ticker deploy/stock-ticker-postgres -- \
  sh -c 'PGPASSWORD=postgres pg_dump -h 127.0.0.1 -U postgres -d stocks --no-owner --no-acl' \
  > stocks-backup.sql
```

**Restore** after a fresh deploy (Postgres pod up; run **after** migrations if you rely on Alembic for schema — a **full** dump includes schema and can conflict with migrations; for repeated dev cycles, prefer backing up **data-only** once schema is stable: `pg_dump --data-only ...`, then restore with `./scripts/k8s-restore-postgres.sh` after the backend init has migrated):

```bash
./scripts/k8s-restore-postgres.sh stocks-backup.sql
```

**4. Remove the app from Kubernetes** (deletes everything defined in `k8s/`, including the **Postgres PVC** — **database data is removed** unless you backed it up above)

```bash
./scripts/k8s-teardown.sh k3d
# or: kubectl delete -k k8s
```

**5. Delete the k3d cluster** (optional; destroys the cluster and cluster-local volumes)

```bash
k3d cluster delete stock-ticker
```

**6. Stop Colima** (optional; frees the Docker VM)

```bash
colima stop
```

Expected pods after a healthy deploy:

```text
stock-ticker-backend    1/1 Running
stock-ticker-frontend   1/1 Running
stock-ticker-postgres   1/1 Running
stock-ticker-redis      1/1 Running
```

### Ubuntu Linux (k3s): install Docker → install k3s → deploy → remove → uninstall

**Install Git and Docker Engine**

```bash
sudo apt update
sudo apt install -y ca-certificates curl git

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Add your user to the `docker` group (above), then **apply that group in this terminal** — **one** of:

- log out and log back in (or reboot), **or**
- run `newgrp docker` once in the shell you use for `docker` / `kubectl`, **or**
- open a **new** terminal session after `usermod`

Then verify:

```bash
docker version
docker ps
```

If you see `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`, the group change is not active yet: run `newgrp docker` or sign out/in. Do **not** rely on `sudo docker` for normal use (it can confuse file permissions on images/volumes).

**Install k3s** (once per machine, or follow [k3s install](https://docs.k3s.io/installation))

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes
```

**Clone the repo** (if you have not already — see **Clone the repo** above), then **deploy**:

`./scripts/deploy-k3s.sh k3s` uses **`sudo k3s kubectl`** by default so it works when `/etc/rancher/k3s/k3s.yaml` is root-only. To use plain `kubectl` (no sudo), copy the kubeconfig to your user (see below) and run `KUBECTL_CMD=kubectl ./scripts/deploy-k3s.sh k3s`.

```bash
cd Real_Time_Stocks_Data_Explorer   # or your clone path

./scripts/deploy-k3s.sh k3s
sudo k3s kubectl -n stock-ticker get pods
sudo k3s kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open the UI at `http://127.0.0.1:3000`.

Before **Remove the app**, back up Postgres the same way as in **Backup Postgres before you delete the namespace** (macOS section above) if you need the data. Use `./scripts/k8s-backup-postgres.sh` when plain `kubectl` works, or adapt the `kubectl exec` example to `sudo k3s kubectl exec`.

**Remove the app** (same as macOS: deletes manifests and **Postgres PVC** — data is removed)

```bash
./scripts/k8s-teardown.sh k3s
# same effect:
sudo k3s kubectl delete -k k8s
```

That drops **deployments, services, secrets, ConfigMaps, and the Postgres PVC** so you can redeploy from a clean slate. It does **not** remove Docker images you built (`stock-ticker-backend:local`, etc.) or uninstall k3s.

**How to tell it finished:** `kubectl delete -k` waits for the **namespace** to disappear. After the `deleted` lines, the command may sit with **no output** for a little while — that is normal. When it is done, your **shell prompt returns**. Confirm:

```bash
sudo k3s kubectl get ns | grep stock-ticker || echo "gone — teardown complete"
```

If there is **no line** for `stock-ticker`, you are clean. If the namespace stays **`Terminating`** for a long time, open another terminal and run `sudo k3s kubectl get pods -n stock-ticker`, `sudo k3s kubectl describe namespace stock-ticker`, and see **Stuck pods after delete** below.

**Uninstall k3s** (optional; removes the cluster and node)

```bash
sudo /usr/local/bin/k3s-uninstall.sh
```

**Use `kubectl` as your user** (optional; avoids prefixing every command with `sudo k3s kubectl`):

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
kubectl config get-contexts
kubectl config use-context default   # name may differ; pick the k3s context from the list above
kubectl get nodes
```

### What the script actually does

`./scripts/deploy-k3s.sh k3d`

- builds `stock-ticker-backend:local`
- builds `stock-ticker-frontend:local`
- imports those images into the existing `k3d` cluster
- runs `kubectl apply -k k8s`
- waits for Redis, Postgres, backend, and frontend to become ready

`./scripts/deploy-k3s.sh k3s`

- builds the same two images locally
- saves them as tar archives
- imports them into native `k3s` containerd with `k3s ctr`
- applies the same manifests from `k8s/`
- waits for Redis, Postgres, backend, and frontend to become ready (same as `k3d` mode)

### What gets deployed

- `postgres:16-alpine` with a **PersistentVolumeClaim** (default **2Gi**) so database files survive pod restarts and host reboots; data lives on the cluster’s storage provisioner (e.g. k3d local-path).
- `redis:7-alpine` for quote/news cache
- `stock-ticker-backend:local`
- `stock-ticker-frontend:local`

The backend connects to the in-cluster Service **`stock-ticker-postgres`** (`DATABASE_URL` in `k8s/backend-database-secret.yaml`). Removing the **PVC** or the whole cluster **without** a backup can still delete data—treat the PVC as your durable store while the cluster exists.

The backend deployment runs `alembic upgrade head` in an init container before the API starts. The API then seeds the default user (`default-user`) automatically when it can reach the database.

### Kubernetes: use Postgres on your Mac instead (optional)

To point the backend at **host Postgres** (e.g. reuse an existing DB on the machine): edit `k8s/backend-database-secret.yaml` so `DATABASE_URL` uses **`host.lima.internal`** or **`host.docker.internal`** (see `./scripts/k8s-test-host-postgres.sh`), then remove **`postgres.yaml`** from `k8s/kustomization.yaml` so you do not run two Postgres instances. Apply and restart the backend.

### Manual commands instead of the helper script

macOS with `k3d`:

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .
k3d image import stock-ticker-backend:local stock-ticker-frontend:local -c stock-ticker
kubectl apply -k k8s
kubectl -n stock-ticker rollout status deployment/stock-ticker-redis
kubectl -n stock-ticker rollout status deployment/stock-ticker-postgres
kubectl -n stock-ticker rollout status deployment/stock-ticker-backend
kubectl -n stock-ticker rollout status deployment/stock-ticker-frontend
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Ubuntu with native `k3s`:

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .
docker save -o /tmp/stock-ticker-backend-local.tar stock-ticker-backend:local
docker save -o /tmp/stock-ticker-frontend-local.tar stock-ticker-frontend:local
sudo k3s ctr -n k8s.io images import /tmp/stock-ticker-backend-local.tar
sudo k3s ctr -n k8s.io images import /tmp/stock-ticker-frontend-local.tar
sudo k3s kubectl apply -k k8s
sudo k3s kubectl -n stock-ticker rollout status deployment/stock-ticker-redis
sudo k3s kubectl -n stock-ticker rollout status deployment/stock-ticker-postgres
sudo k3s kubectl -n stock-ticker rollout status deployment/stock-ticker-backend
sudo k3s kubectl -n stock-ticker rollout status deployment/stock-ticker-frontend
sudo k3s kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

### Troubleshooting

k3s on Ubuntu — `kubectl` fails with **permission denied** on `/etc/rancher/k3s/k3s.yaml`:

The server kubeconfig is often **root-readable only**. Use **`sudo k3s kubectl`** for all commands, **or** copy it once so plain `kubectl` works (same as **Use `kubectl` as your user** under Ubuntu Linux (k3s) above):

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
kubectl get nodes
```

If `KUBECONFIG` points at `/etc/rancher/k3s/k3s.yaml` in your shell, unset it or rely on `~/.kube/config` after copying.

Namespace does not exist:

```text
Error from server (NotFound): namespaces "stock-ticker" not found
```

This usually means you ran `port-forward` before running the deploy script, or `kubectl` is pointed at the wrong cluster. Check:

```bash
kubectl config current-context
kubectl get namespaces
```

k3d on macOS (`No nodes found for given cluster`, or `connection refused` to `kubernetes.docker.internal`):

- Create the cluster once if it does not exist: `k3d cluster create stock-ticker`
- Use the k3d context (not Docker Desktop Kubernetes): `kubectl config use-context k3d-stock-ticker`
- List clusters: `k3d cluster list`

k3s on Ubuntu — Postgres rollout times out (`stock-ticker-postgres` never Ready):

- Check the PVC is **Bound**, not **Pending**: `sudo k3s kubectl get pvc -n stock-ticker`. The manifest uses StorageClass **`local-path`** (k3s default). If your cluster has no `local-path`, run `sudo k3s kubectl get storageclass` and set `storageClassName` in `k8s/postgres.yaml` to match, or install the [local-path-provisioner](https://github.com/rancher/local-path-provisioner).
- **ext4 + `lost+found`**: many provisioned volumes are not empty at the mount root, so Postgres must use **`PGDATA` in a subdirectory** (`k8s/postgres.yaml` sets `/var/lib/postgresql/data/pgdata`). If you had a failed init from an older manifest, delete the Postgres deployment PVC and re-apply so data initializes on a clean volume.
- **ReadWriteOnce + rolling updates**: a single-replica Deployment with default `RollingUpdate` can briefly run two pods; the second cannot attach the same volume, so rollout stalls with **old replicas pending termination**. The manifest uses **`strategy: Recreate`** for Postgres so the old pod releases the PVC before the new one starts.
- First-time image pull for `postgres:16-alpine` needs outbound network from the node. `kubectl describe pod -n stock-ticker -l app=stock-ticker-postgres` shows `ImagePullBackOff` vs volume errors.
- **`0 of 1 updated replicas are available`** / progress deadline: inspect the pod (`sudo k3s kubectl describe pod -n stock-ticker -l app=stock-ticker-postgres`). The manifest uses a **`startupProbe`** so slow `initdb` is not killed by liveness; if logs show repeated restarts or data errors, delete the PVC and re-apply (wipes DB).

Switching from in-cluster Postgres to host Postgres (or vice versa): adjust `k8s/kustomization.yaml` and `backend-database-secret.yaml`, then apply. To **remove** cluster Postgres and its data volume:

```bash
kubectl -n stock-ticker delete deployment,svc,secret stock-ticker-postgres
kubectl -n stock-ticker delete pvc stock-ticker-postgres-data
```

Backend rollout times out or stuck in `Init:CrashLoopBackOff`:

- The init container runs `alembic` against `DATABASE_URL`. If you use **host Postgres**, pods must reach your Mac (see `./scripts/k8s-test-host-postgres.sh`). If you use **in-cluster Postgres**, wait until `stock-ticker-postgres` is Ready. Check:

```bash
kubectl -n stock-ticker logs deployment/stock-ticker-backend -c migrate-db
kubectl -n stock-ticker logs deployment/stock-ticker-backend -c backend
```

- **Host Postgres only — wrong hostname:** run `./scripts/k8s-test-host-postgres.sh`. Edit `k8s/backend-database-secret.yaml`, re-apply, then restart the backend.
- **In-cluster Postgres:** ensure `stock-ticker-postgres` is Running and `DATABASE_URL` uses service `stock-ticker-postgres`.
- Older stuck: `kubectl -n stock-ticker delete pod -l app=stock-ticker-backend` after fixing config.
- If you rebuilt a local image and the pod did not refresh because the tag is still `:local`, restart the deployment:

```bash
kubectl -n stock-ticker rollout restart deployment/stock-ticker-backend
kubectl -n stock-ticker rollout restart deployment/stock-ticker-frontend
```

Old `buildx` plugin on macOS:

```text
client version 1.42 is too old
```

Make sure Docker is using a current Homebrew `buildx` plugin:

```bash
brew install docker-buildx
mkdir -p ~/.docker
printf '%s\n' '{' '  "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]' '}' > ~/.docker/config.json
docker buildx version
docker buildx ls
```

### Stuck pods or namespace `Terminating`

**Namespace stuck `Terminating` for a long time** (even when `get pods -n stock-ticker` is empty): something still has a **finalizer** or a hidden resource. List what is left, then only if needed clear the namespace finalizer:

```bash
./scripts/k8s-unstick-namespace.sh stock-ticker
# k3s:
KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-unstick-namespace.sh stock-ticker
```

If the script shows **no leftover objects** and the namespace still will not finish, install **`jq`** and run the **last-resort** finalize (safe when the namespace is already empty):

```bash
sudo apt install -y jq   # if needed
KUBECTL_CMD="sudo k3s kubectl" ./scripts/k8s-unstick-namespace.sh stock-ticker --force
```

Manual equivalent (same as `--force`): `kubectl get namespace stock-ticker -o json | jq '.spec.finalizers = []' | kubectl replace --raw /api/v1/namespaces/stock-ticker/finalize -f -`

**Pods** hang while terminating:

```bash
kubectl -n stock-ticker delete pod --all --grace-period=0 --force
```

### Stop k3s without uninstalling (Ubuntu)

```bash
sudo systemctl stop k3s
```

### API URL shape (same-origin)

**Preferred:** serve the UI at `/` and the API at `/api` on **one origin** (no cross-origin
fetch). The frontend calls relative URLs like `/api/...`; in dev, Next.js rewrites proxy to
FastAPI (`BACKEND_PROXY_URL`, default `http://127.0.0.1:8000`). Copy `frontend/.env.example`
for overrides.

To call the API **directly** (no proxy), set `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`
and add your UI origin to backend `CORS_ORIGINS` (see `backend/.env.example`).

### Backend environment

- `CORS_ORIGINS` — comma-separated browser origins when UI and API are on different hosts.
- `API_PREFIX` — route prefix (default `/api`; must match `NEXT_PUBLIC_API_PREFIX` on the frontend).

### Key statistics / fundamentals (optional)

Price, volume, and ranges come from Yahoo chart (US) or local history (TW). **Market cap, beta, P/E, EPS, dividend dates,** etc. are **not** provided by those endpoints.

To fill those fields in **Key statistics**, set a [Finnhub](https://finnhub.io/) API key on the backend:

```bash
export FINNHUB_API_KEY="your_token_here"
```

Then restart the API. The backend merges Finnhub `profile2`, `metric`, `calendar/earnings`, and `dividend` data when the key is present.

### Where calculations run

- **Backend:** Price/volume series math for charts (min/max, normalized SVG points, axis tick indices, sparkline trend), key-statistics range marker percentages (0–100 within day / 52-week bands), and all quote/history/key-stats aggregation.
- **Frontend:** Renders API payloads, handles pointer hit-testing for the chart (which candle index is under the cursor), locale/date **display** formatting (`Intl`, currency labels), and i18n strings—not business formulas.
