# AI handoff — #338–#341 merge-readiness corrections

**Branch:** `issue-338-341-tw-research-next`  
**Base:** `main`  
**Feature commit (code):** `975ec23fe5867b393901f3ac103b4b42ccda3a60`  
**Branch tip:** run `git rev-parse HEAD` on `issue-338-341-tw-research-next` (handoff-only follow-up may sit on tip)  
**Date:** 2026-11-12

## Findings fixed (this pass)

| ID | Finding | Resolution |
| --- | --- | --- |
| P1-1 | Phase-2 `already_attached` reused scan without matching request semantics | `_phase2_scan_request_mismatches` compares candidate-set id/version, trading day, calculation version, daily overrides, approved membership; incompatible reuse → HTTP 409 `phase2_scan_request_mismatch` with hint to retry identically or start a new run after a terminal/gated transition; immutable scans preserved |
| P1-2 | Crash between first-pass job create and `pending_first_pass_job_id` left unrecoverable jobs | Claim allocates durable `pending_first_pass_job_id = fpj_{research_run_id}` before create; route passes that id into `create_job`; `create_first_pass_job` is PK-idempotent; expiry reclaim recovers the same id; orphans still marked |
| P2 | Watchlist used mutable membership and labeled all unselected as fundamental keepers | Universe from pinned first-pass snapshot; reasons: `fundamentally_interesting_not_selected`, `first_pass_not_selected_for_technical`, `insufficient_evidence_fundamental`, plus monthly/daily technical deferrals; membership edits after scan do not alter frozen rows |

## Prior findings (already on branch; not redesigned)

- DB-fenced first-pass / Phase-2 claims (`SELECT … FOR UPDATE`)
- Authoritative research gates + OCC revision
- Strict two-pass evidence validation + technical ledger export
- Migrations `20261112_0025`–`20261112_0027`

## Files changed (this correction pass)

- `backend/app/api/routes/tw_research.py`
- `backend/app/repositories/tw_research_repository.py`
- `backend/app/services/tw_research/{research_run,first_pass_job}.py`
- `backend/tests/unit/routes/test_tw_research_route.py`
- `backend/tests/unit/services/tw_research/test_monthly_first_338_341.py`
- `backend/tests/integration/services/test_tw_research_run_claims_341.py`
- `frontend/src/views/research/{TwResearchPage.tsx,TwResearchPage.test.tsx,twResearchI18n.ts}`
- `frontend/src/services/api/twResearch.ts`
- `docs/ai-handoff-338-341.md`

## Tests actually executed

```bash
cd backend && .venv313/bin/python -m pytest tests/unit/services/tw_research/ -q
# 198 passed

cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_monthly_first_338_341.py \
  tests/unit/routes/test_tw_research_route.py -q
# 30 passed

ALLOW_DESTRUCTIVE_DB_TESTS=1 \
DATABASE_URL=postgresql+psycopg://henrybro@127.0.0.1:5432/stocks_test \
  .venv313/bin/alembic upgrade head
# applied through 20261112_0027 on disposable stocks_test

ALLOW_DESTRUCTIVE_DB_TESTS=1 \
DATABASE_URL=postgresql+psycopg://henrybro@127.0.0.1:5432/stocks_test \
  .venv313/bin/python -m pytest \
  tests/integration/services/test_tw_research_run_claims_341.py -q
# 5 passed (concurrent start, crash before create, crash after job before attach,
#           conflicting claim ownership, five duplicate HTTP starts)

cd frontend && npm test -- src/views/research/TwResearchPage.test.tsx
# 34 passed

cd frontend && npx tsc --noEmit
# clean

cd frontend && PLAYWRIGHT_MOCKS_ONLY=1 npx playwright test tests/e2e/tw-research-tabs.spec.ts
# 3 passed (after installing Chromium locally)
```

k3s performance: **not measured**.

## Outstanding acceptance / ops

- Push / open PR when authorized
- CI green on unit + disposable Postgres integration
- Apply migrations `20261112_0025`–`0027` in target env before relying on claims
- CronJobs remain suspended; no Notion publish / live market collection as part of merge
## Remaining performance / operational risks

- Full-two-pass evidence still materializes technical rows in one response (bounded by candidate-set size)
- `claim_held` / Phase-2 409 retryable responses require client retry
- Orphan first-pass jobs are marked failed/annotated, not deleted
- Watchlist still client-paginates the frozen first-pass universe; huge universes may need a search API later

## Exact next action

1. Review local commit on `issue-338-341-tw-research-next`
2. When authorized: push branch and open PR against `main`
3. Confirm CI + migration apply on disposable/staging DB

## Independent merge-readiness verdict

**READY FOR PR** — P1-1, P1-2, and P2 defects addressed with unit + disposable Postgres integration + frontend unit/tsc coverage. Playwright E2E and k3s perf remain unverified operational follow-ups, not blockers for opening the PR.
