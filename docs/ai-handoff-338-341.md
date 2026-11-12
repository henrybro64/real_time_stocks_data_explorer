# AI handoff — #338–#341 final pre-PR readiness

**Branch:** `issue-338-341-tw-research-next`  
**Base:** `main`  
**HEAD (at handoff write):** `48ba50247846b77e548a4e03a4f6426dd8098d12`  
**Date:** 2026-11-12  
**Independent review status entering this pass:** READY FOR PR/CI — no confirmed P0/P1 source blockers  
**This document:** final cleanup / release-prep only (no feature redesign)

Use `Refs #338` `#339` `#340` `#341` on the PR. Do **not** use `Closes` until ops sign-off on deferred acceptance items below.

---

## Final implementation summary

| Issue | What shipped (code) |
| --- | --- |
| **#338** | Monthly-first MACD/KD on analyst-selected candidates only; daily only after monthly pass (or explicit bounded `dailyOverrideIds`); `not_run_by_design` / watchlist stages; admission fixtures 73→12 and 300→40 |
| **#339** | `/research/tw` three Carbon tabs (evidence / watchlist / progress); phase banner; frozen first-pass watchlist classifications; pagination; PG-backed progress polling |
| **#340** | Stock Ticker evidence pack + ZH-TW labeled fixture; digest/provenance; **no** Notion write / no backend shortlist; two external analyst decisions against one snapshot |
| **#341** | Manual `research_run` orchestration in PostgreSQL; DB-fenced job/scan claims; OCC `revision`; two explicit analyst gates; CronJobs stay suspended |

### Phase-2 daily override semantics (authoritative)

```
effective daily overrides =
  pinned candidate-set selectionReasons.dailyOverrideIds
  ∪ request.dailyOverrideIds
```

Normalization: `dedupe_stock_ids` (case / `TW:` canonicalization, order-preserving then compared sorted).

Reuse of an attached technical scan requires **identity** of:

1. Approved candidate-set ID + version (research run ∧ scan provenance ∧ optional request fields)
2. Trading day (omitted request day inherits attached day)
3. Calculation version (`macd…_monthly_first_v1`)
4. Frozen approved stock universe
5. Effective override set vs scan provenance `dailyOverrideIds`

Pinned candidate-set load is **exact version only** — never latest/unversioned fallback. Empty stored overrides ≠ missing/unreadable provenance. Missing pin → **409**; durable-store failure → retryable **503**.

### PostgreSQL claim fencing & recovery

- First-pass: `claim_first_pass_job_slot` (`SELECT … FOR UPDATE`) allocates `pending_first_pass_job_id = fpj_{research_run_id}` **before** job create; create is PK-idempotent; attach clears claim; expiry allows reclaim; orphans marked.
- Phase-2: analogous `phase2_claim_*` / `pending_technical_scan_id`.
- OCC: `revision` on upsert; conflict → **409** `research_run_revision_conflict`.
- `GET /research-runs/current` and `POST /research-runs/gate` call server `reconcile()`; client status params ignored.

### Two explicit analyst gates

1. **Candidate selection** — approve versioned subset after first-pass snapshot (before technical scan).
2. **Report / Notion decision** — external ChatGPT/Agent only; Stock Ticker exports evidence and never auto-publishes.

### UI ↔ PostgreSQL

Watchlist universe comes from the **pinned first-pass snapshot**, not mutable membership. Progress/status reflect committed scan/run state. Membership edits after a scan starts do not rewrite that scan’s frozen set.

---

## Review findings → resolution

| ID | Finding | Final resolution |
| --- | --- | --- |
| Earlier P1 | Concurrent start could create multiple first-pass jobs | DB-fenced claim + pending job id |
| Earlier P1 | Phase-2 scan attach / KeyError swallow | Phase-2 claim + pending scan + fail-closed 503 |
| Earlier P1 | Evidence pack incomplete pins | `full_two_pass` requires pins; partial → incomplete publishability |
| P1-1 | `already_attached` reused without semantic match | `_phase2_scan_request_mismatches` |
| P1-2 | Crash between job create and pending-id | Pre-allocate durable job id on claim |
| P2 | Watchlist from mutable membership / false “fundamental keeper” | Frozen snapshot + distinct reasons |
| Override P1 | Subset-of-attached override compare wrong under additive semantics | Effective = CS ∪ request |
| Provenance P1 | Fallback to latest / empty on exception | Fail closed: exact pin, 409/503 |

---

## Acceptance matrix (#338–#341)

Status key: **Complete** (implemented + locally verified) · **Partial** (core done; edge/ops open) · **Deferred** (explicitly out of merge) · **Not Verified** (not re-proven this pass)

### #338 — Monthly-first MACD/KD for candidates

