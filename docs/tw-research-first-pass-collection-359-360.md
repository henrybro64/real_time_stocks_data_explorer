# TW Research First-Pass collection (#359 / #360)

## Canonical Start (#359)

Ordinary user Start (`開始第一輪資料收集` / `開始研究`) calls
`POST /tw-research/research-runs/start`.

That path:

1. Creates or returns the durable research run (fenced per-user claim).
2. Creates or attaches the authoritative first-pass job.
3. Sets `schedule_enabled` only when drain is available for this user.
4. Returns an additive `execution` block that distinguishes:
   - `job_created`
   - `execution_authorized`
   - `executor_unavailable`
   - `actively_collecting`
   - `waiting_for_retry`
   - `completed` / `partially_complete`
   - `awaiting_analyst_candidate_selection`
   - `failed` / `cancelled`

HTTP 200 or a job row insert alone is **not** proof that collection is running.

### Safe adoption

`POST /tw-research/research-runs/adopt-first-pass-job` explicitly adopts an
eligible standalone job (for example a historical 1/73 job). It validates:

- authenticated owner
- no deletion-in-progress / cancelled
- no active lease
- optional universe digest match
- no conflicting active research-run job

It never silently auto-adopts arbitrary orphans. Persisted symbol evidence is kept.

### Production CronJob

This branch does **not** unsuspend
`stock-ticker-tw-research-first-pass-drain`. Local / isolated testing can run
`run_batch` or a dry-run drain. When the executor is disabled, the UI reports
`executor_unavailable` with a safe next action.

## Timeout hierarchy (#360)

| Layer | Budget | Notes |
|---|---|---|
| Soft per-symbol deadline (all providers) | 45s | ContextVar; each HTTP timeout capped to remaining; skip if ≤1s left |
| Batch wall-clock budget | 60s | Stop before starting the next symbol |
| Drain curl `--max-time` | 150s | CronJob HTTP client |
| k8s Job `activeDeadlineSeconds` | 180s | Bounds the curl Job |
| Batch lease | 300s | Renewed per finished symbol + heartbeat |

Worst case for one drain tick: batch budget 60s + one in-flight symbol ≤45s = **≤105s**, which is under curl 150s < Job 180s < lease 300s.

### Per-provider hard timeouts (first-pass path)

| Provider / step | Configured timeout | Max attempts | Worst-case (timeout×attempts + backoff) | Notes |
|---|---|---|---|
| Catalog / identity | local | 1 | ~0 | No HTTP |
| Stored candles / 52w peek | local PG | 1 | ~0 | No upstream month fetch |
| Institutional day heal | 8s (TWSE/TPEx client) | ≤ recent_days probes | bounded by symbol deadline | Store-first; live only when stale |
| Margin day heal | 8s | ≤ recent_days probes | bounded by symbol deadline | Store-first (#360) |
| PE day snapshot | 8s | 1 per board (lru) | ≤8s first symbol / board | In-process cache |
| TDCC open-data CSV | **min(20s, remaining)** | **2** | ≤40s but still inside 45s symbol deadline | Process singleflight + publication-date key |
| TDCC portal Friday | 30s | 2 retries / Friday | Cut by symbol deadline between loops | Inherently per-symbol |

A Python soft deadline cannot interrupt blocked I/O; hard request timeouts + remaining-time caps prevent starting work that cannot finish.

## Crash-safe persistence (#360)

Each finished symbol is upserted **before** the next symbol starts
(`on_symbol_collected`). Writes require:

- matching `lease_generation`
- non-expired `reserve_expires_at`
- job not cancelled / deleting

Committed rows survive pod death. Lease loss mid-batch keeps already-persisted
symbols and releases unstarted ids on complete.

## Provider audit (Phase A evidence)

| Field group | Adapter / service | Bulk-capable today? | Notes |
|---|---|---|---|
| Identity / catalog | `quote_service.get_catalog_stock` | Catalog local | No HTTP per Start |
| Daily prices / 52w | `daily_price_history_service` + stored candles | Prefer stored history | Avoids refetch when candles exist |
| Institutional 5d | `tw_institutional_service` | Market-day file → PG | Store-first (`taiwan_institutional_daily`); bounded live heal |
| Margin / short | `tw_margin_short_service` | Market-day file → PG | Store-first (`taiwan_margin_short_daily`) wired in #360 |
| TDCC ownership | `tw_ownership_concentration_service` | Open-data CSV market-wide; portal per-symbol | Process TTL cache for open-data CSV; portal remains per-symbol; 45s soft deadline |
| PE / official valuation | `key_stats_service` day snapshots | Market-day PE file | In-process `lru_cache` of TWSE/TPEx PE snapshots |
| Monthly revenue / OCF / debt / PEG | none official | Not bulk-accessible here | Stay `unavailable` with explicit reasons; never 0 |

Partial success model: successful fields commit with provenance; missing fields
keep explicit reasons and selective retry. Missing ≠ 0. Prior `status=ok`
fields are reused via `previous_fields` / `_merge_preserved_ok_fields`.

### Request-budget intent (before → after, when PG warm)

| Path | Before | After (warm store / cache) |
|---|---|---|
| Margin per stock | Live weekday walk (margin + borrowed [+ close]) | PG `list_history` only |
| Institutional per stock | Store-first already | unchanged |
| TDCC open-data | N× HTTP for same CSV | 1× per process TTL |
| TDCC portal Fridays | N× per stock | still per stock (no official bulk history) |
| PE day file | 1× per board per process | unchanged (`lru_cache`) |

### Fixture-measured request budget (offline mocks)

From `test_first_pass_request_budget_360.py` (no live providers):

| Universe | TDCC open-data downloads | Margin live HTTP (warm PG store) |
|---|---|---|
| 73 | 1 | 0 |
| 300 | 1 | 0 |

## Follow-ups

- #361: stepper / control-audit polish (ordinary UI already has one primary Start).
- Multi-Pod singleflight for MI/margin day-file refresh.
- Persist TDCC open-data snapshots in PostgreSQL for cross-pod sharing.
