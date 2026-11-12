# AI handoff — #338–#341 merge-readiness corrections

**Branch:** `issue-338-341-tw-research-next`  
**Base:** `main`  
**Latest local SHA at handoff write:** see `git rev-parse HEAD` after commit  
**Date:** 2026-11-12

## Findings resolved

| ID | Finding | Resolution |
| --- | --- | --- |
| P1-1 | Concurrent `/research-runs/start` could create multiple first-pass jobs while `first_pass_job_id` was null | DB-fenced claim (`SELECT … FOR UPDATE`) + `pending_first_pass_job_id`; only claim owner creates/attaches; orphans marked via `mark_first_pass_job_orphaned` |
| P1-2 | Technical scan persisted before run attach; `KeyError` swallowed | Phase-2 claim + pending scan id + atomic attach; fail closed with `scanId` + retryable 503; no silent KeyError |
| P1-3 | Evidence pack could look ready with missing pins/quality | Explicit `first_pass_only` vs `full_two_pass`; require pinned candidate set; missing `qualityStatus` → `incomplete_or_partial_evidence` |
| P2 | Watchlist omitted first-pass keepers never selected for MACD/KD | Union monthly-fail / daily-not-run / monthly-incomplete / membership-not-selected with distinct reasons |

## Files changed (this correction pass)

- `backend/alembic/versions/20261112_0027_research_run_job_claims.py`
- `backend/app/db/models.py`
- `backend/app/repositories/tw_research_repository.py`
- `backend/app/services/tw_research/{research_run,report_evidence,constants,scan}.py`
- `backend/app/api/routes/tw_research.py`
- `backend/tests/unit/services/tw_research/test_monthly_first_338_341.py`
- `backend/tests/unit/routes/test_tw_research_route.py`
- `backend/tests/integration/services/test_tw_research_run_claims_341.py`
- `frontend/src/views/research/{TwResearchPage.tsx,TwResearchPage.test.tsx,twResearchI18n.ts}`
- `frontend/src/services/api/twResearch.ts`
- `frontend/tests/e2e/tw-research-tabs.spec.ts`
- `docs/tw-research-next-338-341.md`
- `docs/ai-handoff-338-341.md`

## Test commands and results

```bash
cd backend && .venv313/bin/python -m pytest tests/unit/services/tw_research/ -q
# 197 passed

cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_monthly_first_338_341.py \
  tests/unit/routes/test_tw_research_route.py -q
# 27+ route extras (see latest run)

ALLOW_DESTRUCTIVE_DB_TESTS=1 DATABASE_URL=postgresql://…/stocks_test \
  .venv313/bin/python -m pytest \
  tests/integration/services/test_tw_research_run_claims_341.py -q
# 3 passed

cd frontend && npm test -- --run src/views/research/TwResearchPage.test.tsx
# 32 passed
```

Playwright (optional):

```bash
cd frontend && PLAYWRIGHT_MOCKS_ONLY=1 PLAYWRIGHT_SKIP_WEBSERVER=1 \
  npx playwright test tests/e2e/tw-research-tabs.spec.ts
```

## Remaining limitations

- k3s / production performance benchmarks: **not done**
- CronJob schedule enablement: **not done** (`suspend: true`)
- Full 300/40 digest-verified chunk soak against live providers: **not done** (unit/fixture coverage only)
- Server-side watchlist search API for huge universes: membership∪scan union is client-paginated; may need a dedicated endpoint later
- Orphan first-pass jobs created before claim-loss are marked failed/annotated, not deleted

## Known performance risks

- Full-two-pass evidence still materializes all technical rows in one response (bounded by candidate-set size, typically ≤40)
- Concurrent start under claim_held returns queued run without job id — clients must retry
- Phase-2 claim_held returns 409 retryable

## Git status / merge-readiness

- Local commit created for this pass (no push unless authorized)
- **Verdict: READY FOR PR** after applying migration `20261112_0027` in the target environment and CI green
- Do **not** enable CronJobs, Notion writes, or live market collection as part of merge

## Next action for ChatGPT / Claude / Cursor

1. Push branch / open PR against `main` when authorized  
2. Ensure CI runs unit + disposable Postgres integration  
3. Keep schedule suspended; leave #338–#341 open until ops sign-off on enablement checklist  