| Requirement | Location | Evidence | Status | Follow-up |
| --- | --- | --- | --- | --- |
| Only selected candidates get MACD/KD | `first_pass_durable.create_technical_scan_from_candidate_set`, `scan.py` | unit admission 12/73, 40/300 | Complete | — |
| Monthly before daily; fail → `not_run_by_design` | `scan.py`, `evaluation.py` | `test_monthly_first_338_341.py` | Complete | — |
| Bounded `dailyOverrideIds` | schemas + create path + Phase-2 compare | unit + route tests | Complete | — |
| Incomplete history ≠ pass | readiness / quality paths | unit fixtures | Complete | — |
| Idempotent rerun / multi-pod | claims + scan idempotency key | unit + claims integration | Complete | — |
| Preserve #329/#330 budgets/leases | existing services | unit 329 + integration schedule e2e | Complete | — |
| Live 300/40 soak on k3s | — | — | Deferred | Real-node soak |
| Cache invalidate on candle/version change | scan/history reuse paths | unit coverage partial | Partial | Broader candle-revision soak |

### #339 — Three-tab research center

| Requirement | Location | Evidence | Status | Follow-up |
| --- | --- | --- | --- | --- |
| Three tabs on `/research/tw` | `TwResearchPage.tsx` | vitest + Playwright tabs | Complete | — |
| Expandable rows; no JSON-only UX | page + i18n | vitest | Complete | — |
| Frozen watchlist classifications | `watchlistReasonById` | vitest (membership edit / insufficient) | Complete | — |
| PG-backed phase/progress after reload | run/scan queries | vitest + API | Complete | Multi-browser/pod UI soak Not Verified |
| Pagination / no GET-triggered upstream fetch | page + GET routes | code review + vitest polling | Complete | Huge-universe virtualize still limited |
| Editable membership without mutating in-flight scan | membership + frozen snapshot | vitest | Complete | — |
| Full RTL / every 14-sector name matrix | — | sample ZH names in tests | Partial | Exhaustive sector catalog UI pass |
| Measured FE perf under full universe | — | — | Deferred | k3s/FE soak |

### #340 — Notion-ready evidence / Agent handoff

| Requirement | Location | Evidence | Status | Follow-up |
| --- | --- | --- | --- | --- |
| ZH-TW example fixture labeled non-live | `report_evidence_fixture_zh_tw.json` + UI | unit + Playwright | Complete | — |
| Provenance / digest / honest incompleteness | `report_evidence.py` | unit | Complete | — |
| No Stock Ticker Notion write / shortlist | services + pack flags | code review + unit | Complete | — |
| Two analyst decisions, same snapshot | unit two-decision proof | unit | Complete | External Agent Notion writer out of repo |
| Manifest chunk digest protocol | export / first-pass chunks | existing #322-aligned paths | Complete | Transfer-size cost metrics Deferred |
| External Agent Notion read-back / dedupe | — | — | Deferred | Separate Agent repo |

### #341 — Manual full-research first

| Requirement | Location | Evidence | Status | Follow-up |
| --- | --- | --- | --- | --- |
| Manual start + durable run ledger | `research_run.py`, migrations 0025–0027 | unit + PG claims integration | Complete | — |
| Claim fencing / crash recovery / idempotent HTTP | claims repo + routes | 5 claims integration tests | Complete | — |
| Server-authoritative reconcile / gates | `reconcile`, attach APIs | unit | Complete | — |
| Compatible with technical-first + #329/#330 | schedule e2e integration | 5 passed this pass | Complete | — |
| CronJob remains suspended | `k8s/*cronjob.yaml` | `suspend: true` | Complete | Enablement checklist Deferred |
| Written rollout / capacity checklist | docs | `tw-research-next-338-341.md` + this handoff | Partial | Real Ubuntu/k3s benchmarks Deferred |
| Live k3s cold/warm soak | — | — | Deferred / Not Verified | Ops |

**Issue-closure guidance:** keep issues **OPEN** after merge until deferred ops items (k3s soak, schedule enablement, external Agent Notion path) are explicitly accepted. PR should use `Refs`, not `Closes`.

---

## Migrations `20261112_0025`–`0027`

| Rev | Purpose | Chain |
| --- | --- | --- |
| `0025` | `tw_research_research_runs` table + unique run id + updated index | revises `20261111_0024` |
| `0026` | `revision` OCC column (server_default 0) | revises `0025` |
| `0027` | job/phase2 claim tokens, expiry, pending ids | revises `0026` |

**Review:** Models in `TwResearchResearchRun` match columns; additive-only on research_runs; downgrades drop added columns/table; no unrelated table mutation.

**Executed this pass:** disposable `stocks_test` already at `20261112_0027 (head)` via `alembic current` / `alembic heads`.

---

## Tests executed on HEAD `48ba5024` (this cleanup pass)

