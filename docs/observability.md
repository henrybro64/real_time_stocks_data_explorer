# Stock Ticker observability (Prometheus + Grafana + kube-state-metrics)

Optional Kubernetes **workload health** visibility for local k3s/k3d. The core app
deploy does not require it; k3s smoke is unchanged.

## Scope

| Layer | This overlay (`k8s/observability`) | Future app metrics |
| --- | --- | --- |
| Install | Prometheus, Grafana, kube-state-metrics | Reuse this stack |
| Dashboards | **Kubernetes Workload Health** | Backend `/metrics` dashboards |

Do **not** stand up a second Prometheus or Grafana for application metrics — add scrape
jobs and dashboard JSON under the same provisioning path. This overlay covers **#136**
(Kubernetes workload health); backend `/metrics` dashboards belong in **#28** and should
reuse this stack.

## What is collected

| Source | Role |
|--------|------|
| `kube-state-metrics` | Pod phase/readiness, container restarts, waiting/termination reasons, Deployment replica counts, configured requests/limits |
| kubelet / cAdvisor (via Kubernetes API node proxy) | Container CPU usage and memory working set |
| Node metrics (via API proxy) | Node readiness (lightweight) |

Prometheus scrapes:

- `kube-state-metrics:8080`
- Kubernetes API `/api/v1/nodes/<node>/proxy/metrics`
- Kubernetes API `/api/v1/nodes/<node>/proxy/metrics/cadvisor` (filtered to `namespace=stock-ticker`)

### Probe attempt limitation

kube-state-metrics and cAdvisor do **not** reliably expose individual
readiness/liveness probe *attempt* failures. The Workload Health dashboard
therefore uses:

- `kube_pod_status_ready` (ready vs not ready)
- `kube_pod_container_status_restarts_total` (restart pressure, including liveness-triggered restarts)
- waiting/last-terminated reasons (`CrashLoopBackOff`, `OOMKilled`, …)
- Deployment unavailable replicas

Do not interpret those panels as per-probe attempt counters.

## Deploy

Prerequisites: the app namespace and workloads from `./scripts/deploy-k3s.sh`.

```bash
# After the main stack is up:
kubectl apply -k k8s/observability

# Wait for rollouts
kubectl -n stock-ticker rollout status deploy/kube-state-metrics
kubectl -n stock-ticker rollout status deploy/prometheus
kubectl -n stock-ticker rollout status deploy/grafana
```

Or via the deploy helper:

```bash
OBSERVABILITY=1 ./scripts/deploy-k3s.sh k3d
# or
OBSERVABILITY=1 ./scripts/deploy-k3s.sh k3s
```

## Access Grafana

In-cluster:

```bash
kubectl -n stock-ticker port-forward svc/grafana 3000:3000
```

Open http://127.0.0.1:3000

- Anonymous viewer access is enabled for local convenience
- Admin credentials default to `admin` / `admin` (change before any shared use)
- NodePort alternative: `http://<node-ip>:30300`

Dashboard: **Kubernetes Workload Health** (folder **Stock Ticker**).

Prometheus UI (optional):

```bash
kubectl -n stock-ticker port-forward svc/prometheus 9090:9090
```

## Repository layout

```text
ops/prometheus/prometheus.yml
ops/grafana/provisioning/...
ops/grafana/dashboards/kubernetes-workload-health.json

k8s/observability/                 # applied with kubectl apply -k
  kube-state-metrics.yaml
  prometheus-rbac.yaml
  prometheus.yaml
  grafana.yaml
  prometheus.yml                   # mounted ConfigMap source (keep in sync with ops/)
  grafana/                         # provisioning + dashboard files for ConfigMaps
```

`ops/` is the documented source of truth for humans. `k8s/observability/` holds
the copies kustomize mounts. Unit tests assert required scrape jobs and
dashboard queries exist in both places.

## Resource assumptions (small local cluster)

Modest requests/limits are set on app and observability workloads so Grafana can
show usage vs limits without starving a laptop cluster:

