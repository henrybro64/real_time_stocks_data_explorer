# Taiwan research next (#338–#341)

Manual fundamental-first second pass, same-page research center tabs, analyst
evidence export, and gated orchestration. No CronJob enablement, no Notion SDK,
no production deploy in this slice.

## Issues

| Issue | Outcome (fixed 2026-11-12) |
| --- | --- |
| #338 Monthly-first MACD/KD for candidates | `not_run_by_design`, overrides, `technical_stage`, **73→12 admission fixture** |
| #339 `/research/tw` three tabs | Tabs + Phase 1–4 banner + expandable rows + Playwright tabs spec |
| #340 Notion-ready evidence (Stock Ticker only) | Pack + ZH-TW fixture + UI export/example + two-decision unit proof |
| #341 Manual full-research first | **Postgres `tw_research_research_runs`** (fail-closed); start creates first-pass job |

See `docs/AI_HANDOFF.md` for commands and commit notes. Migration: `20261112_0025`.

## Key modules

- `backend/app/services/tw_research/scan.py` — monthly-first path + `_not_run_daily_payload`
- `backend/app/services/tw_research/evaluation.py` — `dailyNotRunByDesign*` / watchlist counters; `map_recoverable_status`
- `backend/app/services/tw_research/research_run.py` — manual orchestration + gates
- `backend/app/services/tw_research/report_evidence.py` — analyst evidence pack
- `backend/app/services/tw_research/report_evidence_fixture_zh_tw.json` — labeled example only
- `frontend/src/views/research/TwResearchPage.tsx` — three Carbon tabs

## APIs added

| Method | Path |
| --- | --- |
| GET | `/api/tw-research/research-runs/current` |
| POST | `/api/tw-research/research-runs/start` |
| POST | `/api/tw-research/research-runs/gate` |
| POST | `/api/tw-research/report-evidence` |
| GET | `/api/tw-research/report-evidence/example-zh-tw` |

Candidate set / technical-scan requests accept `dailyOverrideIds`.

## Safety

- `k8s/backend-tw-research-schedule-cronjob.yaml` remains `suspend: true`
- Stock Ticker does not write Notion or rank final picks
- Hard caps never silently bypassed

## Tests

```bash
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/ \
  tests/unit/routes/test_tw_research_route.py -q
# 199 passed

cd frontend && npm test -- --run src/views/research/TwResearchPage.test.tsx
# 31 passed

# Optional (requires Playwright browsers):
PLAYWRIGHT_MOCKS_ONLY=1 PLAYWRIGHT_SKIP_WEBSERVER=1 \
  npx playwright test tests/e2e/tw-research-tabs.spec.ts
```
