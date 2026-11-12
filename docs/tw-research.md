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
| `backend/app/services/tw_research/contract.py` | Versioned field/source map (+ #335 feasibility matrix) |
| `backend/app/services/tw_research/first_pass.py` | Fundamental-first first-pass collector (#335/#336) |
| `backend/app/services/tw_research/price_basis.py` | Relative price-basis evidence (#337) |
| `backend/app/services/tw_research/fundamentals_watchlist.py` | Versioned membership / compute subset (#336, Postgres fail-closed) |
| `backend/app/services/tw_research/first_pass_durable.py` | Immutable first-pass snapshots + analyst candidate sets (#335/#336) |
| `docs/tw-research-fundamentals.md` | #335–#337 audit, gaps, APIs |
| `docs/tw-research-next-338-341.md` | #338–#341 monthly-first, tabs, evidence, gates |
| Alembic `20261111_0023` | Membership, first-pass snapshots, candidate sets |
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
| Alembic `20261110_0022` | History flights, coordination locks, scan lease generation, budget windows |

## Indicator conventions (#311)

- Completed daily candles only (`moving_average.completed_daily_candles`).
- EMA seed = SMA of the first `period` values.
- OSC = DIF − Signal (not ×2).
- KD zero-range (H9 == L9): RSV = 50; K/D seed = 50; smoothing weight `1/3`.
- Monthly bars: calendar-month OHLCV; **current month excluded**.
- `daily_price_history_service` `MAX=800` is **not** silently treated as enough for long monthly MACD warm-up; insufficient history returns `unknown` / `insufficient_history`.
- Pass (day or month independently): `DIF > 0 AND OSC > 0 AND K > 50 AND D > 50`.

## Scan, checkpoints, and readiness (#312 / #318 / #320)

- Legacy / technical-first (`CALCULATION_VERSION`): Stage A daily MACD/KD; Stage B monthly + SMA20/60/120/240 + 52-week for **day-pass** symbols only.
- Candidate-derived monthly-first (`CALCULATION_VERSION_MONTHLY_FIRST`, #336): Stage A monthly MACD/KD for every approved candidate; Stage B daily + levels only when monthly passes (`MONTHLY_FIRST_DAILY_CONTINUATION_POLICY=only_on_monthly_pass`). Monthly evidence is retained when daily is deferred or fails. Incomplete monthly history is `source_incomplete`, not a screening fail.
- Progress counters (`stage-progress-v1`): `dayPass*` always means **daily** MACD/KD pass; monthly-first also exposes `monthPass*`, `firstStagePass*`, `dailyDeferred*`, and `monthlyIncomplete*` derived from committed symbol evidence.
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
| GET | `/api/tw-research/feasibility-matrix` | No (#335) |
| POST | `/api/tw-research/first-pass/batch` | Yes, bounded (#335/#336) |
| GET/PUT | `/api/tw-research/membership` | Catalog only (#336) |
| POST | `/api/tw-research/relative-price-basis` | Stored bars only (#337) |
| GET | `/api/tw-research/universe/starter-preset` | No |
| POST | `/api/tw-research/universe/resolve` | Catalog only |
| POST | `/api/tw-research/scans` | No (creates ledger) |
| POST | `/api/tw-research/scans/batches` | Yes, bounded |
| POST | `/api/tw-research/scans/resume-budget` | No (#329 reopen window) |
| POST | `/api/tw-research/schedule/run` | Yes, bounded (#330 token auth) |
| POST | `/api/tw-research/scans/enrichment-batches` | Yes, bounded (#313) |
| GET | `/api/tw-research/scans` | No (#327 recent history) |
| GET | `/api/tw-research/scans/{scan_id}` | No |
| GET | `/api/tw-research/scans/{scan_id}/progress` | No (#327 counters only) |
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

## Persisted research dashboard (#327)

PostgreSQL is authoritative for scan list, progress, and ledgers. Durable
`get_scan` / progress reads raise a retryable store error on database failure
instead of returning an empty or partial ledger. The UI polls
`GET /scans/{id}/progress` while a scan is active and reloads the full ledger
only when `ledgerRevision` changes. The recent-scan list polls slowly so an
external Agent or another session can appear without stealing the user's
selected scan. UI duplicate-create checks are **session/page helpers only**.

## History-budget resume and evaluation counters (#329)

- `POST /api/tw-research/scans/resume-budget` opens another bounded upstream
  month-fetch window (default +40, hard cap 200), clears
  `history_budget_exhausted` symbol rows, and rewinds the durable cursor so a
  later batch can resume. Repeat `POST /scans/batches` while status is
  `budget_exhausted` returns **HTTP 409** (`budget_resume_required`) — never a
  false HTTP 200 no-op. Durable resume is a **single PostgreSQL transaction**
  (`SELECT … FOR UPDATE`); concurrent resumes cannot double-grant windows, and
  resume is rejected while an active scan-batch lease is held.
- Snapshots and progress expose `definitiveEvaluatedCount`,
  `budgetExhaustedCount`, `sourceIncompleteCount`, `unresolvedCount`, and
  `unprocessedCount` so `processedCount` is never mistaken for valid Top-N
  results. Unknown / budget-exhausted rows stay out of verified recommendations.
  Progress polls use lean evaluation fields only — not full ledger payloads.
- Warm OHLCV already lives in `daily_price_bars` /
  `daily_price_month_fetches`; a later window reuses stored months
  (`months_used=0`) without spending budget.
- **Cross-pod history single-flight:** compatible backfills use
  `tw_research_history_flights` leases. Compatibility contract:
  `symbol|basis=unadjusted_close|adj=none|provider|limit|through=<tradingDay>|bars=daily_v1`.
  Short DB transactions acquire/renew/complete the lease; waiters poll with
  bounded backoff and never hold a DB transaction during upstream HTTP. A
  `ready` marker is emitted only when persisted OHLCV coverage is validated for
  `expect_through`; waiters always re-check the store and load with budget 0.
  Incomplete fetches clear the hold without a ready marker. Leaders renew the
  lease while upstream work runs so slow healthy fetches are not stolen.
  Process locks remain an intra-pod accelerator. Lease fencing (`generation` +
  owner) recovers after pod crash without stale completes.
- **Atomic cluster admission:** `claim_scan_batch` serializes on the
  `scan_admission` coordination lock, counts live leases, and claims in one
  transaction (fail closed on durable DB errors). `scan_lease_generation`
  fences stale batch completes **and** technical symbol-result upserts: a write
  must present the authorizing generation and an still-active
  `reserve_expires_at` lease, or PostgreSQL rejects it without mutating the row.
  Enrichment continues to use `enrich_lease_generation` independently. Seed rows
  and unfenced fixtures omit both tokens. Limit:
  `DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES` (HTTP 503 when saturated). Alembic
  `20261110_0022`.
- Partial / budget-exhausted finalize stays labeled via
  `technicalStatus` / `sourceCompleteness`; never advertise full coverage when
  unresolved symbols remain.

## Post-market schedule (disabled by default) (#330)

- Internal tick: `POST /api/tw-research/schedule/run` with header
  `X-Tw-Research-Schedule-Token`. Mounted on a **separate router without session
  auth** so CronJob calls work when `AUTH_ENABLED=true`. Requires configured
  `TW_RESEARCH_SCHEDULE_TOKEN`, `TW_RESEARCH_SCHEDULE_USER_ID`, and
  `TW_RESEARCH_SCHEDULE_ENABLED=true`. Placeholder tokens are treated as unset.
  The schedule token never unlocks ordinary `/tw-research/*` user routes.
- Skips Taiwan pre-close sessions (`skipped_data_not_ready`). After the session
  is complete, a lightweight persisted-OHLCV readiness sample
  (`skipped_market_data_not_ready`) still gates *creating* today's scan;
  prior-day backlog continues. On holidays / weekends, still continues
  incomplete backlog within `SCHEDULE_BACKLOG_MAX_CALENDAR_DAYS` without creating
  a newer-day scan. Prioritizes the **oldest** incomplete compatible scan
  (owner + universe + calculation + target) so yesterday is not abandoned when a
  new trading day begins; the original trading-day calculation anchor is
  preserved. Same-day reuse remains idempotent — completed technical scans and
  `verified_ready` snapshots without remaining enrichment report completion
  instead of opening a duplicate scan. A `verified_ready` snapshot with pending
  enrichment continues bounded enrichment. When the per-scan enrichment window
  (`max_enrichments_per_run`) is spent but qualified symbols still lack
  enrichment, the tick opens another bounded window (default +20, hard cap 100)
  via atomic PostgreSQL resume — analogous to #329 history-budget windows —
  rather than treating exhaustion as a permanent stop. Only the hard cap yields
  a terminal `enrichment_budget_exhausted` / `enrichment_cap_reached` outcome.
  Each tick runs bounded technical batches, optional #329 resume, optional
  enrichment-window resume, then bounded enrichment. Outcomes distinguish
  technical completion, enrichment partial / budget exhaustion, verified ready,
  deferred lock / lease loss / deadline, and failures. Durable ticks **fail
  closed** when PostgreSQL coordination is unavailable (never run uncoordinated).
  Owner-level PostgreSQL tick locks are heartbeated during long batches.
  History-flight renewals fail closed after consecutive PostgreSQL renew errors;
  confirmed lease loss raises a retryable coordination error and does not publish
  a false `ready` marker or a definitive screening result for that symbol.
- Kubernetes CronJob `stock-ticker-tw-research-schedule` ships **suspended**
  (`suspend: true`, `timeZone: Asia/Taipei`, weekday continuation windows
  `30 15-18 * * 1-5`, `concurrencyPolicy: Forbid`, resource limits,
  `activeDeadlineSeconds: 900`). Do not unsuspend or set ENABLED without pilot
  approval (#321) and an explicit operator decision.
- Manual `/research/tw` create/batch/resume remains available and still respects
  global admission / single-flight controls from #329.

## Browser rewrite proxy and long research batches (#328)

Same-origin `/api/*` traffic is rewritten through Next.js. Installed Next
`proxy-request` defaults `proxyTimeout` to **30 seconds** and answers HTTP 500
with body `Internal Server Error` when that fires, even if FastAPI later logs
HTTP 200. `experimental.proxyTimeout` is a **single global** rewrite setting —
Next does not expose a path-scoped timeout — so Stock Ticker sets it to
**150 seconds** (`frontend/research-proxy-timeout.mjs`) to cover the default
research batch budget of 120 seconds. Shorter unrelated API calls still finish
as soon as the upstream responds. Capture routes remain in `beforeFiles` and
never hit the rewrite proxy.

This repo's Traefik ingress has no explicit request timeout annotation. Probe
`timeoutSeconds` values are health checks only. The original 2026-11-10 UI
`Request failed: 500` was **not** packet-captured; the 30s/49.7s gap is the
established mechanism from source + access logs, not a live reproduction.

## Extensibility acceptance

Selecting a valid TW symbol **outside** the starter 14 sectors / ~80 list must work with the same schema and no code change: use `mode=stock_ids` on resolve/create-scan.
