from datetime import timedelta

import pytest

from app.services import taiwan_market_data
from app.services.http_client import HttpFetchError
from app.services.taiwan_market_data import TaiwanMarketDataClient
from tests.support import stock_record


class FakeQuoteSource:
    def __init__(self, *, stock=None, stocks=None):
        self._stock = stock
        self._stocks = list(stocks or [])

    def get_stock(self, stock_id: str):
        if self._stock is not None and self._stock.id == stock_id:
            return self._stock
        for stock in self._stocks:
            if stock.id == stock_id:
                return stock
        return None

    def list_stocks(self):
        return list(self._stocks)


def test_parse_taiwan_number_handles_commas_and_negative_signs() -> None:
    assert taiwan_market_data.parse_taiwan_number("1,234,567") == 1234567.0
    assert taiwan_market_data.parse_taiwan_number("-98,765") == -98765.0
    assert taiwan_market_data.parse_taiwan_number("(12,000)") == -12000.0
    assert taiwan_market_data.parse_taiwan_number("1，234") == 1234.0
    assert taiwan_market_data.parse_taiwan_number("--") is None
    assert taiwan_market_data.parse_taiwan_number("-") is None
    assert taiwan_market_data.parse_taiwan_number("") is None
    assert taiwan_market_data.parse_taiwan_number("N/A") is None
    assert taiwan_market_data.parse_taiwan_number("bad-value") is None
    assert taiwan_market_data.parse_taiwan_number(True) is None
    assert taiwan_market_data.parse_taiwan_number(float("nan")) is None


def test_normalize_taiwan_date_supports_roc_and_gregorian_formats() -> None:
    assert taiwan_market_data.normalize_taiwan_date("115/07/09") == "2026-07-09"
    assert taiwan_market_data.normalize_taiwan_date("2026/07/09") == "2026-07-09"
    assert taiwan_market_data.normalize_taiwan_date("2026-07-09") == "2026-07-09"
    assert taiwan_market_data.normalize_taiwan_date("20260709") == "2026-07-09"
    assert taiwan_market_data.normalize_taiwan_date("1150709") == "2026-07-09"
    assert taiwan_market_data.normalize_taiwan_date("--") is None
    assert taiwan_market_data.normalize_taiwan_date("not-a-date") is None
    assert taiwan_market_data.normalize_taiwan_date("115/13/40") is None


def test_shares_to_lots_divides_by_one_thousand() -> None:
    assert taiwan_market_data.shares_to_lots("123,000") == 123.0
    assert taiwan_market_data.shares_to_lots(2500) == 2.5
    assert taiwan_market_data.shares_to_lots(-4500) == -4.5
    assert taiwan_market_data.shares_to_lots("--") is None


def test_normalize_taiwan_symbol_removes_prefix_and_yahoo_suffix() -> None:
    assert taiwan_market_data.normalize_taiwan_symbol("TW:2330") == "2330"
    assert taiwan_market_data.normalize_taiwan_symbol("2330.TW") == "2330"
    assert taiwan_market_data.normalize_taiwan_symbol("6488.TWO") == "6488"
    assert taiwan_market_data.normalize_taiwan_symbol(" 2330 ") == "2330"
    assert taiwan_market_data.normalize_taiwan_symbol("") is None


def test_taiwan_field_helpers_support_twse_like_row_fixture() -> None:
    fields = ["證券代號", "證券名稱", "三大法人買賣超股數", "日期"]
    row = ["2330", "台積電", "1,234,000", "115/07/09"]

    assert taiwan_market_data.taiwan_field_index(fields, "三大法人買賣超股數") == 2
    assert taiwan_market_data.taiwan_row_value(row, fields, "證券代號") == "2330"
    assert (
        taiwan_market_data.parse_taiwan_number(
            taiwan_market_data.taiwan_row_value(row, fields, "三大法人買賣超股數")
        )
        == 1234000.0
    )
    assert (
        taiwan_market_data.normalize_taiwan_date(
            taiwan_market_data.taiwan_row_value(row, fields, "日期")
        )
        == "2026-07-09"
    )
    assert taiwan_market_data.shares_to_lots(
        taiwan_market_data.taiwan_row_value(row, fields, "三大法人買賣超股數")
    ) == 1234.0


def test_taiwan_field_helpers_support_tpex_like_row_fixture() -> None:
    fields = ["代號", "名稱", "三大法人買賣超股數合計", "資料日期"]
    row = ["6488", "環球晶", "-250,000", "115/07/09"]

    assert (
        taiwan_market_data.taiwan_field_index(
            fields, "三大法人買賣超股數合計", "三大法人買賣超股數"
        )
        == 2
    )
    assert taiwan_market_data.taiwan_row_value(row, fields, "代號") == "6488"
    assert (
        taiwan_market_data.parse_taiwan_number(
            taiwan_market_data.taiwan_row_value(row, fields, "三大法人買賣超股數合計")
        )
        == -250000.0
    )
    assert (
        taiwan_market_data.normalize_taiwan_date(
            taiwan_market_data.taiwan_row_value(row, fields, "資料日期")
        )
        == "2026-07-09"
    )


