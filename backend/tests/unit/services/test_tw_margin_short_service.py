from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.http_client import HttpFetchError
from app.services.tw_margin_short_service import (
    TaiwanMarginShortService,
    _BorrowedShortPoint,
    _ClosePoint,
    _MarginPoint,
    _merge_daily_points,
    _parse_tpex_borrowed_short_row,
    _parse_tpex_margin_row,
    _parse_twse_borrowed_short_row,
    _parse_twse_margin_row,
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


_TPEX_MARGIN_FIELDS = [
    "代號",
    "名稱",
    "前資餘額(張)",
    "資買",
    "資賣",
    "現償",
    "資餘額",
    "資屬證金",
    "資使用率(%)",
    "資限額",
    "前券餘額(張)",
    "券賣",
    "券買",
    "券償",
    "券餘額",
    "券屬證金",
    "券使用率(%)",
    "券限額",
    "資券相抵(張)",
    "備註",
]

_TWSE_CLOSE_FIELDS = [
    "證券代號",
    "證券名稱",
    "前日借券餘額(1)股",
    "本日異動股借券(2)",
    "本日異動股還券(3)",
    "本日借券餘額股(4)=(1)+(2)-(3)",
    "本日收盤價(5)單位：元",
    "借券餘額市值單位：元(6)=(4)*(5)",
    "市場別",
]


def test_parse_twse_margin_row_reads_margin_and_short_balances() -> None:
    row = [
        "0050",
        "元大台灣50",
        "282",
        "375",
        "12",
        "13,411",
        "13,306",
        "5,233,875",
        "48",
        "22",
        "0",
        "968",
        "942",
        "5,233,875",
        "14",
        " ",
    ]

    point = _parse_twse_margin_row(row, record_date="2026-07-09")

    assert point is not None
    assert point.symbol == "0050"
    assert point.margin_balance_lots == 13_306.0
    assert point.short_margin_balance_lots == 942.0


def test_parse_tpex_margin_row_reads_margin_and_short_balances() -> None:
    row = [
        "1303",
        "南亞",
        "31,000",
        "500",
        "300",
        "0",
        "31,339",
        "0",
        "0.3",
        "100,000",
        "0",
        "10",
        "10",
        "0",
        "0",
        "0",
        "0.0",
        "100,000",
        "0",
        "",
    ]

    point = _parse_tpex_margin_row(row, _TPEX_MARGIN_FIELDS, record_date="2026-07-09")

    assert point is not None
    assert point.symbol == "1303"
    assert point.margin_balance_lots == 31_339.0
    assert point.short_margin_balance_lots == 0.0


def test_parse_twse_borrowed_short_row_extracts_previous_and_current_shares() -> None:
    row = [
        "1303",
        "南亞",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "12,204,000",
        "0",
        "0",
        "0",
        "12,345,000",
        "0",
        " ",
    ]

    point = _parse_twse_borrowed_short_row(row, record_date="2026-07-09")

    assert point is not None
    assert point.symbol == "1303"
    assert point.previous_borrowed_short_balance_shares == 12_204_000
    assert point.borrowed_short_balance_shares == 12_345_000


def test_parse_tpex_borrowed_short_row_extracts_previous_and_current_shares() -> None:
    row = [
        "6488",
        "環球晶",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "2,000,000",
        "0",
        "0",
        "0",
        "2,100,000",
        "0",
        "",
    ]

    point = _parse_tpex_borrowed_short_row(row, record_date="2026-07-09")

    assert point is not None
    assert point.symbol == "6488"
    assert point.previous_borrowed_short_balance_shares == 2_000_000
    assert point.borrowed_short_balance_shares == 2_100_000


def test_merge_daily_points_converts_share_balances_to_lots_for_100_share_products() -> None:
    record = _merge_daily_points(
        margin_point=_MarginPoint(
            date="2026-07-09",
            symbol="00677K",
            name="富邦VIX",
            margin_balance_lots=10.0,
            short_margin_balance_lots=2.0,
        ),
        borrowed_point=_BorrowedShortPoint(
            date="2026-07-09",
            symbol="00677K",
            name="富邦VIX",
            borrowed_short_balance_shares=1_500,
            previous_borrowed_short_balance_shares=1_000,
        ),
        close_point=_ClosePoint(
            date="2026-07-09",
            symbol="00677K",
            market="TWSE",
            close_price=200.0,
        ),
    )

    assert record is not None
    assert record.borrowed_short_balance_lots == pytest.approx(15.0)
    assert record.borrowed_short_balance_change_lots == pytest.approx(5.0)
    assert record.borrowed_short_balance_change_amount_10k == 10


def test_merge_daily_points_rounds_amount_to_nearest_int() -> None:
    record = _merge_daily_points(
        margin_point=_MarginPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            margin_balance_lots=31_339.0,
            short_margin_balance_lots=0.0,
        ),
        borrowed_point=_BorrowedShortPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            borrowed_short_balance_shares=12_345_000,
            previous_borrowed_short_balance_shares=12_204_000,
        ),
        close_point=_ClosePoint(
            date="2026-07-09",
            symbol="1303",
            market="TWSE",
            close_price=185.0,
        ),
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots == pytest.approx(141.0)
    assert record.borrowed_short_balance_change_amount_10k == 2609


