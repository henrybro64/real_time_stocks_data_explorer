from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.http_client import HttpFetchError
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorsService,
    _dealer_net_shares,
    _parse_tpex_row,
    _parse_twse_row,
    parse_all_institutional_records,
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


def test_parse_all_institutional_records_parses_full_twse_payload() -> None:
    payload = {
        "date": "20260709",
        "fields": _TWSE_FIELDS,
        "data": [
            ["2330", "台積電", "1,000", "2,000", "300", "100", "200"],
            ["2317", "鴻海", "500", "100", "50", "20", "30"],
        ],
    }

    records = parse_all_institutional_records(
        payload,
        market="TWSE",
        current_date=date(2026, 7, 9),
    )

    assert [item.symbol for item in records] == ["2330", "2317"]
    assert records[0].date == "2026-07-09"
    assert records[0].total_net_shares == 3_300


def test_parse_all_institutional_records_parses_full_tpex_payload() -> None:
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
    payload = {
        "date": "2026/07/09",
        "tables": [{"date": "115/07/09", "fields": fields, "data": [row]}],
    }

    records = parse_all_institutional_records(
        payload,
        market="TPEx",
        current_date=date(2026, 7, 9),
    )

    assert len(records) == 1
    assert records[0].symbol == "6488"
    assert records[0].foreign_net_shares == 1_000
    assert records[0].total_net_shares == 3_300


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
        use_default_store=False,
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
        use_default_store=False,
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
        use_default_store=False,
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
        use_default_store=False,
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
        use_default_store=False,
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
        use_default_store=False,
    )

    service.get_institutional_investors("TW:2330", days=2)

    assert fetched_dates
    assert all(day.weekday() < 5 for day in fetched_dates)
    # Empty/stale store uses the recent refresh window, not a multi-month walk.
    assert len(fetched_dates) <= 7


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
        use_default_store=False,
    )

    first = service.get_institutional_investors("TW:2330", days=1)
    first_count = calls["count"]
    assert first_count > 0

    second = service.get_institutional_investors("TW:2330", days=1)

    assert first.latest_date == second.latest_date
    # Second call should hit the short-lived result cache, not refetch market days.
    assert calls["count"] == first_count


class FakeInstitutionalStore:
    def __init__(self, rows_by_key: dict[tuple[str, str], list] | None = None) -> None:
        self.rows_by_key = rows_by_key or {}
        self.list_history_calls = 0
        self.upsert_calls: list[dict] = []
        self.available = True
        self.upsert_error: Exception | None = None

    def is_available(self) -> bool:
        return self.available

    def list_history(self, *, market: str, symbol: str, limit: int):
        self.list_history_calls += 1
        rows = sorted(
            self.rows_by_key.get((market, symbol), []),
            key=lambda row: row.date,
            reverse=True,
        )
        return rows[: max(limit, 1)]

    def upsert_records(self, *, market: str, source: str, records: list) -> object:
        if self.upsert_error is not None:
            raise self.upsert_error
        self.upsert_calls.append(
            {"market": market, "source": source, "records": list(records)}
        )
        for record in records:
            key = (market, record.symbol)
            trade_date = date.fromisoformat(record.date)
            existing = [
                row for row in self.rows_by_key.get(key, []) if row.date != trade_date
            ]
            existing.append(
                _stored_row(
                    market=market,
                    symbol=record.symbol,
                    trade_date=trade_date,
                    foreign_net_shares=record.foreign_net_shares,
                    name=record.name,
                )
            )
            self.rows_by_key[key] = existing
        return {"inserted": len(records), "updated": 0}


def _stored_row(
    *,
    market: str = "TWSE",
    symbol: str = "2330",
    trade_date: date,
    foreign_net_shares: int = 1_000,
    name: str = "台積電",
):
    from types import SimpleNamespace

    return SimpleNamespace(
        market=market,
        symbol=symbol,
        date=trade_date,
        name=name,
        foreign_net_shares=foreign_net_shares,
        investment_trust_net_shares=2_000,
        dealer_self_net_shares=100,
        dealer_hedge_net_shares=200,
        dealer_net_shares=300,
        total_net_shares=foreign_net_shares + 2_300,
        source="test-store",
    )


