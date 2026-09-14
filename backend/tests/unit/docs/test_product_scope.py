"""Guardrails for the product-scope document (#2)."""

from __future__ import annotations

from tests.repo_root import repo_root


_REPO = repo_root()
_SCOPE = _REPO / "docs" / "product-scope.md"
_README = _REPO / "README.md"
_MULTI_USER = _REPO / "docs" / "multi-user-design.md"
_LOCAL_DEV = _REPO / "docs" / "local-development.md"
_ARCHITECTURE = _REPO / "docs" / "backend-architecture.md"


def test_product_scope_doc_exists_and_stays_concise() -> None:
    assert _SCOPE.is_file()
    lines = _SCOPE.read_text(encoding="utf-8").splitlines()
    assert 100 <= len(lines) <= 180, f"expected 100–180 lines, got {len(lines)}"


def test_product_scope_covers_retail_purpose_and_boundaries() -> None:
    text = _SCOPE.read_text(encoding="utf-8")
    assert "retail investor" in text
    assert "散戶" in text
    assert "brokerage" in text.lower()
    assert "automated trading" in text.lower()
    assert "financial advice" in text.lower()
    assert "price_above" in text and "price_below" in text
    assert "email" in text.lower() or "push" in text.lower()
    assert "`US:AAPL`" in text and "`TW:2330`" in text
    assert "TWSE" in text and "TPEx" in text
    assert "/api/stream" in text
    assert "3 seconds" in text
    assert "REDIS_URL" in text
    assert "in-memory" in text.lower()
    assert "Stock AI" in text
    assert "Portfolio AI" in text
    assert "does **not** follow the UI language" in text
    assert "zh-TW" in text
    assert "Yahoo Finance" in text
    assert "Google News" in text
    assert "same candles the chart plots" in text
    assert "yfinance" in text.lower()
    assert "fallback" in text.lower()
    assert "quote retry" in text.lower()
    assert "refresh=true" in text
    assert "on-demand portfolio AI" in text
    assert "API error messages stay in English" in text
    assert "chrome" not in text.lower()
    assert "cumulative dividends" in text
    assert "tax-lot" in text.lower()
    assert "AUTH_ENABLED=false" in text
    assert "[multi-user-design.md](multi-user-design.md)" in text
    assert "[local-development.md](local-development.md)" in text
    assert "not current behavior" in text.lower()


def test_product_scope_refresh_table_distinguishes_mechanisms() -> None:
    text = _SCOPE.read_text(encoding="utf-8")
    for heading in (
        "Request / on-demand",
        "Background quote refresher",
        "WebSocket `/api/stream`",
        "Scheduled jobs",
        "Manual refresh",
        "Cached AI narrative",
    ):
        assert heading in text, f"missing refresh row {heading!r}"


def test_readme_links_product_scope_and_drops_stale_mvp_copy() -> None:
    readme = _README.read_text(encoding="utf-8")
    assert readme.startswith("# Stock Ticker\n")
    assert "Stock Ticker MVP" not in readme
    assert "U.S. symbols display in English; Taiwan symbols in Traditional Chinese." not in readme
    assert "](docs/product-scope.md)" in readme
    assert "English" in readme and "Traditional Chinese" in readme


def test_focused_docs_link_product_scope_instead_of_restating_it() -> None:
    local_dev = _LOCAL_DEV.read_text(encoding="utf-8")
    architecture = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "[product-scope.md](product-scope.md)" in local_dev
    assert "[product-scope.md](product-scope.md)" in architecture
    assert "using yfinance history + backend metrics" not in local_dev
    assert "same candles the chart plots" in local_dev


def test_multi_user_design_lists_realized_trades() -> None:
    text = _MULTI_USER.read_text(encoding="utf-8")
    assert "`realized_trades.user_id`" in text
    assert "/api/realized-trades" in text
