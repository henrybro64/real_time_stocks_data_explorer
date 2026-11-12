# MACD/KD profile values + chip evidence (#331 / #346 / #347)

Gap-fix pass 2026-11-12: P0 FE/BE totals aligned; route/stale/Playwright coverage added.
See `docs/AI_HANDOFF.md`.

## #331 Stock Profile MACD/KD

- Service: `backend/app/services/stock_technical_indicators.py`
- Route: `GET /api/stocks/{stock_id}/technical-indicators` (TW only, stored bars)
- UI: `frontend/src/views/stock-detail/MacdKdIndicatorsPanel.tsx` on Profile
- Reuses `tw_research.indicators` exactly; `peek_daily_history` only; no scan fan-out
- Non-ok quality forces numeric fields + `passStatus` to `null` (never fake zeros)
- Route tests: `tests/unit/routes/test_technical_indicators_331.py`
- Playwright: `frontend/tests/e2e/tw-research-macd-chip.spec.ts`

## #346 Chip trends in `/research/tw`

- Shared formatter: `backend/app/services/tw_research/chip_evidence.py`
- UI: Evidence-tab **籌碼動向** browser + `ChipEvidenceCard` (expand rows + browser)
- API: `GET /api/tw-research/first-pass/snapshots/{id}/chip-evidence`
- FE `formatChipEvidenceFromFields` matches BE partial sum-present semantics
- Explicit `stale` / `not_fetched` / capturedAt / sourceAsOf status lines
- Not a buy signal; utilization unavailable unless verified

## #347 Analyst chip handoff

- First-pass chunk digests: `backend/app/services/tw_research/first_pass_chunks.py`
- Manifest/chunks include `chunkDigests`, `checksumSha256`, `requiredChipFields`
- Report evidence `chipHandoff` + Decision 1/2 guidance
- Ownership fail-closed: cross-user chip-evidence → 404
- Consumers must still verify digests (`complete_attempted` ≠ verified_ready)

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
