"""Unit tests for ``rankings_external_source``.

The parsers are exercised against representative HTML / JSON fixtures
matching the shape Yahoo currently returns. The cache layer is tested
through the public ``RankingsExternalSource.fetch`` API using injected
fake fetchers that count calls.
"""

from datetime import datetime, timezone

import pytest

from app.schemas.rankings import RankingType
from app.services import rankings_external_source as module
from app.services.rankings_external_source import (
    RankingsExternalSource,
    _parse_tw_rank_page,
    _parse_us_screener_response,
)


# ---------------------------------------------------------------------------
# TW parser
# ---------------------------------------------------------------------------


def _tw_rank_html(rows: list[tuple[str, str, str, str, str, str, str, str]]) -> str:
    """Build minimal HTML that mimics what tw.stock.yahoo.com/rank/* returns.

    Each tuple = (name, symbol_with_suffix, last_price, change, change_pct,
                  high, low, volume_token).
    """
    body_parts: list[str] = ["<html><body>"]
    body_parts.append("<div>成交量(張)</div><div>成交金額(億)</div>")
    for idx, (name, symbol, price, change, change_pct, high, low, volume) in enumerate(
        rows, start=1
    ):
        # range = high - low (any plausible value; parser only reads it positionally)
        try:
            rng = f"{float(high) - float(low):.2f}"
        except ValueError:
            rng = "0.00"
        body_parts.append(f"<div>{idx}</div>")
        body_parts.append(f"<div>{name}</div>")
        body_parts.append(f"<div>{symbol}</div>")
        body_parts.append(f"<div>{price}</div>")
        body_parts.append(f"<div>{change}</div>")
        body_parts.append(f"<div>{change_pct}</div>")
        body_parts.append(f"<div>{high}</div>")
        body_parts.append(f"<div>{low}</div>")
        body_parts.append(f"<div>{rng}</div>")
        body_parts.append(f"<div>{volume}</div>")
        body_parts.append("<div>0.0</div>")  # turnover
    body_parts.append("</body></html>")
    return "".join(body_parts)


def test_parse_tw_volume_page_returns_signed_change_and_lots_to_shares() -> None:
    html_text = _tw_rank_html(
        [
            ("群創", "3481.TW", "37.50", "2.00", "5.63%", "37.95", "33.85", "1.374M"),
            ("友達", "2409.TW", "20.20", "0.50", "2.42%", "20.30", "19.50", "516,811"),
        ]
    )
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    envelopes = _parse_tw_rank_page(html_text, RankingType.volume, now)

    assert [s.symbol for s, _ in envelopes] == ["3481", "2409"]
    first_stock, first_quote = envelopes[0]
    assert first_stock.market == "TW"
    assert first_stock.exchange == "TWSE"
    assert first_quote.last_price == 37.5
    # Volume page change is unsigned in plain text; volume ranking sign defaults to positive.
    assert first_quote.change == 2.0
    assert first_quote.change_percent == 5.63
    # 1.374M lots * 1000 shares/lot.
    assert first_quote.volume == 1_374_000_000
    # 516,811 lots * 1000 shares/lot.
    assert envelopes[1][1].volume == 516_811_000


def test_parse_tw_volume_page_merges_twse_and_tpex_rows_in_yahoo_order() -> None:
    """Yahoo's default Taiwan rank tables interleave 上市 (.TW) and 上櫃 (.TWO)."""
    html_text = _tw_rank_html(
        [
            ("上市股", "2330.TW", "100.00", "1.00", "1.00%", "101.00", "99.00", "10K"),
            ("上櫃股", "1234.TWO", "50.00", "0.50", "1.01%", "51.00", "49.00", "5K"),
        ]
    )
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    envelopes = _parse_tw_rank_page(html_text, RankingType.volume, now)

    assert [(s.symbol, s.exchange) for s, _ in envelopes] == [
        ("2330", "TWSE"),
        ("1234", "TPEx"),
    ]


def test_fetch_tw_applies_board_filter_with_board_specific_cache_entries() -> None:
    html_mixed = _tw_rank_html(
        [
            ("上市股", "2330.TW", "100.00", "1.00", "1.00%", "101.00", "99.00", "10K"),
            ("上櫃股", "1234.TWO", "50.00", "0.50", "1.01%", "51.00", "49.00", "5K"),
        ]
    )
    fetch_calls = {"n": 0}

    def tw_fetcher(slug: str) -> str:
        fetch_calls["n"] += 1
        return html_mixed

    source = RankingsExternalSource(tw_html_fetcher=tw_fetcher, cache_ttl_seconds=60)
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    twse_only = source.fetch("TW", RankingType.volume, 10, now=now, tw_board="TWSE")
    tpex_only = source.fetch("TW", RankingType.volume, 10, now=now, tw_board="TPEx")
    twse_only_again = source.fetch("TW", RankingType.volume, 10, now=now, tw_board="TWSE")
    tpex_only_again = source.fetch("TW", RankingType.volume, 10, now=now, tw_board="TPEx")

    assert [s.symbol for s, _ in twse_only] == ["2330"]
    assert [s.symbol for s, _ in tpex_only] == ["1234"]
    assert [s.symbol for s, _ in twse_only_again] == ["2330"]
    assert [s.symbol for s, _ in tpex_only_again] == ["1234"]
    assert fetch_calls["n"] == 2