def test_service_serves_history_from_store_without_live_fetches() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 7, 8), foreign_net_shares=1_000),
                _stored_row(trade_date=date(2026, 7, 7), foreign_net_shares=4_000),
                # Holiday gap: 2026-07-06 absent on purpose.
                _stored_row(trade_date=date(2026, 7, 3), foreign_net_shares=7_000),
            ]
        }
    )
    live_calls: list[date] = []

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: live_calls.append(target_date) or {"stat": "查無資料"},
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert live_calls == []
    assert store.list_history_calls == 1
    assert store.upsert_calls == []
    assert [item.date for item in result.history] == [
        "2026-07-03",
        "2026-07-07",
        "2026-07-08",
    ]
    assert result.latest_date == "2026-07-08"
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 1_000
    assert result.market == "TWSE"
    assert result.source.startswith("Persisted ")
    assert "TWSE" in result.source


def test_service_serves_tpex_history_from_store() -> None:
    stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore(
        {
            ("TPEx", "6488"): [
                _stored_row(
                    market="TPEx",
                    symbol="6488",
                    trade_date=date(2026, 7, 8),
                    foreign_net_shares=500,
                    name="環球晶",
                )
            ]
        }
    )
    live_calls: list[date] = []

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        tpex_fetcher=lambda target_date: live_calls.append(target_date) or {"stat": "查無資料"},
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:6488", days=30)

    assert live_calls == []
    assert result.market == "TPEx"
    assert result.latest_date == "2026-07-08"
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 500
    assert result.source.startswith("Persisted ")
    assert "TPEx" in result.source


def test_service_uses_bounded_live_fallback_when_store_empty() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    payloads = {
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
    fetched: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched.append(target_date)
        return payloads.get(target_date, {"stat": "查無資料", "data": []})

    store = FakeInstitutionalStore()
    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert [item.date for item in result.history] == ["2026-07-07", "2026-07-08"]
    assert len(fetched) <= 7
    assert all(day.weekday() < 5 for day in fetched)
    assert not result.source.startswith("Persisted ")
    assert "TWSE" in result.source
    # Successful live rows should heal the empty store for later requests.
    assert len(store.upsert_calls) == 1
    assert store.upsert_calls[0]["market"] == "TWSE"
    assert [item.date for item in store.upsert_calls[0]["records"]] == [
        "2026-07-07",
        "2026-07-08",
    ]


def test_service_merges_stale_store_with_bounded_live_and_dedupes_dates() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    # Latest stored row is older than the freshness window vs candidate start 2026-07-09.
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 6, 30), foreign_net_shares=9_000),
                _stored_row(trade_date=date(2026, 6, 27), foreign_net_shares=8_000),
            ]
        }
    )
    payloads = {
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        },
        # Live also returns an overlapping older date; live value should win.
        date(2026, 7, 7): {
            "date": "20260630",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,500", "2,000", "3,000", "1,000", "2,000"]],
        },
    }
    fetched: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched.append(target_date)
        return payloads.get(target_date, {"stat": "查無資料", "data": []})

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert len(fetched) <= 7
    assert [item.date for item in result.history] == [
        "2026-06-27",
        "2026-06-30",
        "2026-07-08",
    ]
    # Duplicate trading date from live+store must collapse to one row (live wins).
    assert result.history[1].foreign_net_shares == 1_500
    assert result.latest_date == "2026-07-08"
    assert not result.source.startswith("Persisted ")
    assert len(store.upsert_calls) == 1


def test_service_keeps_stale_store_rows_when_live_fallback_fails() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 6, 1), foreign_net_shares=42),
            ]
        }
    )

    def failing_fetcher(_target_date: date):
        raise HttpFetchError("boom")

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=failing_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=10)

    assert [item.date for item in result.history] == ["2026-06-01"]
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 42
    assert result.source.startswith("Persisted ")
    assert store.upsert_calls == []


def test_service_falls_back_to_bounded_live_when_store_raises() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    class BrokenStore:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def is_available(self) -> bool:
            return True

        def list_history(self, *, market: str, symbol: str, limit: int):
            raise RuntimeError("db down")

        def upsert_records(self, *, market: str, source: str, records: list):
            # list_history failed, but healing after a successful live probe is OK.
            self.upsert_calls += 1
            return {"inserted": len(records), "updated": 0}

    payloads = {
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        },
    }
    fetched: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched.append(target_date)
        return payloads.get(target_date, {"stat": "查無資料", "data": []})

    store = BrokenStore()
    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert result.latest_date == "2026-07-08"
    assert len(fetched) <= 7
    assert not result.source.startswith("Persisted ")
    assert store.upsert_calls == 1


