# TW chip publication-ready times probe (#347)

Measure, do not guess. Code defaults are **16:30** (institutional) and **21:30**
(margin), Asia/Taipei. Fill the results table only after running the probe on
real trading days. Do **not** invent numbers.

## What it does

`backend/scripts/probe_chip_publication_times.py` polls the same official
TWSE/TPEx endpoints the collector uses (T86-equivalent institutional + margin
balance), one request per dataset per interval, until each dataset is available
or the 60-request/dataset cap is hit. It appends one JSON line to
`backend/scripts/out/chip_publication_times.jsonl` (gitignored).

It does **not** write to the DB and does **not** touch first-pass/scan code.

## How to run (3–5 trading days)

Deployment name from `k8s/backend.yaml`: **`stock-ticker-backend`**.

```bash
# On a TW trading day afternoon (Asia/Taipei), inside the backend pod:
sudo k3s kubectl -n stock-ticker exec deploy/stock-ticker-backend -- \
  python -m scripts.probe_chip_publication_times \
    --date YYYY-MM-DD \
    --start 14:30 \
    --end 23:00 \
    --interval-minutes 10 \
    --stock TW:2330 \
    --market TWSE
```

Repeat on **3–5 distinct trading days**. Optionally probe TPEx with
`--market TPEx --stock TW:6488` (or another TPEx symbol you care about).

Local (venv, from `backend/`), same args — still read-only HTTP to official hosts:

```bash
cd backend && python -m scripts.probe_chip_publication_times --date YYYY-MM-DD
```

Non-trading days exit non-zero (calendar check via `market_clock.is_trading_day`).

## Results

| date | market | institutional first-available | margin first-available | notes |
|------|--------|-------------------------------|------------------------|-------|
| _not yet measured_ | | | | |

## Decision rule (ConfigMap / env only)

After measuring:

1. Take the **latest** observed first-available time across the sampled days for
   each dataset.
2. Add **30 minutes**, then round **up** to the next **15 minutes**.
3. Set `TW_CHIP_INSTITUTIONAL_READY` / `TW_CHIP_MARGIN_READY` (`HH:MM`) on the
   backend ConfigMap/env in `k8s/backend.yaml` (`stock-ticker-backend-env`).
4. **Do not change the code defaults** unless the observed times differ from
   16:30 / 21:30 by **more than 30 minutes**.

Example: latest institutional first-available 15:42 → +30m = 16:12 → round up
to **16:15**.
