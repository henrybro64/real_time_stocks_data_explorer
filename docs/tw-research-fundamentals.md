# Taiwan research fundamentals (#335–#337)

Fundamental-first evidence for the configured research universe. Stock Ticker
collects, verifies, and exports; it does **not** decide Notion report
inclusion, rankings, or trade instructions.

## Status

| Issue | Scope | Code status |
| --- | --- | --- |
| #335 | Source audit, field contract, provenance, feasibility matrix | Implemented in-repo (+ durable first-pass snapshot) |
| #336 | First-pass collection + membership/compute-subset + candidate set | Implemented with **Postgres fail-closed** membership / candidate sets (Alembic `20261111_0023`) |
| #337 | Relative price-basis / valuation-position evidence | Implemented (historical PE range still unavailable) |

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

- Real monthly revenue YoY/MoM (`tw_insights` revenue is **synthetic** and forbidden)
- Official PB / official dividend yield adapters (BWIBBU fields not wired)
- Operating cash flow, debt metrics, ROE/ROA
- Same-definition historical PE/PB range
- Second-pass daily/monthly MACD/KD (existing scan path; not invoked by first-pass)

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
| POST | `/api/tw-research/first-pass/batch` | Dedupe **then** `batchLimit` ≤ 25; `persist=true` (default) writes immutable snapshot |
| GET/PUT | `/api/tw-research/membership` | Postgres optimistic `expectedVersion`; **503** if DB unavailable |
| POST/GET | `/api/tw-research/candidate-sets` (+ `/latest`) | Analyst/manual subset pinned to first-pass digest (not Notion list) |
| POST | `/api/tw-research/relative-price-basis` | Stored bars only |

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_fundamentals_335_337.py \
  tests/unit/services/tw_research/test_universe_and_scan.py \
  tests/unit/services/tw_research/test_enrichment_and_export.py -q
```

## Outstanding

1. Wire official PB / yield from verified TWSE/TPEx payloads when field mapping is confirmed.
2. Real MOPS/monthly-revenue adapter (never synthetic insights).
3. Optional explainable `fundamental_*` hint outcomes (analyst still owns selection).
4. First-pass completeness columns in the research UI (name column is present).
5. Live Ubuntu/k3s performance measurements remain `ubuntu_measurement_pending`.
6. Apply Alembic `20261111_0023` before using durable membership/first-pass/candidate APIs in a real DB.