```text
cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/ \
  tests/unit/routes/test_tw_research_route.py -q
# 213 passed

cd backend && .venv313/bin/python -m pytest \
  tests/unit/services/tw_research/test_coordination_329.py \
  tests/unit/services/tw_research/test_budget_resume_329.py -q
# 11 passed

ALLOW_DESTRUCTIVE_DB_TESTS=1 \
DATABASE_URL=postgresql+psycopg://henrybro@127.0.0.1:5432/stocks_test \
  .venv313/bin/alembic current
# 20261112_0027 (head)

ALLOW_DESTRUCTIVE_DB_TESTS=1 \
DATABASE_URL=postgresql+psycopg://henrybro@127.0.0.1:5432/stocks_test \
  .venv313/bin/python -m pytest \
  tests/integration/services/test_tw_research_run_claims_341.py \
  tests/integration/services/test_tw_research_schedule_e2e_329_330.py -q
# 10 passed

cd frontend && npm test -- src/views/research/TwResearchPage.test.tsx
# 34 passed

cd frontend && npx tsc --noEmit
# exit 0

cd frontend && PLAYWRIGHT_MOCKS_ONLY=1 \
  npx playwright test tests/e2e/tw-research-tabs.spec.ts
# 3 passed
```

**Not run this pass:** full frontend Playwright suite against live backend; k3s smoke; production migration; ESLint (no `lint` script in frontend package).

**k3s / production performance:** **not measured**.

---

## CI readiness (`.github/workflows/test-and-smoke.yml`)

| Check | Result |
| --- | --- |
| Backend unit + integration on PR | Yes — `stocks_test` service + `ALLOW_DESTRUCTIVE_DB_TESTS=1` |
| Frontend unit (vitest) | Yes |
| Playwright E2E | Yes (fails job on failure) |
| Explicit `tsc --noEmit` | Not in CI (local tsc clean); vitest still runs |
| Alembic via integration fixtures | Exercised when integration tests hit schema |
| No production / paid Azure on PR | Azure job is `workflow_dispatch` on main only |
| CronJob / Notion | Not touched by CI |

No workflow change required for meaningful PR validation.

---

## Operational safeguards (verified)

- `k8s/backend-tw-research-schedule-cronjob.yaml` → `suspend: true`
- `k8s/backend-history-capture-cronjob.yaml` → `suspend: true`
- Stock Ticker does not write Notion; pack flags `stockTickerWritesNotion: false`
- No automatic final-report shortlist
- Request budgets / lease fencing retained (#329/#330 tests green)
- UI distinguishes running / waiting / partial / failed / verified via status + quality

### Still required before schedule enablement

1. Staging apply of `0025`–`0027`
2. Ubuntu/k3s cold/warm soak + headroom documentation
3. Explicit approval to unsuspend CronJobs
4. External ChatGPT/Agent Notion writer validation
5. Optional large-universe UI search/virtualization if operators hit scale limits

---

## Draft PR description

```markdown
## Summary
Manual fundamental-first Taiwan research path for Refs #338, #339, #340, #341:
monthly-first MACD/KD on analyst-selected candidates, durable PostgreSQL research-run
orchestration with DB-fenced claims, three-tab research UI with frozen watchlist,
and Stock Ticker–only evidence export (no Notion publish).

## Scope
- #338: Monthly-then-daily technical evaluation; `not_run_by_design`; bounded daily overrides
- #339: `/research/tw` tabs, phase banner, frozen first-pass watchlist classifications
- #340: Evidence pack + ZH-TW fixture; external analyst owns Notion decisions
- #341: `tw_research_research_runs`, OCC revision, job/scan claim fencing, manual-first

## Architecture notes
- Effective Phase-2 overrides = pinned candidate-set overrides ∪ request overrides
- Managed reuse requires run pin ≡ scan provenance; exact candidate-set version load; 409/503 fail-closed
- Two gates: candidate selection (in-app) and report/Notion (external)

## Migrations
- `20261112_0025` research runs
- `20261112_0026` revision OCC
- `20261112_0027` claim columns

## Test plan
- [ ] CI backend-pytest (unit + integration on stocks_test)
- [ ] CI frontend unit + Playwright
- [ ] Confirm CronJobs remain suspended post-merge
- [ ] Staging alembic upgrade through 0027 before relying on claims

## Known limitations (do not treat as merge blockers)
- k3s performance/soak not measured
- Schedule enablement deferred
- External Notion Agent writer out of repo
- Keep issues open until ops checklist signed off

Refs #338
Refs #339
Refs #340
Refs #341
```

---

## Exact next action

1. Open PR from `issue-338-341-tw-research-next` → `main` when authorized (use draft description above; **Refs**, not Closes).
2. Confirm CI green.
3. Leave CronJobs suspended; leave #338–#341 open for deferred ops acceptance.

## Independent merge-readiness verdict

**READY FOR PR** — no remaining confirmed source-code merge blockers; deferred items are operational / out-of-repo, not PR blockers.
