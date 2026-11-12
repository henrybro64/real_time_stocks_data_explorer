# MACD/KD profile values + chip evidence (#331 / #346 / #347)

Gap-fix pass 2026-11-12 (review blockers + round-2 targeted fixes).

## Contract notes (breaking vs early #335 drafts)

- Empty first-pass snapshot ⇒ manifest `chunkCount` is **0** (was 1).
- Chunk rows are sorted by `stockId` ascending (`stableOrdering: stockId_asc`).
- Chunk digests are independently recomputable from `checksumEnvelope` + `rows` (see `docs/tw-research-agent-handoff.md`).

## #331 Stock Profile MACD/KD

- Service: `backend/app/services/stock_technical_indicators.py`
- Route: `GET /api/stocks/{stock_id}/technical-indicators` (TW only, stored bars)
- UI: `frontend/src/views/stock-detail/MacdKdIndicatorsPanel.tsx` on Profile
- Reuses `tw_research.indicators` exactly; `peek_daily_history` only; no scan fan-out
- Coverage gates match scan: `validate_ohlcv_series` + `monthly_series_verified`
- Non-ok quality (`insufficient_history` / `source_incomplete` / `stale`) forces numeric fields + `passStatus` to `null`
- zh-TW labels: DIF / MACD（訊號） / OSC; stale rendered as unavailable with last bar date

### Monthly `expectedMonth` recency

After `_drop_incomplete_trailing_months`, the Profile path computes
`expectedMonth` = the latest calendar month **strictly before** the current
Asia/Taipei month whose last trading session is `<= expect_through` (local
calendar helpers only; no network).

If the last remaining monthly bar’s `(year, month) < expectedMonth`, monthly
quality becomes `stale` with `unavailableReason=latest_completed_month_missing`,
numerics/`passStatus` nulled (same path as coverage failure), `lastBarDate`
kept, and `expectedMonth` set to `YYYY-MM`.

When a year’s TW holiday calendar is not loaded, `is_trading_day(..., allow_holiday_fetch=False)`
falls back to weekday sessions for that recency check.

### Bounded calendar preload

Shared helper: `backend/app/services/tw_calendar_preload.py`
(`ensure_tw_calendars_bounded`). Profile keeps
`ensure_profile_tw_holiday_calendars` as a thin alias.

Before coverage, the Profile path gathers calendar years spanned by completed
bars plus the current year. If calendars are not loaded, it preloads with
`allow_network=True, max_fetches=4` at most **once per process per year-set**
(module lock + 10-minute negative cache on failure). Never inside a per-symbol
loop. If calendars are still missing, coverage stays fail-closed with
`unavailableReason=trading_calendar_unavailable`.

## #346 Chip trends in `/research/tw`

- Shared formatter: `backend/app/services/tw_research/chip_evidence.py` (single source of truth)
- UI: Evidence-tab **籌碼動向** browser + ledger/watchlist detail via frozen chip-evidence API
- API: `GET /api/tw-research/first-pass/snapshots/{id}/chip-evidence?stockIds=...` (max 50)
- Without a pinned first-pass snapshot, UI shows that chip evidence requires a pin (no enrichment fallback)
- Explicit `stale` / `not_fetched` / capturedAt / sourceAsOf status lines; zero short balance is a value
- Not a buy signal; utilization unavailable unless verified
- Chip-evidence queries do **not** use `keepPreviousData`. Pending page queries show
  `InlineLoading`; error-without-data shows `RequestStateNotice`; the
  `stock_not_in_first_pass_snapshot` fallback is only used after a successful
  query when the stockId is absent. Stale banners require `isError && data` for
  the same query key.
- `referenceDay` falls back to snapshot `created_at` converted to Asia/Taipei
- Chip-evidence + report-evidence call `ensure_tw_calendars_bounded` **once per
  request** for `{reference_day.year, year-1}`. When the calendar is unknown,
  institutional/margin are **not** labelled `stale`; they get
  `staleCheck=calendar_unavailable` / `staleBasis=unverified`. UI shows
  “新鮮度未驗證（交易日曆不可用）” / “freshness unverified (trading calendar unavailable)”.

### Capture-time freshness (#347)

Institutional/margin staleness is relative to the snapshot **capture timestamp**
(`created_at` → Asia/Taipei), not the capture date alone:

- Defaults (approximate publication-ready cutoffs, Asia/Taipei):
  - institutional: **16:30** (T86 ≈ 15:00–16:00)
  - margin: **21:30** (≈ 21:00–21:30)
- Override via env/settings: `TW_CHIP_INSTITUTIONAL_READY=HH:MM`,
  `TW_CHIP_MARGIN_READY=HH:MM` (`settings.tw_chip_*_ready`).
- `expected_session(kind, capture_at)` = latest TW trading session `S` such that
  `combine(S, ready_time[kind], Asia/Taipei) <= capture_at` (no network).
- Stale ⇔ `source_as_of < expected_session`. A capture before a session’s ready
  time therefore expects the **previous** session. The managed schedule window
  (15:30–18:30) therefore captures **previous-session margin by design**.
- Capture resolution order: `created_at` → request `tradingDay` @ **23:59**
  Taipei → payload `tradingDay` @ **23:59**. Trading-day fallbacks set
  `staleBasis=date_only`.
- If none is available: institutional/margin get
  `staleCheck=capture_time_unavailable` (never treated as fresh). Full_two_pass
  candidates → blocking `chip_staleness_unverifiable_capture_time_unavailable`;
  non-candidates → chipGaps `stale_check_unavailable` (mirrors calendar handling).
