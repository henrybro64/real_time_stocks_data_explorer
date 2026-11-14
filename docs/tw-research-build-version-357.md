# Frontend build version markers (#357)

## Goal

Make the served frontend build identity verifiable (git SHA + build time) without relying on runtime ConfigMap alone.

## Bake into the image

`NEXT_PUBLIC_*` is inlined at **`next build`**. Pass build-args when building the frontend image:

```bash
docker build \
  --build-arg BUILD_SHA="$(git rev-parse --short HEAD)" \
  --build-arg BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -f frontend/Dockerfile \
  -t stock-ticker-frontend:local \
  .
```

`frontend/Dockerfile` sets `ENV NEXT_PUBLIC_BUILD_SHA` / `NEXT_PUBLIC_BUILD_TIME` **before** `npm run build`.

`scripts/deploy-k3s.sh` passes `--build-arg BUILD_SHA` / `BUILD_TIME` (from git HEAD, or override via `BUILD_SHA` / `BUILD_TIME` env).

## Verify (read-only)

- UI: open **進階資訊** on TW research → `data-testid="tw-research-build-version"`.
- HTTP: `GET /api/version` → `{ "sha": "…", "buildTime": "…" }`.
- Post-deploy: `scripts/verify-k3s-post-deploy.sh` compares served SHA to `EXPECTED_BUILD_SHA` (default: `git rev-parse --short HEAD`) and **fails** on `unknown` or mismatch.

If SHA is `unknown` or an old commit, the cluster is serving a stale `:local` (or otherwise outdated) image.

## Explicit non-goals / safety

- No production deploy, Pod restart, cache purge, or CronJob enablement without separate approval.
- Research first-pass CronJob stays suspended.
- ConfigMap injection of `NEXT_PUBLIC_*` at runtime does **not** change the baked build markers.
