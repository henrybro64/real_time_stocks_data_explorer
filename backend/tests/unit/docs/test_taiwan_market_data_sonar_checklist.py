from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docs").is_dir() and (candidate / "backend").is_dir():
            return candidate
    raise AssertionError(f"could not locate repo root from {here}")


_REPO_ROOT = _repo_root()
_CHECKLIST = _REPO_ROOT / "docs" / "taiwan-market-data-sonar-checklist.md"
_PR_TEMPLATE = _REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
_README = _REPO_ROOT / "README.md"
_ARCHITECTURE = _REPO_ROOT / "docs" / "backend-architecture.md"

# Paths the owner checklist tells implementers to run. Keep this list in sync
# with docs/taiwan-market-data-sonar-checklist.md so CI fails if either drifts.
_CHECKLIST_TEST_PATHS = (
    "backend/tests/unit/services/test_taiwan_market_data.py",
    "backend/tests/unit/services/test_taiwan_history_scan.py",
    "backend/tests/unit/services/test_tw_institutional_service.py",
    "backend/tests/unit/routes/test_tw_institutional_route.py",
    "backend/tests/unit/services/test_tw_margin_short_service.py",
    "backend/tests/unit/routes/test_tw_margin_short_route.py",
    "backend/tests/unit/services/test_http_client.py",
    "backend/tests/unit/services/test_taiwan_market_data_refresh_service.py",
    "backend/tests/unit/repositories/test_taiwan_market_daily_repository.py",
    "backend/tests/unit/db/test_taiwan_market_daily_migration.py",
    "backend/tests/unit/routes/test_taiwan_market_data_route.py",
    "backend/tests/unit/jobs/test_jobs_runtime.py",
    "backend/tests/integration/api/test_taiwan_market_data_api.py",
    "backend/tests/integration/api/test_tw_margin_short_api.py",
    "backend/tests/integration/api/test_stock_detail_api.py",
)


def test_taiwan_market_data_sonar_checklist_exists_with_owner_anchors() -> None:
    """Issue #108: keep a reusable Sonar owner checklist for Taiwan data PRs."""
    assert _CHECKLIST.is_file(), f"missing checklist at {_CHECKLIST}"
    text = _CHECKLIST.read_text(encoding="utf-8")

    required_anchors = [
        "SonarCloud",
        "shares_to_lots",
        "collect_dated_history",
        "Deferred Sonar findings",
        "PR #104",
        "f953f40",
        "976501b",
        "test_tw_institutional_service.py",
        "test_tw_margin_short_service.py",
        "test_taiwan_market_data_refresh_service.py",
        "test_taiwan_market_data_route.py",
        "test_jobs_runtime.py",
        "test_taiwan_market_data_api.py",
        "test_tw_margin_short_api.py",
        "test_stock_detail_api.py",
        "henrybro64_real_time_stocks_data_explorer",
        "No deferred Sonar debt",
    ]
    missing = [anchor for anchor in required_anchors if anchor not in text]
    assert missing == [], f"checklist missing anchors: {missing}"


def test_checklist_referenced_test_files_exist_on_disk() -> None:
    """Checklist CI commands must point at real test modules (no stale paths)."""
    text = _CHECKLIST.read_text(encoding="utf-8")
    missing_from_doc: list[str] = []
    missing_on_disk: list[str] = []
    for relative in _CHECKLIST_TEST_PATHS:
        if relative not in text:
            missing_from_doc.append(relative)
        if not (_REPO_ROOT / relative).is_file():
            missing_on_disk.append(relative)
    assert missing_from_doc == [], f"checklist omits expected test paths: {missing_from_doc}"
    assert missing_on_disk == [], f"checklist paths missing on disk: {missing_on_disk}"

    # Also catch any other backend/tests/... paths that appear in the doc but vanished.
    referenced = sorted(set(re.findall(r"backend/tests/[\w./-]+\.py", text)))
    stale = [path for path in referenced if not (_REPO_ROOT / path).is_file()]
    assert stale == [], f"checklist references missing files: {stale}"


def test_pull_request_template_links_taiwan_sonar_checklist() -> None:
    assert _PR_TEMPLATE.is_file(), f"missing PR template at {_PR_TEMPLATE}"
    text = _PR_TEMPLATE.read_text(encoding="utf-8")
    # PR descriptions resolve relative links under /pull/<n>/, so the template must
    # use an absolute blob URL (not docs/... or ../docs/...).
    assert (
        "](https://github.com/henrybro64/real_time_stocks_data_explorer/blob/main/"
        "docs/taiwan-market-data-sonar-checklist.md)"
    ) in text
    assert "](docs/taiwan-market-data-sonar-checklist.md)" not in text
    assert "../docs/" not in text
    assert "SonarCloud" in text
    assert "Deferred" in text or "deferred" in text


def test_readme_and_architecture_link_taiwan_sonar_checklist() -> None:
    readme = _README.read_text(encoding="utf-8")
    architecture = _ARCHITECTURE.read_text(encoding="utf-8")
    # Single README link under the Taiwan market-data section (avoid duplicate Structure blurb).
    assert readme.count("](docs/taiwan-market-data-sonar-checklist.md)") == 1
    assert "taiwan-market-data-sonar-checklist.md" in architecture
    assert "SonarCloud" in readme
    assert "SonarCloud" in architecture


def test_checklist_treats_cleanup_shas_as_illustrative() -> None:
    text = _CHECKLIST.read_text(encoding="utf-8")
    assert "Illustrative" in text
    assert "may age" in text
    assert "failure-mode table" in text
    # #104 acceptance trail remains documented, but is labeled as historical.
    assert "acceptance trail" in text or "not a living required set" in text
