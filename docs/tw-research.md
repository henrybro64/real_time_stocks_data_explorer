# Taiwan equity research (#310–#320)

Industry-agnostic Taiwan stock research on the **existing** monorepo. No new service, Notion SDK, schedule, or production deploy in this slice. Persistence reuses the **existing** Stock Ticker PostgreSQL (#318).

## Status of related closed work

Confirm and reuse — do not reimplement:

| Issue | Already in tree |
| --- | --- |
| #269 | Persisted daily bars (`daily_price_history_service`, `daily_price_bars`) |
| #271 | TW MIS quote batching (`TWProvider.get_quotes_batch`, 20 symbols/chunk) |
| #272 | Market-data routes require session when auth is on; read paths must not unbounded-fetch |
| #274 | Related history/chart optimizations |

## Product rules

- Any catalog **TWSE/TPEx** symbol is eligible (餐飲, 航空, finance, unclassified, …).
- Theme/sector tags are **optional metadata**. Missing a theme is not an error and not a rejection reason.
- The 14-sector / ~80-stock list is an **optional starter preset** (`UNIVERSE_PRESET_VERSION`). It is not a hard universe cap and must not appear as an authorization limit.
- `target_count` 10|20 is an **output target only**. It never relaxes pass/fail rules to fill slots.
- Scan batch size **10** is independent of MIS quote chunk size **20**.

## Modules

| Path | Role |
| --- | --- |
| `backend/app/services/tw_research/preset.py` | Optional TW sector themes + assignments (extends `stock_theme_service`) |
| `backend/app/services/tw_research/universe.py` | Universe selector: starter preset / theme keys / explicit IDs |
| `backend/app/services/tw_research/contract.py` | Versioned field/source map |
| `backend/app/services/tw_research/indicators.py` | Daily + monthly MACD(12,26,9) and KD(9,3,3) |
| `backend/app/services/tw_research/scan.py` | Bounded Stage A/B scan + enrichment batches + durable checkpoints |
| `backend/app/services/tw_research/coverage.py` | OHLCV + TW calendar coverage / readiness (#320) |
| `backend/app/services/tw_research/persistence.py` | Scan/symbol/snapshot helpers over Postgres (#318/#319) |
| `backend/app/repositories/tw_research_repository.py` | SQLAlchemy repository for scans / symbols / snapshots |
| `backend/app/services/tw_research/enrichment.py` | Day+month qualifier enrichment with per-field provenance (#313) |
| `backend/app/services/tw_research/export.py` | Manifest / chunked JSON / CSV / browser text / digests (#314/#319) |
| `backend/app/api/routes/tw_research.py` | Authenticated contract / universe / scan / enrich / export APIs |
| `frontend/src/app/research/tw/page.tsx` | Private research UI (`/research/tw`) |
| `docs/tw-research-pilot.md` | #315/#321 measurement worksheet (`ubuntu_measurement_pending` until node run) |
| `docs/tw-research-agent-handoff.md` | #322 browser-only Agent transfer runbook |
| `backend/app/services/tw_research/agent_handoff.py` | Manifest-first chunk plan + digest verify helpers (#322) |
| Alembic `20261109_0021` | `tw_research_scans`, `tw_research_symbol_results`, `tw_research_export_snapshots` |

## Indicator conventions (#311)

- Completed daily candles only (`moving_average.completed_daily_candles`).
- EMA seed = SMA of the first `period` values.
- OSC = DIF − Signal (not ×2).
- KD zero-range (H9 == L9): RSV = 50; K/D seed = 50; smoothing weight `1/3`.
- Monthly bars: calendar-month OHLCV; **current month excluded**.
- `daily_price_history_service` `MAX=800` is **not** silently treated as enough for long monthly MACD warm-up; insufficient history returns `unknown` / `insufficient_history`.
- Pass (day or month independently): `DIF > 0 AND OSC > 0 AND K > 50 AND D > 50`.

## Scan, checkpoints, and readiness (#312 / #318 / #320)

- Stage A: daily MACD/KD for each symbol in the batch.
- Stage B: monthly + SMA20/60/120/240 + 52-week position for **day-pass** symbols only.
- Upstream month budget is enforced **before** history backfill via `get_daily_history_within_budget`; counters track actual month fetches, not Stage B local math.
- Enrichment levels use **stored-only** `peek_daily_history` by default (no cold backfill). Stage B `week52_position` is reused when the store peek is empty.
- Indicator `now` is anchored to the scan `tradingDay` (after TW cash close). Overlapping batch POSTs use a per-scan lock **and** durable leased reservations for both scan and enrichment (`cursor` / `enrich_cursor` watermarks + lease fields) under `SELECT … FOR UPDATE`. Stale `upsert_scan` writes never regress progress; stale workers cannot wipe newer enrichment payloads.
- Checkpoint key: `(trading_day, universe_version, calculation_version, symbol, scan_id)`.
- Checkpoints and ledgers are **durable in PostgreSQL** when `DATABASE_URL` is configured (Alembic `20261109_0021`). In-memory maps are a process cache; workers hydrate from Postgres before each batch. With `persist=True`, durable claim/complete failures raise a retryable error and never silently fall back to memory-only processing; persist write failures mark the scan `failed` and skip new `verified_ready` publication. Explicit `persist=False` is the non-durable in-memory path for tests.
- Scan lifecycle: `queued` → `running` → `partial` | `budget_exhausted` | `completed_processing` → `verified_ready` | `failed`.
- Per-symbol readiness: `processed` | `technically_qualified` | `source_incomplete` | `verified_ready` | `rejected`.
- OHLCV validation uses the Taiwan trading calendar. Each batch runs a bounded preflight for the years required by monthly MACD/KD warm-up; unknown years fail closed as `trading_calendar_unavailable`. History must reach the scan trading day (`expect_through`). GET/export never downloads calendars.
- Publication manifests expose `technicalStatus`, `enrichmentStatus`, and `sourceCompleteness` (`not_started` | `in_progress` | `partial` | `complete` | `unavailable`). `technicalStatus=complete` requires every accepted symbol to be processed (budget-exhausted mid-universe is `partial`). `sourceCompleteness=complete` only when every accepted stock has a **definitive** technical pass or fail; unknown indicators, provider/symbol errors, insufficient history, missing sessions, invalid candles, and unprocessed stocks keep it `partial`. Valid technical failures are definitive. Enrichment is independent via `enrichmentStatus`.
- Enrichment leases bump `enrich_lease_generation` on each claim; stale workers cannot overwrite enrichment or clear a reclaimed lease.
- Stock Ticker publishes immutable snapshots independently whenever research data is ready or changes. Prior snapshots are never mutated. Consumers choose which snapshot to read; Stock Ticker does not track acknowledgment and never writes Notion.
- Zero qualifying stocks is a valid published outcome when evaluation finished. Incomplete coverage is labeled, not padded to fill `target_count`.
- All scan and snapshot routes are **user-scoped** (no cross-user fallback).
- Retention helper: `persistence.retention_cleanup(retention_days=30)` deletes aged snapshots first, then aged scans. Snapshot `scan_id` uses `ON DELETE SET NULL`.

## Deterministic universe + immutable exports (#319)

- Explicit / theme universes use SHA-256 digests of canonical sorted IDs (no Python `hash()`). Theme versions stay within `String(128)` as `themes:{count}:{digest16}`.
- Finalized `content_digest` and chunk checksums hash research content only (no timestamps or scan/snapshot UUIDs). Identical content → identical digests across reruns; uniqueness is per `(user_id, content_digest)`.
- Latest verified manifest: `GET /api/tw-research/exports/latest/manifest?universeVersion=&tradingDay=` (authenticated; **this user’s** snapshots only; read-only).

### API

All routes use `Depends(get_effective_user_id)`.

| Method | Path | Upstream fetches? |
| --- | --- | --- |
| GET | `/api/tw-research/contract` | No |
| GET | `/api/tw-research/universe/starter-preset` | No |
| POST | `/api/tw-research/universe/resolve` | Catalog only |
| POST | `/api/tw-research/scans` | No (creates ledger) |
| POST | `/api/tw-research/scans/batches` | Yes, bounded |
| POST | `/api/tw-research/scans/enrichment-batches` | Yes, bounded (#313) |
| GET | `/api/tw-research/scans/{scan_id}` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/manifest` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/chunk` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/csv` | No |
| GET | `/api/tw-research/scans/{scan_id}/export/browser-text` | No |
| GET | `/api/tw-research/exports/latest/manifest` | No (#319) |
| GET | `/api/tw-research/exports/snapshots/{snapshot_id}/chunk` | No (#319) |

## Baseline / instrumentation plan (#310 → #321)

Code establishes the contract and budgets. **Actual Ubuntu/k3s headroom** (CPU, RSS, disk, DB/Redis, HTTP counts, latency) is measured on the home node per [tw-research-pilot.md](tw-research-pilot.md). Coding sessions without cluster access must keep status **`ubuntu_measurement_pending`** and must not invent node metrics (#321).

Suggested pilot thresholds to record on the home node:

1. Cold resolve of starter preset (unique count, rejected reasons).
2. Manual scan batches of 10 → 20 → 40 symbols, then full preset across multiple batches.
3. Peak RSS, wall time, upstream month fetches, UI responsiveness during batch POST.
4. Confirm GET snapshot / latest manifest does not increase upstream HTTP counts.
5. Include out-of-preset symbols: e.g. 餐飲 `TW:2727`, 航空 `TW:2618`, unclassified `TW:2330` (if not in preset).
6. Same-universe `targetCount=10` vs `20` cost delta (scan similar; enrichment/export incremental).

## Agent handoff (#322)

Manifest-first, changed-chunks-only transfer: [tw-research-agent-handoff.md](tw-research-agent-handoff.md). A plan is `verification_required` until checksum checks return `stock_ticker_export_verified`. `notion_published_verified` stays with the external Agent.

## Extensibility acceptance

Selecting a valid TW symbol **outside** the starter 14 sectors / ~80 list must work with the same schema and no code change: use `mode=stock_ids` on resolve/create-scan.