def test_merge_daily_points_sets_change_and_amount_to_none_when_previous_missing() -> None:
    record = _merge_daily_points(
        margin_point=_MarginPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            margin_balance_lots=31_339.0,
            short_margin_balance_lots=0.0,
        ),
        borrowed_point=_BorrowedShortPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            borrowed_short_balance_shares=12_345_000,
            previous_borrowed_short_balance_shares=None,
        ),
        close_point=_ClosePoint(
            date="2026-07-09",
            symbol="1303",
            market="TWSE",
            close_price=185.0,
        ),
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots is None
    assert record.borrowed_short_balance_change_amount_10k is None


def test_merge_daily_points_sets_amount_to_none_when_close_missing() -> None:
    record = _merge_daily_points(
        margin_point=_MarginPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            margin_balance_lots=31_339.0,
            short_margin_balance_lots=0.0,
        ),
        borrowed_point=_BorrowedShortPoint(
            date="2026-07-09",
            symbol="1303",
            name="南亞",
            borrowed_short_balance_shares=12_345_000,
            previous_borrowed_short_balance_shares=12_204_000,
        ),
        close_point=None,
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots == pytest.approx(141.0)
    assert record.borrowed_short_balance_change_amount_10k is None


def test_service_returns_latest_summary_and_chronological_history() -> None:
    stock = stock_record(
        "TW:1303",
        market="TW",
        symbol="1303",
        name="南亞",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="Nan Ya Plastics",
        name_zh_tw="南亞",
    )

    twse_margin_payloads = {
        date(2026, 7, 9): {"stat": "查無資料", "data": []},
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": ["placeholder"],
            "data": [["1303", "南亞", "0", "0", "0", "31,000", "31,339", "0", "0", "0", "0", "0", "0", "0", "0", ""]],
        },
        date(2026, 7, 7): {
            "date": "20260707",
            "fields": ["placeholder"],
            "data": [["1303", "南亞", "0", "0", "0", "30,800", "31,000", "0", "0", "0", "0", "0", "0", "0", "0", ""]],
        },
    }
    twse_short_payloads = {
        date(2026, 7, 9): {"stat": "查無資料", "data": []},
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": ["placeholder"],
            "data": [["1303", "南亞", "0", "0", "0", "0", "0", "0", "12,204,000", "0", "0", "0", "12,345,000", "0", " "]],
        },
        date(2026, 7, 7): {
            "date": "20260707",
            "fields": ["placeholder"],
            "data": [["1303", "南亞", "0", "0", "0", "0", "0", "0", "12,000,000", "0", "0", "0", "12,204,000", "0", " "]],
        },
    }
    close_payloads = {
        date(2026, 7, 9): {"stat": "查無資料", "data": []},
        date(2026, 7, 8): {
            "date": "20260708",
            "fields": _TWSE_CLOSE_FIELDS,
            "data": [["1303", "南亞", "0", "0", "0", "0", "185", "0", "集中市場"]],
        },
        date(2026, 7, 7): {
            "date": "20260707",
            "fields": _TWSE_CLOSE_FIELDS,
            "data": [["1303", "南亞", "0", "0", "0", "0", "184", "0", "集中市場"]],
        },
    }

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda target_date: twse_margin_payloads.get(
            target_date,
            {"stat": "查無資料", "data": []},
        ),
        twse_borrowed_short_fetcher=lambda target_date: twse_short_payloads.get(
            target_date,
            {"stat": "查無資料", "data": []},
        ),
        twse_close_fetcher=lambda target_date: close_payloads.get(
            target_date,
            {"stat": "查無資料", "data": []},
        ),
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=2)

    assert result.market == "TWSE"
    assert result.latest_date == "2026-07-08"
    assert [item.date for item in result.history] == ["2026-07-07", "2026-07-08"]
    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(31_339.0)
    assert result.summary.borrowed_short_balance_lots == pytest.approx(12_345.0)
    assert result.summary.borrowed_short_balance_change_lots == pytest.approx(141.0)
    assert result.summary.borrowed_short_balance_change_amount_10k == 2609


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

    tpex_margin_payload = {
        "date": "20260708",
        "tables": [
            {
                "date": "115/07/08",
                "fields": _TPEX_MARGIN_FIELDS,
                "data": [
                    [
                        "6488",
                        "環球晶",
                        "10,000",
                        "0",
                        "0",
                        "0",
                        "10,200",
                        "0",
                        "0.1",
                        "100,000",
                        "5",
                        "0",
                        "0",
                        "0",
                        "4",
                        "0",
                        "0.0",
                        "100,000",
                        "0",
                        "",
                    ]
                ],
            }
        ],
    }
    tpex_sbl_payload = {
        "date": "20260708",
        "tables": [
            {
                "date": "115/07/08",
                "fields": [
                    "股票代號",
                    "股票名稱",
                    "前日餘額",
                    "賣出",
                    "買進",
                    "現券",
                    "當日餘額",
                    "限額",
                    "前日餘額",
                    "當日賣出",
                    "當日還券",
                    "當日調整數額",
                    "當日餘額",
                    "次一營業日可借券賣出限額",
                    "備註",
                ],
                "data": [["6488", "環球晶", "5,000", "0", "0", "0", "4,000", "0", "2,000,000", "0", "0", "0", "2,100,000", "0", ""]],
            }
        ],
    }
    close_payload = {
        "date": "20260708",
        "fields": _TWSE_CLOSE_FIELDS,
        "data": [["6488", "環球晶", "0", "0", "0", "0", "510", "0", "櫃檯買賣中心"]],
    }

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        tpex_margin_fetcher=lambda _target_date: tpex_margin_payload,
        tpex_borrowed_short_fetcher=lambda _target_date: tpex_sbl_payload,
        twse_close_fetcher=lambda _target_date: close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:6488", days=1)

    assert result.market == "TPEx"
    assert result.latest_date == "2026-07-08"
    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(10_200.0)
    assert result.summary.short_margin_balance_lots == pytest.approx(4.0)
    assert result.summary.borrowed_short_balance_lots == pytest.approx(2_100.0)
    assert result.summary.borrowed_short_balance_change_lots == pytest.approx(100.0)
    assert result.summary.borrowed_short_balance_change_amount_10k == 5100


def test_service_raises_upstream_error_when_required_feed_is_unavailable() -> None:
    stock = stock_record(
        "TW:1303",
        market="TW",
        symbol="1303",
        name="南亞",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: {
            "date": "20260708",
            "fields": ["placeholder"],
            "data": [["1303", "南亞", "0", "0", "0", "31,000", "31,339", "0", "0", "0", "0", "0", "0", "0", "0", ""]],
        },
        twse_borrowed_short_fetcher=lambda _target_date: _raise_http_fetch_error(),
        twse_close_fetcher=lambda _target_date: {
            "date": "20260708",
            "fields": _TWSE_CLOSE_FIELDS,
            "data": [["1303", "南亞", "0", "0", "0", "0", "185", "0", "集中市場"]],
        },
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short data is temporarily unavailable from official sources.",
    ):
        service.get_margin_short("TW:1303", days=1)


def _raise_http_fetch_error() -> object:
    raise HttpFetchError("upstream unavailable")
