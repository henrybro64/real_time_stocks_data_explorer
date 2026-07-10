from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.http_client import HttpFetchError
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorsService,
    _dealer_net_shares,
    _parse_tpex_row,
    _parse_twse_row,
)
from tests.support import stock_record


class FakeQuoteSource:
    def __init__(self, stock):
        self._stock = stock

    def get_stock(self, stock_id: str):
        if self._stock is not None and self._stock.id == stock_id:
            return self._stock
        return None

    def list_stocks(self):
        return [self._stock] if self._stock is not None else []


_TWSE_FIELDS = [
    "證券代號",
    "證券名稱",
    "外陸資買賣超股數(不含外資自營商)",
    "投信買賣超股數",
    "自營商買賣超股數",
    "自營商買賣超股數(自行買賣)",
    "自營商買賣超股數(避險)",
]


def test_parse_twse_row_aggregates_dealer_split_and_total() -> None:
    row = [
        "2330",
        "台積電",
        "-12,748,541",
        "43,225",
        "89,863",
        "905",
        "88,958",
    ]

    record = _parse_twse_row(row, _TWSE_FIELDS, record_date="2026-07-09")

    assert record is not None
    assert record.symbol == "2330"
    assert record.foreign_net_shares == -12_748_541
    assert record.investment_trust_net_shares == 43_225
    assert record.dealer_self_net_shares == 905
    assert record.dealer_hedge_net_shares == 88_958
    # Prefer official combined 自營商買賣超 when present.
    assert record.dealer_net_shares == 89_863
    assert record.total_net_shares == -12_615_453


def test_parse_twse_row_falls_back_to_dealer_split_when_combined_missing() -> None:
    fields = [
        "證券代號",
        "證券名稱",
        "外陸資買賣超股數(不含外資自營商)",
        "投信買賣超股數",
        "自營商買賣超股數(自行買賣)",
        "自營商買賣超股數(避險)",
    ]
    row = ["2330", "台積電", "1,000", "2,000", "100", "200"]

    record = _parse_twse_row(row, fields, record_date="2026-07-09")

    assert record is not None
    assert record.dealer_net_shares == 300
    assert record.total_net_shares == 3_300


def test_dealer_net_shares_prefers_official_combined_value() -> None:
    assert _dealer_net_shares(100, 200, 250) == 250
    assert _dealer_net_shares(100, 200, None) == 300
    assert _dealer_net_shares(100, None, None) == 100
    assert _dealer_net_shares(None, None, None) is None


def test_parse_tpex_row_aggregates_dealer_split_and_total() -> None:
    fields = [
        "代號",
        "名稱",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "三大法人買賣超股數合計",
    ]
    row = [
        "8069",
        "元太",
        "2,426,336",
        "2,253,443",
        "172,893",
        "0",
        "0",
        "0",
        "2,426,336",
        "2,253,443",
        "172,893",
        "218,590",
        "0",
        "218,590",
        "0",
        "0",
        "0",
        "182,682",
        "71,753",
        "110,929",
        "182,682",
        "71,753",
        "110,929",
        "502,412",
    ]

    record = _parse_tpex_row(row, fields, record_date="2026-07-09")

    assert record is not None
    assert record.symbol == "8069"
    assert record.foreign_net_shares == 172_893
    assert record.investment_trust_net_shares == 218_590
    assert record.dealer_self_net_shares == 0
    assert record.dealer_hedge_net_shares == 110_929
    assert record.dealer_net_shares == 110_929
    assert record.total_net_shares == 502_412


def test_parse_twse_row_skips_malformed_numeric_rows() -> None:
    row = ["2330", "台積電", "--", "2,000", "3,000", "1,000", "2,000"]
    assert _parse_twse_row(row, _TWSE_FIELDS, record_date="2026-07-09") is None


def test_service_returns_latest_summary_and_chronological_history() -> None:
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

    payloads = {
        date(2026, 7, 9): {"stat": "查無資料", "data": []},
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        },
        date(2026, 7, 7): {
            "date": "20260707",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "4,000", "5,000", "6,000", "1,000", "5,000"]],
        },
    }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: payloads.get(target_date, {"stat": "查無資料", "data": []}),
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_institutional_investors("TW:2330", days=2)

    assert result.market == "TWSE"
    assert result.latest_date == "2026-07-08"
    assert [item.date for item in result.history] == ["2026-07-07", "2026-07-08"]
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 1_000
    assert result.summary.dealer_net_shares == 3_000
    assert result.summary.total_net_shares == 6_000