def test_taiwan_field_helpers_return_none_for_unexpected_columns() -> None:
    fields = ["證券代號", "證券名稱"]
    row = ["2330", "台積電"]

    assert taiwan_market_data.taiwan_field_index(fields, "資券餘額") is None
    assert taiwan_market_data.taiwan_row_value(row, fields, "資券餘額") is None


def test_resolve_taiwan_market_prefers_existing_metadata_when_available() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    resolved = taiwan_market_data.resolve_taiwan_market(
        "TW:2330",
        quote_source=FakeQuoteSource(stock=stock),
    )

    assert resolved == "TWSE"


def test_resolve_taiwan_market_uses_known_stock_objects_without_guessing() -> None:
    twse_stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    tpex_stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    assert (
        taiwan_market_data.resolve_taiwan_market(
            "2330",
            quote_source=FakeQuoteSource(stocks=[twse_stock, tpex_stock]),
            known_stocks=[twse_stock],
        )
        == "TWSE"
    )
    assert (
        taiwan_market_data.resolve_taiwan_market(
            "6488",
            quote_source=FakeQuoteSource(stocks=[twse_stock, tpex_stock]),
        )
        == "TPEx"
    )
    assert (
        taiwan_market_data.resolve_taiwan_market(
            "9999",
            quote_source=FakeQuoteSource(stocks=[twse_stock, tpex_stock]),
        )
        == "UNKNOWN"
    )


def test_resolve_taiwan_market_does_not_rewrite_non_taiwan_stock_ids() -> None:
    us_stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )

    assert (
        taiwan_market_data.resolve_taiwan_market(
            "US:AAPL",
            quote_source=FakeQuoteSource(stock=us_stock, stocks=[us_stock]),
        )
        == "UNKNOWN"
    )


def test_resolve_taiwan_market_returns_unknown_on_conflicting_exchanges() -> None:
    twse = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    conflict = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    assert (
        taiwan_market_data.resolve_taiwan_market(
            "2330",
            quote_source=FakeQuoteSource(stocks=[]),
            known_stocks=[twse, conflict],
        )
        == "UNKNOWN"
    )


def test_resolve_taiwan_market_accepts_localized_exchange_labels() -> None:
    listed = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="上市",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    otc = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="上櫃",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    assert (
        taiwan_market_data.resolve_taiwan_market(
            "2330",
            quote_source=FakeQuoteSource(stocks=[listed, otc]),
        )
        == "TWSE"
    )
    assert (
        taiwan_market_data.resolve_taiwan_market(
            "6488",
            quote_source=FakeQuoteSource(stocks=[listed, otc]),
        )
        == "TPEx"
    )


def test_is_taiwan_no_data_payload_detects_holiday_and_empty_shapes() -> None:
    assert taiwan_market_data.is_taiwan_no_data_payload({"stat": "Sorry, no data found"})
    assert taiwan_market_data.is_taiwan_no_data_payload({"stat": "OK", "data": []})
    assert taiwan_market_data.is_taiwan_no_data_payload({"tables": []})
    assert taiwan_market_data.is_taiwan_no_data_payload("查無資料")
    assert taiwan_market_data.is_taiwan_no_data_payload("Sorry, no data found")
    assert taiwan_market_data.is_taiwan_no_data_payload("No Data")
    assert not taiwan_market_data.is_taiwan_no_data_payload(
        {"stat": "OK", "data": [["2330", "台積電"]]}
    )


def test_fetch_twse_data_caches_by_market_date_request(monkeypatch) -> None:
    client = TaiwanMarketDataClient()
    calls: list[tuple[str, dict[str, str] | None]] = []

    def fake_fetch_json(url: str, **kwargs):
        calls.append((url, kwargs.get("params")))
        return {"stat": "OK", "data": [["2330", "台積電"]]}

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)

    first = client.fetch_twse_data(
        "https://www.twse.com.tw/fund/T86",
        params={"response": "json", "date": "20260709"},
        cache_ttl=timedelta(hours=6),
        resource_id="2026-07-09",
    )
    second = client.fetch_twse_data(
        "https://www.twse.com.tw/fund/T86",
        params={"response": "json", "date": "20260709"},
        cache_ttl=timedelta(hours=6),
        resource_id="2026-07-09",
    )

    assert first == second
    assert calls == [
        (
            "https://www.twse.com.tw/fund/T86",
            {"response": "json", "date": "20260709"},
        )
    ]