| Workload | Requests | Limits |
|----------|----------|--------|
| backend | 50m / 256Mi | 1 CPU / 1Gi |
| frontend | 25m / 128Mi | 500m / 512Mi |
| postgres | 50m / 256Mi | 500m / 1Gi |
| redis | 25m / 64Mi | 250m / 256Mi |
| history-capture CronJob | 10m / 32Mi | 100m / 64Mi |
| prometheus | 50m / 256Mi | 500m / 768Mi |
| grafana | 50m / 128Mi | 300m / 512Mi |
| kube-state-metrics | 20m / 64Mi | 200m / 256Mi |

These are starting points for personal k3s/k3d, not production sizing.

Background quote refresher runs inside the backend Deployment, so it is
covered by backend pod panels. The history-capture CronJob is covered by the
CronJob/Job panels.

## Troubleshooting flows

### Pod not ready

1. Open **Not-ready pods** and **Pod readiness over time**.
2. Identify the pod name, then check waiting reasons table for
   `CrashLoopBackOff`, `ImagePullBackOff`, or `CreateContainerConfigError`.
3. Correlate with `kubectl -n stock-ticker describe pod <pod>`.

### CrashLoopBackOff / repeated restarts

1. Confirm **Container restart rate** rising for the pod/container.
2. Check **Waiting reasons** for `CrashLoopBackOff`.
3. Inspect logs: `kubectl -n stock-ticker logs deploy/<name> --previous`.

### OOMKilled

1. **Last terminated reasons** should show `OOMKilled`.
2. Compare **Memory working set** vs **Memory usage vs limits**.
3. Raise the container memory limit only if the increase is justified.

### ImagePullBackOff / ErrImagePull

1. Waiting reasons table should list `ImagePullBackOff` or `ErrImagePull`.
2. For local images, confirm `imagePullPolicy: Never` and that
   `./scripts/deploy-k3s.sh` imported the image into the cluster.

### Deployment has unavailable replicas

1. **Unavailable deployment replicas** and **Desired vs available replicas**
   identify the Deployment.
2. Check whether pods are Pending (scheduling/PVC) or failing readiness.
3. Open **Not scheduled (PodScheduled=false)** for scheduling failures
   (node pressure, PVC, affinity) vs CrashLoop-style failures.

### CronJob / history-capture Job failures

1. **Failed Jobs (count)** and **CronJob / Job failures** cover
   `stock-ticker-history-capture` and any other batch Jobs in the namespace.
2. Confirm the CronJob schedule and recent Job pods:
   `kubectl -n stock-ticker get cronjob,job,pods | grep history`.
3. Inspect Job logs for the curl capture container if failures rise.

## Manual verification (temporary unhealthy state)

Do **not** leave a permanently failing workload in the normal deploy.

Example:

```bash
# Scale backend to an unavailable state briefly (optional), or patch a throwaway
# test Deployment with an invalid image in a disposable name, then delete it.
kubectl -n stock-ticker create deployment obs-verify-bad \
  --image=stock-ticker-backend:does-not-exist
kubectl -n stock-ticker set image deploy/obs-verify-bad \
  '*=stock-ticker-backend:does-not-exist'

# Confirm Grafana shows ImagePullBackOff / not-ready / unavailable replicas
# for obs-verify-bad within a scrape interval (~30s).

kubectl -n stock-ticker delete deploy obs-verify-bad
# Confirm the dashboard returns to healthy for core workloads.
```

## Automated checks

```bash
# From repo root
python -m pytest backend/tests/unit/observability -q
kubectl kustomize k8s/observability >/dev/null
```

These validate manifests/RBAC presence, Prometheus scrape jobs, and dashboard
query coverage without requiring a live cluster.

## Remove

```bash
kubectl delete -k k8s/observability
```

This removes Deployments/Services/ConfigMaps in `stock-ticker` and the
cluster-scoped Prometheus / kube-state-metrics RBAC objects created by this
overlay. The core app under `kubectl delete -k k8s` does **not** remove the
observability stack.
