# Taiwan research fundamentals (#335–#337)

Fundamental-first evidence for the configured research universe. Stock Ticker
collects, verifies, and exports; it does **not** decide Notion report
inclusion, rankings, or trade instructions.

## Status

| Issue | Scope | Code status |
| --- | --- | --- |
| #335 | Source audit + **resumable** full-universe first-pass jobs + analyst export | Implemented (Alembic `20261111_0023` + `0024`); owner-scoped snapshot IDs + durable per-symbol retry |
| #336 | Editable per-sector membership, OCC, candidate sets pinned to digest | Implemented with **Postgres fail-closed** + UI sector editor + candidate-set version tracking |
| #337 | Relative price-basis with coverage/staleness/corporate-action flags | Implemented (historical PE range still unavailable) |

See also `docs/ai-handoff-335-336-337.md` for the latest implementation handoff.

No CronJob enablement, Notion SDK, new microservice, or production deploy in this slice.

## Modules

| Path | Role |
| --- | --- |
| `backend/app/services/tw_research/contract_fields.json` | Versioned field/source contract (+ status/unit/required) |
| `backend/app/services/tw_research/feasibility_matrix.json` | Machine-readable feasibility audit |
| `backend/app/services/tw_research/contract.py` | Contract + matrix loader (`CONTRACT_VERSION=2026-11-11.1`) |
| `backend/app/services/tw_research/first_pass.py` | Bounded first-pass collector (dedupe-before-fetch) |
| `backend/app/services/tw_research/price_basis.py` | Relative price-basis classifier (#337) |
| `backend/app/services/tw_research/fundamentals_watchlist.py` | Version-checked membership / compute subset |
| `docs/tw-research-fundamentals.md` | This audit + runbook |

## What already works

- Catalog identity (`stockId`, exchange, `name_zh_tw` / `name`)
- Stored completed daily OHLCV (prefer `peek_daily_history`)
- Official daily PE (TWSE BWIBBU_d / TPEx peQryDate via `key_stats_service`)
- Institutional 5d, margin balance/delta, TDCC ≥400-lot concentration
- SMA20/60/120/240 + 52-week position helpers (shared indicators)
- Relative price-basis categories with provenance and value-trap **flag** (evidence only)

## Partial / opt-in

- TTM EPS often null on the official PE path; PEG stays unavailable without a verified growth denominator
- Yahoo quarterly margins via `tw_profitability_service` — **opt-in only** (`includeNonofficialMargins`); default first-pass marks `not_fetched`

## Explicit gaps (do not fabricate)

Missing values stay `unavailable` / `not_fetched` with provenance reasons. They are never coerced to zero or treated as favorable.

| Metric | Mandatory for #335–#337? | Collection status | Dependency / follow-up |
| --- | --- | --- | --- |
| Real monthly revenue YoY/MoM | Desired for ChatGPT fundamental triage; **not blocking** durable workflow APIs | **Unavailable** — `tw_insights` revenue is synthetic and forbidden | Real MOPS/monthly-revenue adapter (proposed follow-up; do not invent issue #) |
| Operating cash flow | Same as above | **Unavailable** — no adapter | Reliable cash-flow source + adapter |
| Debt-related metrics | Same as above | **Unavailable** — no adapter | Balance-sheet / debt adapter |
| Verified historical valuation comparisons | #337 marks historical PE/PB range as unavailable | **Unavailable** — same-definition history not verified | Verified PE/PB history source |
| Official PB / dividend yield | Partial toward fundamentals | **Unavailable** or unmapped on BWIBBU | Field mapping verification |
| Second-pass MACD/KD | #336 technical path | **Available** | Candidate scans: versioned **monthly-first** (`CALCULATION_VERSION_MONTHLY_FIRST`, Stage A monthly → Stage B daily with `only_on_monthly_pass`). Legacy/technical-first scans keep daily-first (`CALCULATION_VERSION`). |

Do **not** declare mandatory acceptance complete for adapters that remain absent. Deferred metrics stay explicitly tracked here and in the AI handoff.

## Provenance rules

Runtime first-pass fields use `ProvenanceField`:

`field`, `status` (`ok`|`partial`|`unavailable`), `source`, `source_as_of`, `captured_at`, `freshness_class`, `reason`, `value`

Completeness summaries always publish denominators:

- `fieldsAttempted` / `fieldsOk` / `fieldsUnavailable`
- `fundamentalFieldsAttempted` / `fundamentalFieldsOk`
- `fundamentalComplete` stays `false` while core adapters are missing

A finished first-pass run ≠ fundamental completeness.

## Relative price basis (#337)

Categories (configurable thresholds, defaults 0.30 / 0.70):

- `low_relative_price_basis`
- `mid_relative_price_basis`
- `high_relative_price_basis`
- `insufficient_data`

Low relative price **does not** mean cheap valuation. Historical valuation context remains `unavailable` until a same-definition source is verified. Multi-year (3–5y) location stays `not_calculated` on first-pass to avoid unbounded backfill.

## APIs

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/tw-research/contract` | Includes feasibility matrix + first-pass versions |
| GET | `/api/tw-research/feasibility-matrix` | Read-only audit |
| POST | `/api/tw-research/first-pass/batch` | Ephemeral/bounded helper; prefer jobs for full universe |
| GET | `/api/tw-research/first-pass/jobs` | Owner-scoped recent jobs (UI restore after refresh) |
| POST | `/api/tw-research/first-pass/jobs` | Start resumable full-universe job (frozen membership) |
| POST | `/api/tw-research/first-pass/jobs/{id}/batches` | Lease + process next bounded batch; field-level retries |
| GET | `/api/tw-research/first-pass/jobs/{id}/progress` | pending/running/completed/partial/failed/deferred |
| GET | `/api/tw-research/first-pass/jobs/{id}/symbols` | Paginated evidence; `q` / status / completeness filters |
| GET | `/api/tw-research/first-pass/snapshots/{id}` (+ `/manifest`, `/chunk`) | Analyst export |
| GET/PUT | `/api/tw-research/membership` | Per-sector add/remove + OCC; **503** if DB unavailable |
| POST | `/api/tw-research/membership/preview` | Preview without persist |
| POST/GET | `/api/tw-research/candidate-sets` (+ `/latest`) | Analyst/manual subset pinned to first-pass digest |
| POST | `/api/tw-research/candidate-sets/technical-scan` | Stage A **monthly** → Stage B **daily** MACD/KD (`monthly_first` version); single catalog resolve; atomic provenance |
| POST | `/api/tw-research/relative-price-basis` | Stored bars only |

## Provider cost notes (honest)

| Provider | Batch | Worst-case for N symbols | Notes |
| --- | --- | --- | --- |
| Catalog identity | local | 0 upstream | Prefer stored catalog |
| Daily OHLCV | `peek_daily_history` | 0 if warm | No 3–5y first-pass backfill |
| Official PE/PB/yield | TWSE BWIBBU / TPEx daily | ~1 board snapshot / day (shared cache) | Per-board LRU cache |
| Institutional / margin / TDCC | existing services | ≤N calls; reuse enrichment caches | Per-symbol isolation |
| Yahoo margins | opt-in only | 0 by default | `includeNonofficialMargins` |
| Monthly revenue / OCF / debt | none | n/a | Explicit unavailable |

Live Ubuntu/k3s cold/warm timings remain `ubuntu_measurement_pending`.

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_fundamentals_335_337.py \
  tests/unit/services/tw_research/test_universe_and_scan.py \
  tests/unit/services/tw_research/test_enrichment_and_export.py -q

# PostgreSQL provenance round-trip (disposable DB only):
ALLOW_DESTRUCTIVE_DB_TESTS=1 \
DATABASE_URL=postgresql+psycopg://henrybro@127.0.0.1:5432/stocks_test \
  .venv313/bin/python -m pytest \
  tests/integration/services/test_tw_research_candidate_provenance_336.py -q
```

## Outstanding

1. Real MOPS/monthly-revenue adapter (never synthetic insights).
2. Operating cash flow / debt adapters.
3. Optional explainable `fundamental_*` hint outcomes (analyst still owns selection).
4. TPEx official PB/yield field mapping verification against live payloads.
5. Live Ubuntu/k3s performance measurements remain `ubuntu_measurement_pending`.
6. Apply Alembic `20261111_0023` + `20261111_0024` before using durable APIs in a real DB.
