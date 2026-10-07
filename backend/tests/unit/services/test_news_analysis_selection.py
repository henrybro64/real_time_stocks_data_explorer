from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.stock import StockRecord
from app.services.news_service import (
    NewsItem,
    select_news_for_analysis,
)
from tests.support import stock_record


NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)


def _apple() -> StockRecord:
    return stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )


def _ai_ticker() -> StockRecord:
    return stock_record(
        "US:AI",
        market="US",
        symbol="AI",
        name="C3.ai Inc.",
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        name_en="C3.ai Inc.",
    )


def _item(
    *,
    title: str,
    url: str = "https://example.com/article",
    source: str = "Example",
    published_at: datetime | str,
) -> NewsItem:
    if isinstance(published_at, datetime):
        published = published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    else:
        published = published_at
    return NewsItem(
        title=title,
        url=url,
        source=source,
        published_at=published,
    )


def test_selects_relevant_items_within_seven_days() -> None:
    stock = _apple()
    recent = _item(
        title="Apple Inc. unveils new product line",
        published_at=NOW - timedelta(days=2),
    )
    older = _item(
        title="Apple Inc. older headline outside window",
        published_at=NOW - timedelta(days=10),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [recent, older]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.selected_count == 1
    assert result.window_days == 7
    assert result.items[0].id == "N1"
    assert result.items[0].title == recent.title
    assert result.window_start == (NOW - timedelta(days=7)).isoformat().replace(
        "+00:00", "Z"
    )
    assert result.window_end == NOW.isoformat().replace("+00:00", "Z")
    assert result.data_as_of == NOW.isoformat().replace("+00:00", "Z")
    assert result.failure_reason is None


def test_falls_back_to_thirty_day_window_when_seven_day_empty() -> None:
    stock = _apple()
    older = _item(
        title="AAPL and Apple Inc. report quarterly results",
        published_at=NOW - timedelta(days=20),
    )
    calls: list[int] = []

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        calls.append(window_days)
        return [older]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    # One upstream round only; the 7-day preference is applied in memory.
    assert calls == [30]
    assert result.available is True
    assert result.selected_count == 1
    assert result.window_days == 30
    assert result.window_start == (NOW - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )
    assert result.items[0].title == older.title


def test_fetch_failure_is_not_retried_with_a_second_window() -> None:
    stock = _apple()
    calls: list[int] = []

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        calls.append(window_days)
        raise TimeoutError("upstream stalled")

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert calls == [30]
    assert result.available is False
    assert result.failure_reason == "fetch_failed"


def test_drops_syndicated_copies_of_the_same_headline() -> None:
    stock = _apple()
    items = [
        _item(
            title="Apple Inc. beats quarterly revenue estimates - Reuters",
            url="https://reuters.example.com/a",
            source="Reuters",
            published_at=NOW - timedelta(days=1),
        ),
        _item(
            title="Apple Inc. beats quarterly revenue estimates - Bloomberg",
            url="https://bloomberg.example.com/b",
            source="Bloomberg",
            published_at=NOW - timedelta(days=1, hours=1),
        ),
        _item(
            title="Apple Inc. names a new chief financial officer",
            url="https://example.com/c",
            source="Example",
            published_at=NOW - timedelta(days=2),
        ),
    ]

    result = select_news_for_analysis(
        stock,
        now=NOW,
        fetch_items=lambda _stock, *, window_days: items,
    )

    assert result.selected_count == 2
    titles = [item.title for item in result.items]
    assert "Apple Inc. names a new chief financial officer" in titles
    assert sum("beats quarterly revenue estimates" in title for title in titles) == 1


def test_falls_back_when_seven_day_items_are_not_relevant() -> None:
    stock = _apple()
    unrelated = _item(
        title="Global markets rally on rate hopes",
        published_at=NOW - timedelta(days=1),
    )
    older_relevant = _item(
        title="Apple Inc. expands services revenue",
        published_at=NOW - timedelta(days=14),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        if window_days == 7:
            return [unrelated]
        return [unrelated, older_relevant]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.selected_count == 1
    assert result.items[0].title == older_relevant.title
    assert result.window_start == (NOW - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )


def test_limits_selection_to_five_items() -> None:
    stock = _apple()
    items = [
        _item(
            title=f"Apple Inc. headline number {index}",
            url=f"https://example.com/{index}",
            published_at=NOW - timedelta(hours=index),
        )
        for index in range(8)
    ]

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return items

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.fetched_count == 8
    assert result.selected_count == 5
    assert [item.id for item in result.items] == ["N1", "N2", "N3", "N4", "N5"]


def test_short_ticker_requires_name_match() -> None:
    stock = _ai_ticker()
    symbol_only = _item(
        title="Why AI stocks surged this week",
        published_at=NOW - timedelta(days=1),
    )
    with_name = _item(
        title="C3.ai Inc. AI platform wins enterprise deal",
        published_at=NOW - timedelta(days=2),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [symbol_only, with_name]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.selected_count == 1
    assert result.items[0].title == with_name.title


def test_symbol_match_allowed_for_longer_tickers() -> None:
    stock = _apple()
    symbol_only = _item(
        title="AAPL climbs after supplier update",
        published_at=NOW - timedelta(days=1),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [symbol_only]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.selected_count == 1
    assert result.items[0].title == symbol_only.title


def test_orders_by_stronger_match_then_newer_published_at() -> None:
    stock = _apple()
    weaker_newer = _item(
        title="AAPL ticks higher in afternoon trade",
        published_at=NOW - timedelta(hours=1),
    )
    stronger_older = _item(
        title="Apple Inc. AAPL announces buyback plan",
        published_at=NOW - timedelta(days=3),
    )
    stronger_newer = _item(
        title="Apple Inc. AAPL ships record iPhones",
        published_at=NOW - timedelta(hours=2),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [weaker_newer, stronger_older, stronger_newer]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert [item.title for item in result.items] == [
        stronger_newer.title,
        stronger_older.title,
        weaker_newer.title,
    ]


def test_deduplicates_normalized_titles() -> None:
    stock = _apple()
    first = _item(
        title="Apple Inc. beats estimates!!!",
        url="https://example.com/a",
        published_at=NOW - timedelta(hours=3),
    )
    duplicate = _item(
        title="apple inc beats estimates",
        url="https://example.com/b",
        published_at=NOW - timedelta(hours=1),
    )
    other = _item(
        title="Apple Inc. expands retail footprint",
        url="https://example.com/c",
        published_at=NOW - timedelta(hours=2),
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [first, duplicate, other]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.selected_count == 2
    titles = {item.title for item in result.items}
    assert first.title in titles or duplicate.title in titles
    assert other.title in titles
    assert len(titles) == 2


def test_excludes_invalid_url_title_and_date() -> None:
    stock = _apple()
    valid = _item(
        title="Apple Inc. opens new store",
        published_at=NOW - timedelta(days=1),
    )
    invalids = [
        _item(title="   ", published_at=NOW - timedelta(days=1)),
        _item(
            title="Apple Inc. bad url",
            url="ftp://example.com/bad",
            published_at=NOW - timedelta(days=1),
        ),
        _item(
            title="Apple Inc. missing date",
            published_at="",
        ),
        _item(
            title="Apple Inc. invalid date",
            published_at="not-a-date",
        ),
    ]

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [valid, *invalids]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.selected_count == 1
    assert result.items[0].title == valid.title
    assert result.items[0].url.startswith("https://")


def test_empty_fetch_returns_soft_unavailable() -> None:
    stock = _apple()

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return []

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is False
    assert result.items == ()
    assert result.failure_reason == "empty"
    assert result.window_start is None
    assert result.window_end is None
    assert result.data_as_of is None


def test_fetch_exception_returns_soft_unavailable() -> None:
    stock = _apple()

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        raise TimeoutError("news timed out")

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is False
    assert result.failure_reason == "fetch_failed"
    assert result.selected_count == 0


def test_prompt_payload_shape_when_available() -> None:
    stock = _apple()
    item = _item(
        title="Apple Inc. launches product",
        published_at=NOW - timedelta(days=1),
        source="Reuters",
        url="https://example.com/apple",
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [item]

    payload = select_news_for_analysis(
        stock, now=NOW, fetch_items=fetch_items
    ).to_prompt_payload()

    context = payload["recentNewsContext"]
    assert context["available"] is True
    assert context["windowStart"] == (NOW - timedelta(days=7)).isoformat().replace(
        "+00:00", "Z"
    )
    assert context["windowEnd"] == NOW.isoformat().replace("+00:00", "Z")
    assert context["dataAsOf"] == NOW.isoformat().replace("+00:00", "Z")
    assert context["failureReason"] is None
    assert context["items"] == [
        {
            "id": "N1",
            "title": item.title,
            "source": "Reuters",
            "publishedAt": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "url": "https://example.com/apple",
        }
    ]


def test_prompt_payload_shape_when_unavailable() -> None:
    stock = _apple()

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return []

    payload = select_news_for_analysis(
        stock, now=NOW, fetch_items=fetch_items
    ).to_prompt_payload()

    assert payload == {
        "recentNewsContext": {
            "available": False,
            "windowDays": None,
            "windowStart": None,
            "windowEnd": None,
            "dataAsOf": None,
            "items": [],
            "failureReason": "empty",
        }
    }


def test_taiwan_name_zh_tw_match() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="TSMC",
        name_zh_tw="台積電",
    )
    item = _item(
        title="台積電擴大先進製程產能",
        published_at=NOW - timedelta(days=1),
        source="經濟日報",
    )

    def fetch_items(_stock, *, window_days: int) -> list[NewsItem]:
        return [item]

    result = select_news_for_analysis(stock, now=NOW, fetch_items=fetch_items)

    assert result.available is True
    assert result.items[0].title == item.title


def test_later_company_query_is_used_when_first_query_is_irrelevant(monkeypatch) -> None:
    stock = _apple()
    queries: list[str] = []

    def fake_fetch(query, **_kwargs):
        queries.append(query)
        if query == "AAPL stock":
            return [
                _item(
                    title="Unrelated market wrap with no company mention",
                    url="https://example.com/unrelated",
                    published_at=NOW - timedelta(days=1),
                )
            ]
        return [
            _item(
                title="Apple Inc. launches a new product line",
                url="https://example.com/apple-launch",
                published_at=NOW - timedelta(days=1),
            )
        ]

    monkeypatch.setattr("app.services.news_service._fetch_google_news", fake_fetch)

    result = select_news_for_analysis(stock, now=NOW)

    assert len(queries) >= 2
    assert result.available is True
    assert result.items[0].title == "Apple Inc. launches a new product line"