- TDCC remains a 10-calendar-day rule (unchanged).
- Separate report-day gate: when request `tradingDay` is later than
  `expected_session("institutional", capture_at)`, full_two_pass adds blocking
  `first_pass_snapshot_predates_trading_day`; first_pass_only records it in
  `summary.notes` (non-blocking). Fields are **not** all marked stale for this.

### Publication-ready defaults

Code defaults (16:30 / 21:30) come from secondary sources. To measure real
first-available times on trading days, use the read-only probe and runbook:

[`docs/runbooks/tw-chip-publication-times.md`](runbooks/tw-chip-publication-times.md)

Do not change code defaults unless measured times differ by more than 30 minutes;
prefer ConfigMap/env overrides (`TW_CHIP_*_READY`).

### Performance (#346)

Runbook: [`docs/runbooks/tw-research-chip-evidence-perf-346.md`](runbooks/tw-research-chip-evidence-perf-346.md)

Tooling:

- Timing logs: `endpoint.timing.completed` via `app.core.endpoint_timing`
- Offline: `backend/scripts/bench_chip_evidence.py`
- Live READ-ONLY: `scripts/bench-tw-research-endpoints.sh`

**Measured**

_(empty until a human runs the k3s probe and fills the runbook table)_

**Unmeasured**

- chip-evidence page p50/p95 and response size on private k3s
- report-evidence p50/p95 at ~300 records / 40 candidates on private k3s
- pod CPU/memory delta during the live probe
- upstream fan-out confirmation on a cold vs warm pod
- technical-indicators / manifest / chunk live sizes under production snapshots

## #347 Analyst chip handoff

- First-pass chunk digests: `backend/app/services/tw_research/first_pass_chunks.py`
- Manifest/chunks include `chunkDigests`, `checksumEnvelope`, `checksumSha256`, `requiredChipFields`
- Report evidence `chipHandoff` + per-symbol `chipEvidence` from frozen first-pass (enrichment renamed `phase2Enrichment`)
- Ownership fail-closed: cross-user chip-evidence → 404

### `chipGaps` semantics

Gating fields: `institutional_5d`, `margin`, `tdcc_holders_ge_400_lots`
(`margin_utilization` is never gating).

The readiness gate, `summary.chipGaps`, and `symbols[].chipEvidence` all use the
**same Asia/Taipei capture-time freshness** path (`format_chip_evidence` with
`capture_at` from snapshot `created_at`, else `reference_day` @ 23:59 Taipei).
Raw collector payloads typically have `status=ok` and no `stale` flag; staleness
is derived from `source_as_of` vs `expected_session(kind, capture_at)` (and
explicit raw `stale: true` is still honored). When the TW holiday calendar is
unknown, institutional/margin staleness is **not claimed**; see
`staleCheck=calendar_unavailable`.

**Blocking warnings**

- `first_pass_chip_fields_missing` — any record lacks a gating key (`not_fetched`),
  or any gating field is `unavailable`/`partial` with empty/None reason.
- `candidate_chip_fields_stale_or_missing` — **full_two_pass only**; any stock in
  the candidate set (`candidate_set.stockIds`) has a gating field that is `stale`
  or `not_fetched`.
- `chip_staleness_unverifiable_calendar_unavailable` — **full_two_pass only**; a
  candidate gating field has `staleCheck=calendar_unavailable` (fail closed with
  an honest reason, not a false `stale`).
- `first_pass_snapshot_predates_trading_day` — **full_two_pass only**; request
  `tradingDay` is later than `expected_session("institutional", capture_at)`.
  In `first_pass_only` this is a non-blocking `summary.notes` entry instead.
- `chip_staleness_unverifiable_capture_time_unavailable` — **full_two_pass only**;
  a candidate gating field has `staleCheck=capture_time_unavailable` (no
  `created_at` and no tradingDay fallback).

**Non-blocking** reasoned `unavailable`/`partial` and stale outside the candidate
set are reported in `summary.chipGaps`. Non-candidate
`calendar_unavailable` → chipGaps status `stale_check_unavailable`:

```json
{
  "counts": { "margin": { "unavailable": 20 } },
  "items": [{ "stockId": "TW:…", "field": "margin", "status": "unavailable", "reason": "…" }],
  "truncated": false
}
```

Items are sorted by `stockId`, capped at 200. The blanket
`first_pass_chip_fields_partial` warning is removed.

## Tests

```bash
cd backend && .venv/bin/python3.11 -m pytest \
  tests/unit/services/test_stock_technical_indicators_331.py \
  tests/unit/services/tw_research/test_chip_evidence_346_347.py \
  tests/unit/core/test_endpoint_timing.py \
  tests/unit/scripts/test_bench_chip_evidence_smoke.py \
  tests/unit/routes/test_technical_indicators_331.py \
  tests/unit/routes/test_tw_research_route.py -q

cd backend && .venv/bin/python3.11 scripts/bench_chip_evidence.py --n 73 300 --iterations 5

cd frontend && npm test -- --run \
  src/views/stock-detail/MacdKdIndicatorsPanel.test.tsx \
  src/views/stock-detail/StockDetailPage.test.tsx \
  src/views/research/ChipEvidenceCard.test.tsx \
  src/views/research/TwResearchPage.test.tsx
```

## Safety

- CronJob unchanged / still suspended
- No Notion writes
- No unbounded universe materialization in report-evidence response