def test_fetch_tw_tpex_filters_after_parsing_later_rows_beyond_first_50() -> None:
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    for idx in range(55):
        symbol = f"{1100 + idx}"
        rows.append(
            (f"上市股{idx}", f"{symbol}.TW", "100.00", "1.00", "1.00%", "101.00", "99.00", "10K")
        )
    for idx in range(20):
        symbol = f"{2100 + idx}"
        rows.append(
            (f"上櫃股{idx}", f"{symbol}.TWO", "50.00", "0.50", "1.01%", "51.00", "49.00", "5K")
        )

    source = RankingsExternalSource(
        tw_html_fetcher=lambda slug: _tw_rank_html(rows),
        cache_ttl_seconds=60,
    )
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    result = source.fetch("TW", RankingType.volume, 10, now=now, tw_board="TPEx")

    assert result is not None
    assert len(result) == 10
    assert [stock.symbol for stock, _ in result] == [str(2100 + idx) for idx in range(10)]
    assert all(stock.exchange == "TPEx" for stock, _ in result)


def test_parse_tw_change_down_page_flips_sign_on_change_and_percent() -> None:
    html_text = _tw_rank_html(
        [
            ("世芯-KY", "3661.TW", "4,780.00", "530.00", "9.98%", "5,135.00", "4,780.00", "3,226"),
        ]
    )
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    envelopes = _parse_tw_rank_page(html_text, RankingType.change_down, now)

    assert len(envelopes) == 1
    _, quote = envelopes[0]
    assert quote.change == -530.0
    assert quote.change_percent == -9.98
    # previous_close = last_price - change = 4780 - (-530) = 5310
    assert quote.previous_close == 5310.0


def test_parse_tw_skips_rows_with_malformed_fields() -> None:
    html_text = _tw_rank_html(
        [
            # Bad percent token
            ("壞資料", "1234.TW", "100.0", "1.0", "not-a-percent", "101", "99", "10"),
            # Good row
            ("好資料", "5678.TWO", "50.0", "0.5", "1.01%", "51", "49", "1.0K"),
        ]
    )
    now = datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc)

    envelopes = _parse_tw_rank_page(html_text, RankingType.volume, now)

    assert [s.symbol for s, _ in envelopes] == ["5678"]
    stock, quote = envelopes[0]
    # 1.0K lots × 1000 shares/lot = 1,000,000 shares.
    assert stock.exchange == "TPEx"
    assert quote.volume == 1_000_000


# ---------------------------------------------------------------------------
# U.S. parser
# ---------------------------------------------------------------------------


def _us_quote_dict(symbol: str, *, price: float, prev: float, vol: int, name: str | None = None):
    return {
        "symbol": symbol,
        "shortName": name or f"{symbol} Inc.",
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev,
        "regularMarketChange": price - prev,
        "regularMarketChangePercent": ((price - prev) / prev) * 100,
        "regularMarketVolume": vol,
        "regularMarketTime": 1778687973,
        "currency": "USD",
        "fullExchangeName": "NasdaqGS",
        "exchange": "NMS",
        "quoteType": "EQUITY",
        "regularMarketOpen": prev,
        "regularMarketDayHigh": price + 1,
        "regularMarketDayLow": prev - 1,
    }


def test_parse_us_screener_response_maps_quotes_in_yahoo_order() -> None:
    response = {
        "quotes": [
            _us_quote_dict("AAA", price=10.0, prev=8.0, vol=1_000_000, name="Top Gainer Co"),
            _us_quote_dict("BBB", price=20.0, prev=18.0, vol=500_000),
        ]
    }
    now = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)

    envelopes = _parse_us_screener_response(response, now)

    assert [s.symbol for s, _ in envelopes] == ["AAA", "BBB"]
    stock, quote = envelopes[0]
    assert stock.market == "US"
    assert stock.exchange == "NASDAQ"
    assert quote.last_price == 10.0
    assert quote.previous_close == 8.0
    assert quote.change == 2.0
    assert round(quote.change_percent, 2) == 25.0
    assert quote.volume == 1_000_000


