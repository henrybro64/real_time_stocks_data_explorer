# MACD/KD profile values + chip evidence (#331 / #346 / #347)

Gap-fix pass 2026-11-12 (review blockers).

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

## #346 Chip trends in `/research/tw`

- Shared formatter: `backend/app/services/tw_research/chip_evidence.py` (single source of truth)
- UI: Evidence-tab **籌碼動向** browser + ledger/watchlist detail via frozen chip-evidence API
- API: `GET /api/tw-research/first-pass/snapshots/{id}/chip-evidence?stockIds=...` (max 50)
- Without a pinned first-pass snapshot, UI shows that chip evidence requires a pin (no enrichment fallback)
- Explicit `stale` / `not_fetched` / capturedAt / sourceAsOf status lines; zero short balance is a value
- Not a buy signal; utilization unavailable unless verified
- k3s performance impact of the chip-evidence page query is **still unmeasured**

## #347 Analyst chip handoff

- First-pass chunk digests: `backend/app/services/tw_research/first_pass_chunks.py`
- Manifest/chunks include `chunkDigests`, `checksumEnvelope`, `checksumSha256`, `requiredChipFields`
- Report evidence `chipHandoff` + per-symbol `chipEvidence` from frozen first-pass (enrichment renamed `phase2Enrichment`)
- Chip content gates publishability (`first_pass_chip_fields_missing` / `first_pass_chip_fields_partial`); `complete_attempted` alone is not enough
- Ownership fail-closed: cross-user chip-evidence → 404

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/test_stock_technical_indicators_331.py \
  tests/unit/services/tw_research/test_chip_evidence_346_347.py \
  tests/unit/routes/test_technical_indicators_331.py \
  tests/unit/routes/test_tw_research_route.py -q

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
