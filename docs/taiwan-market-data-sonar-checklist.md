# Taiwan market-data SonarCloud checklist

Owner checklist for backend PRs that touch Taiwan market-data paths
(institutional / 資券 / ownership / profitability / refresh jobs / shared Taiwan helpers).

**Why:** functional CI (`backend-pytest`, Playwright, k3s) can be green while
SonarCloud still fails the quality gate on maintainability, duplication,
reliability, or new-code coverage. That happened on
[PR #104](https://github.com/henrybro64/real_time_stocks_data_explorer/pull/104)
before follow-up commits cleared the gate.

SonarCloud project dashboard (replace `N` with the PR number):

```text
https://sonarcloud.io/dashboard?id=henrybro64_real_time_stocks_data_explorer&pullRequest=N
```

SonarCloud runs as a separate GitHub check (`SonarCloud Code Analysis`), not as a job inside
`.github/workflows/test-and-smoke.yml`. Green pytest/Playwright/k3s does **not** imply a green
Sonar gate — open the PR Checks tab and the dashboard link above.

---

## When this applies

Use this checklist for PRs that change any of:

| Area | Typical paths |
| --- | --- |
| Shared Taiwan helpers | `backend/app/services/taiwan_market_data.py`, `taiwan_history_scan.py`, `http_client.py` |
| Institutional API | `tw_institutional_service.py`, `api/routes/tw_institutional.py` |
| 資券 API | `tw_margin_short_service.py`, `api/routes/tw_margin_short.py`, `docs/taiwan-margin-short-endpoints.md` |
| Ownership / 籌碼 | `tw_ownership_concentration_service.py`, `tdcc_shareholding.py`, `taiwan_signals_redis_cache.py`, `api/routes/tw_ownership_concentration.py` |
| Profitability / 財報三率 | `tw_profitability_service.py`, `api/routes/tw_profitability.py` |
| Refresh / store | `taiwan_market_data_refresh_service.py`, `taiwan_market_daily_repository.py`, related migrations |
| Jobs / settings | `backend/app/jobs/runtime.py`, `core/config.py` Taiwan refresh flags |

Frontend-only UI work is out of scope for this checklist (see issue #108).

---

## Pre-merge checklist

Copy into the PR description (or tick here while reviewing):

- [ ] Open the SonarCloud PR dashboard and **list every failed condition** (duplication, reliability, coverage on new code, code smells).
- [ ] Prefer shared helpers from `taiwan_market_data.py` / `taiwan_history_scan.py`:
  - `parse_taiwan_number`, `normalize_taiwan_date`, `normalize_taiwan_symbol`
  - `shares_to_lots` / `taiwan_shares_per_lot` (do **not** hardcode `/1000`)
  - `is_taiwan_no_data_payload`, `fetch_twse_data` / `fetch_tpex_data`
  - `collect_dated_history` / result-cache helpers for weekday walks
- [ ] Avoid deep nested parse/fetch condition chains; extract pure row/payload parsers.
- [ ] Do not copy conversion or calendar-walk helpers into a second service module.
- [ ] New service branches have focused unit tests for **success**, **empty / no-data**, **malformed payload**, and **error / upstream failure** paths.
- [ ] Fix cheap Sonar findings in the **same PR** (duplication, obvious smells, missing branch tests).
- [ ] If a finding is intentionally deferred, document it in the PR with rationale + Sonar link (do not leave a silent red gate).
- [ ] After pushing fixes, confirm **SonarCloud Code Analysis** is green on the PR checks tab.

---

## Minimum test coverage for Taiwan backend changes

Run the suites that match what you touched (from repo root):

```bash
# Shared helpers / lot conversion / history scan
python -m pytest backend/tests/unit/services/test_taiwan_market_data.py \
  backend/tests/unit/services/test_taiwan_history_scan.py -q

# Institutional
python -m pytest backend/tests/unit/services/test_tw_institutional_service.py \
  backend/tests/unit/routes/test_tw_institutional_route.py -q

# 資券
python -m pytest backend/tests/unit/services/test_tw_margin_short_service.py \
  backend/tests/unit/routes/test_tw_margin_short_route.py \
  backend/tests/unit/services/test_http_client.py -q

# Ownership / 籌碼 + profitability / 財報三率
python -m pytest backend/tests/unit/services/test_tw_ownership_concentration_service.py \
  backend/tests/unit/services/test_tdcc_shareholding.py \
  backend/tests/unit/routes/test_tw_ownership_concentration_route.py \
  backend/tests/unit/services/test_tw_profitability_service.py \
  backend/tests/unit/routes/test_tw_profitability_route.py \
  backend/tests/unit/routes/test_tw_profitability_http.py -q

# Refresh + store + capture route
python -m pytest backend/tests/unit/services/test_taiwan_market_data_refresh_service.py \
  backend/tests/unit/repositories/test_taiwan_market_daily_repository.py \
  backend/tests/unit/db/test_taiwan_market_daily_migration.py \
  backend/tests/unit/routes/test_taiwan_market_data_route.py \
  backend/tests/unit/jobs/test_jobs_runtime.py -q
```

When `DATABASE_URL` is available (CI always has it for `backend-pytest`), also exercise the integration paths that touch these features:

```bash
python -m pytest \
  backend/tests/integration/api/test_taiwan_market_data_api.py \
  backend/tests/integration/api/test_tw_margin_short_api.py \
  backend/tests/integration/api/test_stock_detail_api.py -q
```

Add or extend tests when you introduce new branches. Prefer fixtures/mocks; do not depend on live TWSE/TPEx websites in unit tests.

---

## Common Sonar failure modes (and cheap fixes)

| Symptom | Typical cause | Fix pattern used in this repo |
| --- | --- | --- |
| Duplicated lines across TWSE/TPEx services | Copied calendar walk, cache, or lot conversion | Extract shared helper (`taiwan_history_scan`, `shares_to_lots`) |
| Cognitive complexity / nested loops | Inline parse + fetch + filter in one method | Pure `_parse_*_row` / `_parse_all_*` functions |
| Reliability findings | Float `==` in tests, bare broad catches without intent | `pytest.approx`, `# noqa: BLE001` only with a short rationale |
| Coverage on new code | Happy-path-only tests | Add empty / malformed / HttpFetchError cases |
| Identical repository / migration boilerplate | Two near-copy daily tables | Shared base helpers (see Taiwan daily repository/migration collapse commits) |

**Illustrative** Sonar cleanup commits (patterns to reuse; SHAs are historical examples and may age):
`f953f40`, `976501b`, `5b6ffd0`, `5fc7bb8`, `21d0b3c`, `0ce3e79`, `fe9b615`, `baf22d2`, `a219d67`.
Prefer the failure-mode table above over hunting for these hashes on every PR.

---

## PR #104 acceptance trail (historical)

[PR #104](https://github.com/henrybro64/real_time_stocks_data_explorer/pull/104) initially failed Sonar while functional jobs were green. Follow-ups cleared the gate — illustrative commits (not a living required set): [`f953f40`](https://github.com/henrybro64/real_time_stocks_data_explorer/commit/f953f40), [`976501b`](https://github.com/henrybro64/real_time_stocks_data_explorer/commit/976501b). Final merge: SonarCloud **pass**, pytest/Playwright/k3s **pass**. **No deferred Sonar debt** was left open for that PR.

---

## Deferred findings template

If you must merge with an accepted Sonar exception (rare), add this block to the PR body:

```markdown
### Deferred Sonar findings
- Condition: <e.g. Duplicated Lines on new code>
- Dashboard: https://sonarcloud.io/dashboard?id=henrybro64_real_time_stocks_data_explorer&pullRequest=<N>
- Rationale: <why fixing in-PR is not practical>
- Follow-up: <issue link or "none — accepted risk">
```

Prefer fixing in-PR when the change is a local copy, nested parser, or missing branch test.