def test_parse_us_screener_skips_non_equity_quote_types() -> None:
    response = {
        "quotes": [
            _us_quote_dict("AAA", price=10.0, prev=8.0, vol=1_000_000),
            {**_us_quote_dict("ETFX", price=100.0, prev=99.0, vol=50_000), "quoteType": "ETF"},
        ]
    }
    envelopes = _parse_us_screener_response(response, datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc))
    assert [s.symbol for s, _ in envelopes] == ["AAA"]


def test_parse_us_screener_skips_rows_missing_price() -> None:
    response = {
        "quotes": [
            _us_quote_dict("AAA", price=10.0, prev=8.0, vol=1_000_000),
            {"symbol": "BBB", "shortName": "missing prices"},
        ]
    }
    envelopes = _parse_us_screener_response(response, datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc))
    assert [s.symbol for s, _ in envelopes] == ["AAA"]


# ---------------------------------------------------------------------------
# Cache + dispatcher
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """All cache tests run with Redis disabled (in-memory only)."""
    monkeypatch.setattr(module, "_redis_get", lambda key: None)
    monkeypatch.setattr(module, "_redis_setex", lambda key, ttl, value: None)


def test_fetch_returns_none_when_market_unsupported() -> None:
    source = RankingsExternalSource()
    assert source.fetch("XX", RankingType.volume, 10) is None


def test_fetch_returns_none_when_ranking_type_not_pre_rankable() -> None:
    source = RankingsExternalSource()
    assert source.fetch("US", RankingType.volume_up, 10) is None
    assert source.fetch("TW", RankingType.net_buy, 10) is None


def test_fetch_caches_us_response_in_memory_until_ttl_expires() -> None:
    call_count = {"count": 0}

    def fake_us_fetcher(scr_id, count):
        call_count["count"] += 1
        return {
            "quotes": [
                _us_quote_dict("AAA", price=10.0, prev=8.0, vol=1_000_000),
                _us_quote_dict("BBB", price=20.0, prev=18.0, vol=500_000),
            ]
        }

    source = RankingsExternalSource(
        us_screener_fetcher=fake_us_fetcher,
        cache_ttl_seconds=60,
    )
    base = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)

    first = source.fetch("US", RankingType.volume, 5, now=base)
    second = source.fetch("US", RankingType.volume, 5, now=base.replace(minute=0, second=30))

    assert first and second
    assert call_count["count"] == 1, "second call within TTL should reuse cached result"


def test_fetch_refetches_after_ttl_expires() -> None:
    call_count = {"count": 0}

    def fake_us_fetcher(scr_id, count):
        call_count["count"] += 1
        return {
            "quotes": [
                _us_quote_dict("AAA", price=10.0 + call_count["count"], prev=8.0, vol=1_000_000),
            ]
        }

    source = RankingsExternalSource(
        us_screener_fetcher=fake_us_fetcher,
        cache_ttl_seconds=60,
    )
    t0 = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
    t_after_ttl = datetime(2026, 5, 13, 15, 2, tzinfo=timezone.utc)

    source.fetch("US", RankingType.volume, 5, now=t0)
    source.fetch("US", RankingType.volume, 5, now=t_after_ttl)

    assert call_count["count"] == 2


def test_fetch_returns_none_when_fetcher_raises() -> None:
    def raising_fetcher(scr_id, count):
        raise RuntimeError("yahoo down")

    source = RankingsExternalSource(us_screener_fetcher=raising_fetcher)
    assert source.fetch(
        "US",
        RankingType.volume,
        5,
        now=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
    ) is None


def test_fetch_returns_none_when_external_response_is_empty() -> None:
    def empty_fetcher(scr_id, count):
        return {"quotes": []}

    source = RankingsExternalSource(us_screener_fetcher=empty_fetcher)
    assert source.fetch(
        "US",
        RankingType.change_up,
        5,
        now=datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc),
    ) is None


def test_fetch_dispatches_tw_to_html_fetcher() -> None:
    seen_slugs: list[str] = []

    def tw_fetcher(slug):
        seen_slugs.append(slug)
        return _tw_rank_html(
            [("群創", "3481.TW", "37.50", "2.00", "5.63%", "37.95", "33.85", "1.374M")]
        )

    source = RankingsExternalSource(tw_html_fetcher=tw_fetcher)
    result = source.fetch(
        "TW",
        RankingType.change_up,
        5,
        now=datetime(2026, 5, 13, 5, 0, tzinfo=timezone.utc),
    )

    assert seen_slugs == ["change-up"]
    assert result and result[0][0].symbol == "3481"
