from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.ai_providers import AnalysisCitation
from app.services import stock_analysis_redis_cache as cache_module
from app.services.stock_analysis_redis_cache import (
    get_cached_stock_analysis,
    set_cached_stock_analysis,
    stock_analysis_cache_key,
)

BUCKET = "env:openai:gpt"
ANALYZED_AT = datetime(2026, 9, 11, 4, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """In-memory stand-in so cache behavior is tested, not just key strings."""
    store: dict[str, str] = {}

    monkeypatch.setattr(cache_module.settings, "redis_url", "redis://test", raising=False)
    monkeypatch.setattr(
        cache_module.settings,
        "stock_analysis_cache_ttl_seconds",
        3600,
        raising=False,
    )
    monkeypatch.setattr(cache_module, "_redis_get", store.get)
    monkeypatch.setattr(
        cache_module,
        "_redis_setex",
        lambda key, _ttl, value: store.__setitem__(key, value),
    )
    return store


def _write(language: str, analysis: str, **overrides) -> None:
    set_cached_stock_analysis(
        "user-1",
        "US:AAPL",
        BUCKET,
        language,
        analyzed_at=overrides.get("analyzed_at", ANALYZED_AT),
        ai_provider="openai",
        ai_model="gpt-4o-mini",
        ai_analysis=analysis,
        citations=overrides.get("citations", ()),
        search_results=(),
        recent_news_context=overrides.get("recent_news_context"),
    )


def test_stock_analysis_cache_key_includes_language() -> None:
    en_key = stock_analysis_cache_key("user-1", "US:AAPL", BUCKET, "en")
    zh_key = stock_analysis_cache_key("user-1", "US:AAPL", BUCKET, "zh-TW")
    assert en_key != zh_key
    assert en_key.endswith(":en")
    assert zh_key.endswith(":zh-TW")
    assert "user-1:US:AAPL:env:openai:gpt" in en_key


def test_reading_one_language_never_returns_the_other(fake_redis: dict[str, str]) -> None:
    _write("en", "English narrative")

    assert get_cached_stock_analysis("user-1", "US:AAPL", BUCKET, "zh-TW") is None

    cached_en = get_cached_stock_analysis("user-1", "US:AAPL", BUCKET, "en")
    assert cached_en is not None
    assert cached_en.ai_analysis == "English narrative"
    assert cached_en.language == "en"


def test_languages_do_not_overwrite_each_other(fake_redis: dict[str, str]) -> None:
    _write("en", "English narrative")
    _write("zh-TW", "繁體中文分析")

    cached_en = get_cached_stock_analysis("user-1", "US:AAPL", BUCKET, "en")
    cached_zh = get_cached_stock_analysis("user-1", "US:AAPL", BUCKET, "zh-TW")

    assert cached_en is not None and cached_en.ai_analysis == "English narrative"
    assert cached_zh is not None and cached_zh.ai_analysis == "繁體中文分析"


def test_news_snapshot_and_citations_survive_the_round_trip(
    fake_redis: dict[str, str],
) -> None:
    snapshot = {
        "available": True,
        "windowDays": 7,
        "items": [{"id": "N1", "title": "Headline", "url": "https://example.com/a"}],
    }
    _write(
        "en",
        "English narrative",
        recent_news_context=snapshot,
        citations=(
            AnalysisCitation(
                url="https://example.com/a",
                title="Headline",
                source="Example",
                published_at="2026-09-10T00:00:00Z",
            ),
        ),
    )

    cached = get_cached_stock_analysis("user-1", "US:AAPL", BUCKET, "en")

    assert cached is not None
    assert cached.recent_news_context == snapshot
    assert cached.analyzed_at == ANALYZED_AT
    assert len(cached.citations) == 1
    assert cached.citations[0].published_at == "2026-09-10T00:00:00Z"
    assert cached.citations[0].source == "Example"