def test_service_supports_tpex_symbols() -> None:
    stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    fields = [
        "代號",
        "名稱",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "三大法人買賣超股數合計",
    ]
    row = [
        "6488",
        "環球晶",
        "0",
        "0",
        "1,000",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "2,000",
        "0",
        "0",
        "100",
        "0",
        "0",
        "200",
        "0",
        "0",
        "300",
        "3,300",
    ]

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        tpex_fetcher=lambda _target_date: {
            "date": "2026/07/08",
            "tables": [{"date": "115/07/08", "fields": fields, "data": [row]}],
        },
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_institutional_investors("TW:6488", days=1)

    assert result.market == "TPEx"
    assert result.latest_date == "2026-07-08"
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 1_000
    assert result.summary.investment_trust_net_shares == 2_000
    assert result.summary.dealer_net_shares == 300
    assert result.summary.total_net_shares == 3_300
    assert "TPEx" in result.source


def test_service_dedupes_tpex_history_when_weekend_probes_resolve_to_same_trading_date() -> None:
    stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    fields = [
        "代號",
        "名稱",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "買進股數",
        "賣出股數",
        "買賣超股數",
        "三大法人買賣超股數合計",
    ]

    def row_for(symbol: str, foreign: str, trust: str, dealer: str, total: str) -> list[str]:
        return [
            symbol,
            "環球晶",
            "0",
            "0",
            foreign,
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            trust,
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            dealer,
            total,
        ]

    payloads = {
        # Weekend probes both resolve to Friday 2026-07-10 in the payload date.
        date(2026, 7, 12): {
            "date": "2026/07/10",
            "tables": [
                {
                    "date": "115/07/10",
                    "fields": fields,
                    "data": [row_for("6488", "1,000", "2,000", "300", "3,300")],
                }
            ],
        },
        date(2026, 7, 11): {
            "date": "2026/07/10",
            "tables": [
                {
                    "date": "115/07/10",
                    "fields": fields,
                    "data": [row_for("6488", "1,000", "2,000", "300", "3,300")],
                }
            ],
        },
        date(2026, 7, 10): {
            "date": "2026/07/10",
            "tables": [
                {
                    "date": "115/07/10",
                    "fields": fields,
                    "data": [row_for("6488", "1,000", "2,000", "300", "3,300")],
                }
            ],
        },
        date(2026, 7, 9): {
            "date": "2026/07/09",
            "tables": [
                {
                    "date": "115/07/09",
                    "fields": fields,
                    "data": [row_for("6488", "4,000", "5,000", "600", "9,600")],
                }
            ],
        },
    }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        tpex_fetcher=lambda target_date: payloads.get(
            target_date,
            {"stat": "查無資料", "tables": []},
        ),
        # Sunday evening local time so candidate start is 2026-07-12.
        now_provider=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )

    result = service.get_institutional_investors("TW:6488", days=2)

    assert [item.date for item in result.history] == ["2026-07-09", "2026-07-10"]
    assert result.latest_date == "2026-07-10"
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 1_000


def test_service_returns_empty_history_when_symbol_has_no_available_rows() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    payload = {
        "date": "20260708",
        "fields": _TWSE_FIELDS,
        "data": [["2317", "鴻海", "1,000", "2,000", "3,000", "1,000", "2,000"]],
    }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_institutional_investors("TW:2330", days=2)

    assert result.latest_date is None
    assert result.summary is None
    assert result.history == ()


def test_service_raises_for_missing_stock() -> None:
    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(None),
    )

    with pytest.raises(ValueError, match="Stock not found"):
        service.get_institutional_investors("TW:9999")


def test_service_raises_for_non_taiwan_stock() -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
    )

    with pytest.raises(ValueError, match="only available for TW stocks"):
        service.get_institutional_investors("US:AAPL")


def test_service_raises_when_official_source_is_unavailable() -> None:
    stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    def failing_fetcher(_target_date: date):
        raise HttpFetchError("boom")

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        tpex_fetcher=failing_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        service.get_institutional_investors("TW:6488", days=1)


def test_service_skips_weekend_candidate_dates_without_fetching(monkeypatch) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    fetched_dates: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched_dates.append(target_date)
        return {
            "date": target_date.strftime("%Y%m%d"),
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        # Sunday local morning -> candidate start Saturday after lookback.
        now_provider=lambda: datetime(2026, 7, 12, 1, 0, tzinfo=UTC),
    )

    service.get_institutional_investors("TW:2330", days=2)

    assert fetched_dates
    assert all(day.weekday() < 5 for day in fetched_dates)


def test_service_reuses_short_lived_result_cache() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    calls = {"count": 0}

    def counting_fetcher(target_date: date):
        calls["count"] += 1
        return {
            "date": target_date.strftime("%Y%m%d"),
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=counting_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    first = service.get_institutional_investors("TW:2330", days=1)
    first_count = calls["count"]
    assert first_count > 0

    second = service.get_institutional_investors("TW:2330", days=1)

    assert first.latest_date == second.latest_date
    # Second call should hit the short-lived result cache, not refetch market days.
    assert calls["count"] == first_count