def test_service_falls_back_when_store_reports_unavailable() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 7, 8), foreign_net_shares=1_000),
            ]
        }
    )
    store.available = False
    payloads = {
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        },
    }
    fetched: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched.append(target_date)
        return payloads.get(target_date, {"stat": "查無資料", "data": []})

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert result.latest_date == "2026-07-08"
    assert store.list_history_calls == 0
    assert store.upsert_calls == []
    assert len(fetched) <= 7


def test_service_treats_store_as_fresh_on_freshness_boundary() -> None:
    """Latest == start_date - RECENT_DAYS is still fresh (no live probe)."""
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    # now 2026-07-10 01:00 UTC → Taipei 09:00 → candidate_start 2026-07-09
    # with RECENT_DAYS=7, boundary fresh date is 2026-07-02
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 7, 2), foreign_net_shares=7),
            ]
        }
    )
    live_calls: list[date] = []

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: live_calls.append(target_date) or {"stat": "查無資料"},
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert live_calls == []
    assert result.latest_date == "2026-07-02"
    assert result.source.startswith("Persisted ")


def test_service_treats_store_as_stale_just_beyond_freshness_boundary() -> None:
    """Latest == start_date - RECENT_DAYS - 1 must trigger bounded live fallback."""
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore(
        {
            ("TWSE", "2330"): [
                _stored_row(trade_date=date(2026, 7, 1), foreign_net_shares=1),
            ]
        }
    )
    payloads = {
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "9,000", "2,000", "3,000", "1,000", "2,000"]],
        },
    }
    fetched: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched.append(target_date)
        return payloads.get(target_date, {"stat": "查無資料", "data": []})

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=tracking_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert fetched
    assert result.latest_date == "2026-07-08"
    assert not result.source.startswith("Persisted ")
    assert len(store.upsert_calls) == 1


def test_service_live_fallback_upsert_failure_does_not_break_response() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    store = FakeInstitutionalStore()
    store.upsert_error = RuntimeError("disk full")
    payloads = {
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_FIELDS,
            "data": [["2330", "台積電", "1,000", "2,000", "3,000", "1,000", "2,000"]],
        },
    }

    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: payloads.get(
            target_date, {"stat": "查無資料", "data": []}
        ),
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=store,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert result.latest_date == "2026-07-08"
    assert result.summary is not None
    assert result.summary.foreign_net_shares == 1_000


def test_service_store_path_compatible_with_repository_upsert() -> None:
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base
    from app.repositories.taiwan_market_daily_repository import (
        TaiwanInstitutionalDailyRepository,
    )
    from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord

    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    repository = TaiwanInstitutionalDailyRepository()
    repository.set_session_factory_override(session_local)
    repository.upsert_records(
        market="TWSE",
        source="twse-t86",
        records=[
            TaiwanInstitutionalInvestorRecord(
                date="2026-07-08",
                symbol="2330",
                name="台積電",
                foreign_net_shares=1_000,
                investment_trust_net_shares=2_000,
                dealer_self_net_shares=100,
                dealer_hedge_net_shares=200,
                dealer_net_shares=300,
                total_net_shares=3_300,
            ),
            TaiwanInstitutionalInvestorRecord(
                date="2026-07-07",
                symbol="2330",
                name="台積電",
                foreign_net_shares=4_000,
                investment_trust_net_shares=5_000,
                dealer_self_net_shares=100,
                dealer_hedge_net_shares=500,
                dealer_net_shares=600,
                total_net_shares=9_600,
            ),
        ],
    )

    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    live_calls: list[date] = []
    service = TaiwanInstitutionalInvestorsService(
        quote_source=FakeQuoteSource(stock),
        twse_fetcher=lambda target_date: live_calls.append(target_date) or {"stat": "查無資料"},
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
        store=repository,
    )

    result = service.get_institutional_investors("TW:2330", days=90)

    assert live_calls == []
    assert [item.date for item in result.history] == ["2026-07-07", "2026-07-08"]
    assert result.summary is not None
    assert result.summary.total_net_shares == 3_300
    assert result.source.startswith("Persisted ")