def test_fetch_twse_data_uses_short_ttl_for_no_data_payloads(monkeypatch) -> None:
    client = TaiwanMarketDataClient()
    calls = {"count": 0}
    payloads = [
        {"stat": "Sorry, no data found"},
        {"stat": "OK", "data": [["2330", "台積電"]]},
    ]

    def fake_fetch_json(url: str, **kwargs):
        index = min(calls["count"], len(payloads) - 1)
        calls["count"] += 1
        return payloads[index]

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)

    first = client.fetch_twse_data(
        "https://www.twse.com.tw/fund/T86",
        params={"response": "json", "date": "20260709"},
        cache_ttl=timedelta(hours=12),
    )
    assert taiwan_market_data.is_taiwan_no_data_payload(first)
    assert calls["count"] == 1

    # Within the short no-data TTL, reuse the empty payload.
    second = client.fetch_twse_data(
        "https://www.twse.com.tw/fund/T86",
        params={"response": "json", "date": "20260709"},
        cache_ttl=timedelta(hours=12),
    )
    assert second == first
    assert calls["count"] == 1

    # After the short no-data TTL expires, refetch so newly published data is visible.
    entry = next(iter(client._cache.values()))
    object.__setattr__(
        entry,
        "cached_at",
        entry.cached_at - timedelta(minutes=3),
    )

    third = client.fetch_twse_data(
        "https://www.twse.com.tw/fund/T86",
        params={"response": "json", "date": "20260709"},
        cache_ttl=timedelta(hours=12),
    )
    assert third == {"stat": "OK", "data": [["2330", "台積電"]]}
    assert calls["count"] == 2


def test_fetch_text_cache_key_includes_encoding_and_headers(monkeypatch) -> None:
    client = TaiwanMarketDataClient()
    calls: list[dict[str, object]] = []

    def fake_fetch_text(url: str, **kwargs):
        calls.append(
            {
                "encoding": kwargs.get("encoding"),
                "headers": kwargs.get("headers"),
            }
        )
        return f"payload-{kwargs.get('encoding')}"

    monkeypatch.setattr(taiwan_market_data, "fetch_text", fake_fetch_text)

    first = client.fetch_twse_text(
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        encoding=None,
        headers={"Accept": "text/html"},
    )
    second = client.fetch_twse_text(
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        encoding="big5",
        headers={"Accept": "text/csv"},
    )
    third = client.fetch_twse_text(
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        encoding="big5",
        headers={"Accept": "text/csv"},
    )

    assert first != second
    assert second == third
    assert len(calls) == 2
    assert calls[0]["encoding"] is None
    assert calls[1]["encoding"] == "big5"


def test_resolve_taiwan_market_skips_default_quote_source_when_known_stocks_resolve(
    monkeypatch,
) -> None:
    known = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    def fail_default_quote_source():
        raise AssertionError("default quote source should not be used")

    monkeypatch.setattr(
        taiwan_market_data,
        "_default_quote_source",
        fail_default_quote_source,
    )

    assert (
        taiwan_market_data.resolve_taiwan_market(
            "2330",
            known_stocks=[known],
        )
        == "TWSE"
    )


def test_fetch_tpex_data_supports_post_payloads(monkeypatch) -> None:
    client = TaiwanMarketDataClient()
    captured: dict[str, object] = {}

    def fake_fetch_json(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"stat": "ok", "tables": []}

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)

    payload = client.fetch_tpex_data(
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
        method="POST",
        data={"response": "json", "date": "2026/07/09"},
        resource_id="2026-07-09",
    )

    assert payload == {"stat": "ok", "tables": []}
    assert captured["url"] == "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    assert captured["kwargs"]["method"] == "POST"
    assert captured["kwargs"]["data"] == {"response": "json", "date": "2026/07/09"}
    assert captured["kwargs"]["provider_name"] == "tpex"


def test_fetch_twse_text_supports_csv_style_payloads(monkeypatch) -> None:
    client = TaiwanMarketDataClient()

    def fake_fetch_text(url: str, **kwargs):
        assert url.startswith("https://www.twse.com.tw/")
        assert kwargs["provider_name"] == "twse"
        return "證券代號,證券名稱\n2330,台積電\n"

    monkeypatch.setattr(taiwan_market_data, "fetch_text", fake_fetch_text)

    payload = client.fetch_twse_text(
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL",
        params={"response": "csv"},
    )

    assert "2330,台積電" in payload


def test_fetch_taiwan_data_rejects_non_official_hosts() -> None:
    client = TaiwanMarketDataClient()

    with pytest.raises(ValueError, match="Unsupported Taiwan market data host"):
        client.fetch_twse_data("https://example.com/not-official")

    with pytest.raises(ValueError, match="Unsupported Taiwan market data host"):
        client.fetch_tpex_data("https://api.example.com/not-official")


def test_fetch_taiwan_data_propagates_timeout_errors(monkeypatch) -> None:
    client = TaiwanMarketDataClient()

    def fake_fetch_json(url: str, **kwargs):
        raise HttpFetchError(f"timeout fetching {url}")

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)

    with pytest.raises(HttpFetchError, match="timeout"):
        client.fetch_twse_data(
            "https://www.twse.com.tw/fund/T86",
            params={"response": "json", "date": "20260709"},
        )


def test_module_level_wrappers_delegate_to_shared_client(monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch_json(url: str, **kwargs):
        calls.append(url)
        return {"ok": True}

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)
    taiwan_market_data.taiwan_market_data_client.clear_cache()

    assert taiwan_market_data.fetch_twse_data("https://www.twse.com.tw/fund/T86") == {"ok": True}
    assert taiwan_market_data.fetch_tpex_data(
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    ) == {"ok": True}
    assert calls == [
        "https://www.twse.com.tw/fund/T86",
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
    ]
