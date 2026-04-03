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

## Run In k3s

This repo now supports a no-registry local Kubernetes flow:

1. build the backend and frontend images locally
2. import those local images into `k3s` / `k3d`
3. deploy Postgres, Redis, backend, and frontend from `k8s/`
4. expose the frontend locally with `kubectl port-forward`

The helper script is `scripts/deploy-k3s.sh`.

### Prerequisites

- A working Docker daemon on the machine that builds the images
- `kubectl`
- macOS: `k3d` (native `k3s` is Linux-only, so macOS uses `k3d`, which runs k3s inside Docker)
- Ubuntu Linux: native `k3s` or `kubectl` access to an existing k3s cluster

### Exact Commands: macOS

Install the local tooling once:

```bash
brew install colima docker kubectl k3d
colima start --cpu 4 --memory 8 --disk 30
```

Clone, create the cluster, build/import/deploy the app, and expose it locally:

```bash
git clone <your-github-url> Real_Time_Stocks_Data_Explorer
cd Real_Time_Stocks_Data_Explorer

k3d cluster create stock-ticker
./scripts/deploy-k3s.sh k3d
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open the app at `http://127.0.0.1:3000`.

If you want the exact manual commands instead of the helper script:

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .
k3d image import stock-ticker-backend:local stock-ticker-frontend:local -c stock-ticker
kubectl apply -k k8s
kubectl -n stock-ticker rollout status deployment/stock-ticker-postgres
kubectl -n stock-ticker rollout status deployment/stock-ticker-redis
kubectl -n stock-ticker rollout status deployment/stock-ticker-backend
kubectl -n stock-ticker rollout status deployment/stock-ticker-frontend
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

### Exact Commands: Ubuntu Linux

Install native `k3s` once:

```bash
curl -sfL https://get.k3s.io | sh -

mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
```

Clone, build/import/deploy the app, and expose it locally:

```bash
git clone <your-github-url> Real_Time_Stocks_Data_Explorer
cd Real_Time_Stocks_Data_Explorer

./scripts/deploy-k3s.sh k3s
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

Open the app at `http://127.0.0.1:3000`.

If you want the exact manual commands instead of the helper script:

```bash
docker build -t stock-ticker-backend:local -f backend/Dockerfile .
docker build -t stock-ticker-frontend:local -f frontend/Dockerfile .

docker save -o /tmp/stock-ticker-backend-local.tar stock-ticker-backend:local
docker save -o /tmp/stock-ticker-frontend-local.tar stock-ticker-frontend:local

sudo k3s ctr -n k8s.io images import /tmp/stock-ticker-backend-local.tar
sudo k3s ctr -n k8s.io images import /tmp/stock-ticker-frontend-local.tar

kubectl apply -k k8s
kubectl -n stock-ticker rollout status deployment/stock-ticker-postgres
kubectl -n stock-ticker rollout status deployment/stock-ticker-redis
kubectl -n stock-ticker rollout status deployment/stock-ticker-backend
kubectl -n stock-ticker rollout status deployment/stock-ticker-frontend
kubectl -n stock-ticker port-forward svc/stock-ticker-frontend 3000:3000
```

### What Gets Deployed

- `postgres:16-alpine` with a local PVC for app data
- `redis:7-alpine` for quote/news cache
- `stock-ticker-backend:local`
- `stock-ticker-frontend:local`

The backend deployment runs `alembic upgrade head` in an init container before the API starts. The API startup then seeds the default user (`default-user`) automatically.

### Cleanup

Remove the app resources:

```bash
kubectl delete -k k8s
```

Delete the macOS k3d cluster:

```bash
k3d cluster delete stock-ticker
```

Uninstall native Ubuntu k3s completely:

```bash
sudo /usr/local/bin/k3s-uninstall.sh
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
