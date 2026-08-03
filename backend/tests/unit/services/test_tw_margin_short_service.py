from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.services.http_client import HttpFetchError
from app.services.tw_margin_short_service import (
    TaiwanMarginShortService,
    _BorrowedShortPoint,
    _ClosePoint,
    _MarginPoint,
    _SourceOutcome,
    _close_supplement_market,
    _core_request_succeeded,
    _extract_twse_borrowed_short_point,
    _extract_twse_margin_point,
    _fetch_tpex_dated_payload,
    _merge_daily_points,
    _parse_close_supplement_row,
    _parse_tpex_borrowed_short_row,
    _parse_tpex_margin_row,
    _parse_twse_borrowed_short_row,
    _parse_twse_margin_row,
    _round_nearest_int,
    _twse_stock_table,
    _upstream_error_for_scan,
    parse_all_margin_short_records,
    resolve_tpex_borrowed_short_columns,
    resolve_twse_margin_columns,
)
from tests.support import FakeQuoteSource, stock_record

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tw_margin_short"


def _load_fixture(name: str) -> object:
    path = _FIXTURES / name
    if path.suffix == ".html":
        return path.read_text(encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


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

# Mirrors the live TPEx `/margin/sbl` response shape (duplicate short labels).
_TPEX_SBL_FIELDS = [
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
]

_TPEX_SBL_UNIQUE_FIELDS = [
    "股票代號",
    "股票名稱",
    "融券前日餘額",
    "融券賣出",
    "融券買進",
    "融券現券",
    "融券當日餘額",
    "融券限額",
    "借券賣出前日餘額",
    "借券賣出當日賣出",
    "借券賣出當日還券",
    "借券賣出當日調整數額",
    "借券賣出當日餘額",
    "次一營業日可借券賣出限額",
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


def _nan_ya_stock():
    return stock_record(
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


def _margin_point(
    *,
    date_value: str = "2026-07-09",
    symbol: str = "1303",
    name: str = "南亞",
    margin_balance_lots: float = 31_339.0,
    short_margin_balance_lots: float = 0.0,
) -> _MarginPoint:
    return _MarginPoint(
        date=date_value,
        symbol=symbol,
        name=name,
        margin_balance_lots=margin_balance_lots,
        short_margin_balance_lots=short_margin_balance_lots,
    )


def _borrowed_point(
    *,
    date_value: str = "2026-07-09",
    symbol: str = "1303",
    name: str = "南亞",
    current_shares: int = 12_345_000,
    previous_shares: int | None = 12_204_000,
) -> _BorrowedShortPoint:
    return _BorrowedShortPoint(
        date=date_value,
        symbol=symbol,
        name=name,
        borrowed_short_balance_shares=current_shares,
        previous_borrowed_short_balance_shares=previous_shares,
    )


def _close_point(
    *,
    date_value: str = "2026-07-09",
    symbol: str = "1303",
    market: str = "TWSE",
    close_price: float | None = 185.0,
) -> _ClosePoint:
    return _ClosePoint(
        date=date_value,
        symbol=symbol,
        market=market,  # type: ignore[arg-type]
        close_price=close_price,
    )


def _twse_margin_payload(target_date: date, *, margin_lots: str = "31,339") -> dict:
    return {
        "date": target_date.strftime("%Y%m%d"),
        "fields": ["placeholder"],
        "data": [["1303", "南亞", "0", "0", "0", "31,000", margin_lots, "0", "0", "0", "0", "0", "0", "0", "0", ""]],
    }


def _twse_short_payload(target_date: date) -> dict:
    return {
        "date": target_date.strftime("%Y%m%d"),
        "fields": ["placeholder"],
        "data": [["1303", "南亞", "0", "0", "0", "0", "0", "0", "12,204,000", "0", "0", "0", "12,345,000", "0", " "]],
    }


def _twse_close_payload(target_date: date, *, price: str = "185", market_label: str = "集中市場") -> dict:
    return {
        "date": target_date.strftime("%Y%m%d"),
        "fields": _TWSE_CLOSE_FIELDS,
        "data": [["1303", "南亞", "0", "0", "0", "0", price, "0", market_label]],
    }


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
    assert point.margin_balance_lots == pytest.approx(13_306.0)
    assert point.short_margin_balance_lots == pytest.approx(942.0)


def test_parse_all_margin_short_records_merges_twse_payloads() -> None:
    records = parse_all_margin_short_records(
        market="TWSE",
        current_date=date(2026, 7, 9),
        margin_payload=_twse_margin_payload(date(2026, 7, 9)),
        borrowed_payload=_twse_short_payload(date(2026, 7, 9)),
        close_payload=_twse_close_payload(date(2026, 7, 9)),
    )

    assert len(records) == 1
    assert records[0].symbol == "1303"
    assert records[0].margin_balance_lots == pytest.approx(31_339.0)
    assert records[0].borrowed_short_balance_lots == pytest.approx(12_345.0)
    assert records[0].close_price == pytest.approx(185.0)


def test_parse_all_margin_short_records_merges_tpex_payloads() -> None:
    margin_payload = {
        "date": "20260709",
        "tables": [
            {
                "date": "115/07/09",
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
    borrowed_payload = {
        "date": "20260709",
        "tables": [
            {
                "date": "115/07/09",
                "fields": _TPEX_SBL_FIELDS,
                "data": [
                    [
                        "6488",
                        "環球晶",
                        "5,000",
                        "0",
                        "0",
                        "0",
                        "4,000",
                        "0",
                        "2,000,000",
                        "0",
                        "0",
                        "0",
                        "2,100,000",
                        "0",
                        "",
                    ]
                ],
            }
        ],
    }
    close_payload = {
        "date": "20260709",
        "fields": _TWSE_CLOSE_FIELDS,
        "data": [["6488", "環球晶", "0", "0", "0", "0", "510", "0", "櫃檯買賣中心"]],
    }

    records = parse_all_margin_short_records(
        market="TPEx",
        current_date=date(2026, 7, 9),
        margin_payload=margin_payload,
        borrowed_payload=borrowed_payload,
        close_payload=close_payload,
    )

    assert len(records) == 1
    assert records[0].symbol == "6488"
    assert records[0].margin_balance_lots == pytest.approx(10_200.0)
    assert records[0].borrowed_short_balance_lots == pytest.approx(2_100.0)
    assert records[0].close_price == pytest.approx(510.0)


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
    assert point.margin_balance_lots == pytest.approx(31_339.0)
    assert point.short_margin_balance_lots == pytest.approx(0.0)


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

    point = _parse_tpex_borrowed_short_row(row, _TPEX_SBL_FIELDS, record_date="2026-07-09")

    assert point is not None
    assert point.symbol == "6488"
    assert point.previous_borrowed_short_balance_shares == 2_000_000
    assert point.borrowed_short_balance_shares == 2_100_000


def test_resolve_tpex_borrowed_short_columns_uses_unique_official_labels() -> None:
    columns = resolve_tpex_borrowed_short_columns(_TPEX_SBL_UNIQUE_FIELDS)

    assert columns is not None
    assert columns.previous_balance_index == 8
    assert columns.current_balance_index == 12
    assert columns.used_index_fallback is False


def test_resolve_tpex_borrowed_short_columns_handles_reordered_duplicate_labels() -> None:
    # Borrowed-short section first (with markers), margin-like duplicates last.
    # Last-duplicate-wins would wrongly pick indexes 8/9.
    reordered = [
        "股票代號",
        "股票名稱",
        "前日餘額",
        "當日賣出",
        "當日還券",
        "當日調整數額",
        "當日餘額",
        "次一營業日可借券賣出限額",
        "前日餘額",
        "當日餘額",
        "備註",
    ]
    columns = resolve_tpex_borrowed_short_columns(reordered)

    assert columns is not None
    assert columns.previous_balance_index == 2
    assert columns.current_balance_index == 6
    assert columns.used_index_fallback is False


def test_resolve_tpex_borrowed_short_columns_uses_documented_index_fallback() -> None:
    fields = [
        "股票代號",
        "股票名稱",
        "col2",
        "col3",
        "col4",
        "col5",
        "col6",
        "col7",
        "col8",
        "col9",
        "col10",
        "col11",
        "col12",
        "col13",
    ]
    columns = resolve_tpex_borrowed_short_columns(fields)

    assert columns is not None
    assert columns.used_index_fallback is True
    assert columns.previous_balance_index == 8
    assert columns.current_balance_index == 12


def test_parse_tpex_borrowed_short_row_ignores_margin_section_duplicates() -> None:
    """Regression: first 前日餘額/當日餘額 belong to 融券, not 借券賣出."""
    row = [
        "6488",
        "環球晶",
        "29,000",
        "129,000",
        "16,000",
        "0",
        "142,000",
        "119,528,431",
        "10,024,000",
        "1,144,000",
        "67,000",
        "0",
        "11,101,000",
        "2,477,593",
        "",
    ]

    point = _parse_tpex_borrowed_short_row(row, _TPEX_SBL_FIELDS, record_date="2026-07-31")

    assert point is not None
    assert point.previous_borrowed_short_balance_shares == 10_024_000
    assert point.borrowed_short_balance_shares == 11_101_000


def test_parse_tpex_borrowed_short_row_supports_unique_label_aliases() -> None:
    row = [
        "6488",
        "環球晶",
        "5,000",
        "0",
        "0",
        "0",
        "4,000",
        "0",
        "2,000,000",
        "0",
        "0",
        "0",
        "2,100,000",
        "0",
        "",
    ]

    point = _parse_tpex_borrowed_short_row(
        row,
        _TPEX_SBL_UNIQUE_FIELDS,
        record_date="2026-07-09",
    )

    assert point is not None
    assert point.borrowed_short_balance_shares == 2_100_000


def test_resolve_tpex_borrowed_short_columns_reports_unknown_schema() -> None:
    assert resolve_tpex_borrowed_short_columns(["foo", "bar"]) is None


def test_merge_daily_points_converts_share_balances_to_lots_for_100_share_products() -> None:
    """資券 borrowed-short shares reuse shares_to_lots (100-unit K/C FX ETFs)."""
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
    # 1500 / 100 = 15 張; delta 500 / 100 = 5 張 (not 1.5 / 0.5 from /1000).
    assert record.borrowed_short_balance_lots == pytest.approx(15.0)
    assert record.borrowed_short_balance_change_lots == pytest.approx(5.0)
    assert record.borrowed_short_balance_change_amount_10k == 10


def test_merge_daily_points_rounds_amount_to_nearest_int() -> None:
    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=_borrowed_point(),
        close_point=_close_point(),
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots == pytest.approx(141.0)
    assert record.borrowed_short_balance_change_amount_10k == 2609


def test_merge_daily_points_sets_change_and_amount_to_none_when_previous_missing() -> None:
    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=_borrowed_point(previous_shares=None),
        close_point=_close_point(),
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots is None
    assert record.borrowed_short_balance_change_amount_10k is None


def test_merge_daily_points_sets_amount_to_none_when_close_missing() -> None:
    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=_borrowed_point(),
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
                "fields": _TPEX_SBL_FIELDS,
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


def test_service_returns_partial_history_when_one_core_feed_http_fails() -> None:
    stock = _nan_ya_stock()

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda target_date: _twse_margin_payload(target_date),
        twse_borrowed_short_fetcher=lambda _target_date: _raise_http_fetch_error(),
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=1)

    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(31_339.0)
    assert result.summary.borrowed_short_balance_lots is None


def test_service_raises_upstream_error_when_both_core_feeds_are_unavailable() -> None:
    stock = _nan_ya_stock()

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: _raise_http_fetch_error(),
        twse_borrowed_short_fetcher=lambda _target_date: _raise_http_fetch_error(),
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short data is temporarily unavailable from official sources.",
    ):
        service.get_margin_short("TW:1303", days=1)


def test_parse_twse_margin_row_skips_malformed_numeric_rows() -> None:
    row = [
        "1303",
        "南亞",
        "0",
        "0",
        "0",
        "bad",
        "not-a-number",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "",
    ]

    assert _parse_twse_margin_row(row, record_date="2026-07-09") is None


def test_parse_borrowed_short_row_skips_malformed_numeric_rows() -> None:
    row = [
        "1303",
        "南亞",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "bad",
        "0",
        "0",
        "0",
        "also-bad",
        "0",
        " ",
    ]

    assert _parse_twse_borrowed_short_row(row, record_date="2026-07-09") is None


def test_parse_close_supplement_row_maps_twse_and_tpex_markets() -> None:
    twse_point = _parse_close_supplement_row(
        ["1303", "南亞", "0", "0", "0", "0", "185", "0", "集中市場"],
        _TWSE_CLOSE_FIELDS,
        record_date="2026-07-09",
    )
    tpex_point = _parse_close_supplement_row(
        ["6488", "環球晶", "0", "0", "0", "0", "510", "0", "櫃檯買賣中心"],
        _TWSE_CLOSE_FIELDS,
        record_date="2026-07-09",
    )

    assert twse_point is not None
    assert twse_point.market == "TWSE"
    assert twse_point.close_price == pytest.approx(185.0)
    assert tpex_point is not None
    assert tpex_point.market == "TPEx"
    assert tpex_point.close_price == pytest.approx(510.0)
    assert _close_supplement_market("集中市場") == "TWSE"
    assert _close_supplement_market("櫃檯買賣中心") == "TPEx"
    assert _close_supplement_market("unknown") is None


def test_merge_daily_points_returns_none_when_dates_or_symbols_mismatch() -> None:
    margin = _margin_point()
    borrowed = _borrowed_point(date_value="2026-07-08")
    other_symbol = _borrowed_point(symbol="2330", name="台積電")

    assert _merge_daily_points(margin_point=margin, borrowed_point=borrowed, close_point=None) is None
    assert (
        _merge_daily_points(margin_point=margin, borrowed_point=other_symbol, close_point=None)
        is None
    )


def test_merge_daily_points_keeps_margin_when_borrowed_short_is_absent() -> None:
    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=None,
        close_point=None,
    )

    assert record is not None
    assert record.margin_balance_lots == pytest.approx(31_339.0)
    assert record.short_margin_balance_lots == pytest.approx(0.0)
    assert record.borrowed_short_balance_lots is None
    assert record.borrowed_short_balance_change_lots is None


def test_merge_daily_points_keeps_borrowed_short_when_margin_is_absent() -> None:
    record = _merge_daily_points(
        margin_point=None,
        borrowed_point=_borrowed_point(),
        close_point=_close_point(),
    )

    assert record is not None
    assert record.margin_balance_lots is None
    assert record.short_margin_balance_lots is None
    assert record.borrowed_short_balance_lots == pytest.approx(12_345.0)
    assert record.borrowed_short_balance_change_lots == pytest.approx(141.0)
    assert record.borrowed_short_balance_change_amount_10k == 2609


def test_merge_daily_points_normalizes_explicit_zero_borrowed_short_balance() -> None:
    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=_borrowed_point(current_shares=0, previous_shares=0),
        close_point=_close_point(),
    )

    assert record is not None
    assert record.borrowed_short_balance_lots == pytest.approx(0.0)
    assert record.borrowed_short_balance_change_lots == pytest.approx(0.0)
    assert record.borrowed_short_balance_change_amount_10k == 0


def test_borrowed_short_change_amount_rounds_negative_values_to_nearest_int() -> None:
    assert _round_nearest_int(-2608.5) == -2609
    assert _round_nearest_int(-2608.4) == -2608

    record = _merge_daily_points(
        margin_point=_margin_point(),
        borrowed_point=_borrowed_point(
            current_shares=12_204_000,
            previous_shares=12_345_000,
        ),
        close_point=_close_point(),
    )

    assert record is not None
    assert record.borrowed_short_balance_change_lots == pytest.approx(-141.0)
    assert record.borrowed_short_balance_change_amount_10k == -2609


def test_service_returns_empty_history_when_symbol_has_no_available_rows() -> None:
    stock = _nan_ya_stock()

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda target_date: {
            "date": target_date.strftime("%Y%m%d"),
            "fields": ["placeholder"],
            "data": [["2330", "台積電", "0", "0", "0", "1,000", "1,100", "0", "0", "0", "0", "0", "0", "0", "0", ""]],
        },
        twse_borrowed_short_fetcher=lambda target_date: {
            "date": target_date.strftime("%Y%m%d"),
            "fields": ["placeholder"],
            "data": [["2330", "台積電", "0", "0", "0", "0", "0", "0", "1,000", "0", "0", "0", "1,100", "0", " "]],
        },
        twse_close_fetcher=lambda target_date: {
            "date": target_date.strftime("%Y%m%d"),
            "fields": _TWSE_CLOSE_FIELDS,
            "data": [["2330", "台積電", "0", "0", "0", "0", "100", "0", "集中市場"]],
        },
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=2)

    assert result.latest_date is None
    assert result.summary is None
    assert result.history == ()


def test_service_raises_for_missing_stock() -> None:
    service = TaiwanMarginShortService(quote_source=FakeQuoteSource(None))
    with pytest.raises(ValueError, match="Stock not found"):
        service.get_margin_short("TW:0000")


@pytest.mark.parametrize(
    ("stock_id", "symbol", "name"),
    [
        ("US:MSFT", "MSFT", "Microsoft"),
        ("HK:0700", "0700", "Tencent"),
    ],
)
def test_service_rejects_non_taiwan_markets(stock_id: str, symbol: str, name: str) -> None:
    foreign = stock_record(
        stock_id,
        market=stock_id.split(":", 1)[0],
        symbol=symbol,
        name=name,
        exchange="FOREIGN",
        currency="USD",
        timezone="UTC",
    )
    service = TaiwanMarginShortService(quote_source=FakeQuoteSource(foreign))
    with pytest.raises(ValueError, match="only available for TW stocks"):
        service.get_margin_short(stock_id)


def test_service_dedupes_history_when_holiday_probes_resolve_to_same_trading_date() -> None:
    stock = _nan_ya_stock()

    payloads = {
        date(2026, 7, 13): (
            {**_twse_margin_payload(date(2026, 7, 10)), "date": "20260710"},
            {**_twse_short_payload(date(2026, 7, 10)), "date": "20260710"},
            {**_twse_close_payload(date(2026, 7, 10)), "date": "20260710"},
        ),
        date(2026, 7, 10): (
            _twse_margin_payload(date(2026, 7, 10)),
            _twse_short_payload(date(2026, 7, 10)),
            _twse_close_payload(date(2026, 7, 10)),
        ),
        date(2026, 7, 9): (
            _twse_margin_payload(date(2026, 7, 9), margin_lots="31,000"),
            _twse_short_payload(date(2026, 7, 9)),
            _twse_close_payload(date(2026, 7, 9), price="184"),
        ),
    }

    def pick(target_date: date, index: int):
        if target_date in payloads:
            return payloads[target_date][index]
        return {"stat": "查無資料", "data": []}

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda target_date: pick(target_date, 0),
        twse_borrowed_short_fetcher=lambda target_date: pick(target_date, 1),
        twse_close_fetcher=lambda target_date: pick(target_date, 2),
        now_provider=lambda: datetime(2026, 7, 14, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=2)

    assert [item.date for item in result.history] == ["2026-07-09", "2026-07-10"]
    assert result.latest_date == "2026-07-10"
    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(31_339.0)


def test_service_skips_weekend_candidate_dates_without_fetching() -> None:
    stock = _nan_ya_stock()
    fetched_dates: list[date] = []

    def tracking_fetcher(target_date: date):
        fetched_dates.append(target_date)
        return _twse_margin_payload(target_date)

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=tracking_fetcher,
        twse_borrowed_short_fetcher=_twse_short_payload,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 12, 1, 0, tzinfo=UTC),
    )

    service.get_margin_short("TW:1303", days=2)

    assert fetched_dates
    assert all(day.weekday() < 5 for day in fetched_dates)


def test_service_reuses_short_lived_result_cache() -> None:
    stock = _nan_ya_stock()
    calls = {"count": 0}

    def counting_fetcher(target_date: date):
        calls["count"] += 1
        return _twse_margin_payload(target_date)

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=counting_fetcher,
        twse_borrowed_short_fetcher=_twse_short_payload,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    first = service.get_margin_short("TW:1303", days=1)
    first_count = calls["count"]
    assert first_count > 0

    second = service.get_margin_short("TW:1303", days=1)

    assert first.latest_date == second.latest_date
    assert calls["count"] == first_count


def test_service_raises_when_core_feeds_return_malformed_json_bodies() -> None:
    stock = _nan_ya_stock()
    malformed = {"stat": "error", "message": "schema changed"}

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: malformed,
        twse_borrowed_short_fetcher=lambda _target_date: malformed,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short official response could not be interpreted.",
    ):
        service.get_margin_short("TW:1303", days=1)


def test_fetch_tpex_dated_payload_requests_json_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_fetch_tpex_data(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"date": "2026/07/08", "tables": []}

    monkeypatch.setattr(
        "app.services.tw_margin_short_service.fetch_tpex_data",
        fake_fetch_tpex_data,
    )

    _fetch_tpex_dated_payload(
        "https://www.tpex.org.tw/www/zh-tw/margin/balance",
        date(2026, 7, 8),
        operation_name="tw_margin_short_tpex_margin_daily",
    )

    assert captured["kwargs"]["params"] == {
        "response": "json",
        "date": "2026/07/08",
    }


def test_fetch_twse_dated_payload_requests_select_type_and_yyyymmdd(monkeypatch) -> None:
    from app.services.tw_margin_short_service import _fetch_twse_dated_payload

    captured: dict[str, object] = {}

    def fake_fetch_twse_data(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {"stat": "OK", "tables": []}

    monkeypatch.setattr(
        "app.services.tw_margin_short_service.fetch_twse_data",
        fake_fetch_twse_data,
    )

    _fetch_twse_dated_payload(
        "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
        date(2026, 7, 31),
        operation_name="tw_margin_short_twse_margin_daily",
        select_type="ALL",
    )

    assert captured["url"].endswith("/MI_MARGN")
    assert captured["kwargs"]["params"] == {
        "response": "json",
        "date": "20260731",
        "selectType": "ALL",
    }


def test_service_keeps_borrowed_short_when_margin_schema_is_invalid() -> None:
    stock = _nan_ya_stock()
    close_calls = {"count": 0}

    def close_fetcher(target_date: date):
        close_calls["count"] += 1
        return _twse_close_payload(target_date)

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: {"stat": "OK", "date": "20260708"},
        twse_borrowed_short_fetcher=_twse_short_payload,
        twse_close_fetcher=close_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=1)

    assert result.summary is not None
    assert result.summary.margin_balance_lots is None
    assert result.summary.borrowed_short_balance_lots == pytest.approx(12_345.0)
    assert close_calls["count"] == 1


def test_service_skips_optional_close_when_borrowed_short_point_missing() -> None:
    stock = _nan_ya_stock()
    close_calls = {"count": 0}

    def close_fetcher(target_date: date):
        close_calls["count"] += 1
        return _twse_close_payload(target_date)

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=_twse_margin_payload,
        twse_borrowed_short_fetcher=lambda _target_date: {
            "date": "20260708",
            "fields": ["placeholder"],
            "data": [["2330", "台積電", "0", "0", "0", "0", "0", "0", "1,000", "0", "0", "0", "1,100", "0", " "]],
        },
        twse_close_fetcher=close_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=1)

    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(31_339.0)
    assert result.summary.borrowed_short_balance_lots is None
    assert close_calls["count"] == 0


def test_service_disables_optional_close_after_first_hard_failure() -> None:
    stock = _nan_ya_stock()
    close_calls = {"count": 0}

    def close_fetcher(_target_date: date):
        close_calls["count"] += 1
        raise HttpFetchError("close unavailable")

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=_twse_margin_payload,
        twse_borrowed_short_fetcher=_twse_short_payload,
        twse_close_fetcher=close_fetcher,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=2)

    assert result.summary is not None
    assert result.summary.close_price is None
    assert result.summary.borrowed_short_balance_change_amount_10k is None
    assert close_calls["count"] == 1


def test_core_request_succeeded_requires_found_or_both_soft_empty() -> None:
    found = _SourceOutcome(status="found")
    absent = _SourceOutcome(status="symbol_absent")
    no_data = _SourceOutcome(status="no_data")
    schema_error = _SourceOutcome(status="schema_error")
    http_error = _SourceOutcome(status="http_error")
    parse_error = _SourceOutcome(status="parse_error")

    assert _core_request_succeeded(found, http_error) is True
    assert _core_request_succeeded(schema_error, found) is True
    assert _core_request_succeeded(absent, no_data) is True
    assert _core_request_succeeded(schema_error, absent) is False
    assert _core_request_succeeded(http_error, no_data) is False
    assert _core_request_succeeded(parse_error, absent) is False
    assert _core_request_succeeded(http_error, schema_error) is False


def test_service_raises_when_hard_failure_mixes_with_soft_absence() -> None:
    stock = _nan_ya_stock()

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: {"stat": "OK", "date": "20260708"},
        twse_borrowed_short_fetcher=lambda target_date: {
            "date": target_date.strftime("%Y%m%d"),
            "fields": ["placeholder"],
            "data": [["2330", "台積電", "0", "0", "0", "0", "0", "0", "1,000", "0", "0", "0", "1,100", "0", " "]],
        },
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short official response could not be interpreted.",
    ):
        service.get_margin_short("TW:1303", days=1)


def test_extract_twse_marks_matching_unparseable_row_as_parse_error() -> None:
    payload = {
        "date": "20260708",
        "fields": ["placeholder"],
        "data": [
            [
                "1303",
                "南亞",
                "0",
                "0",
                "0",
                "0",
                "-",
                "0",
                "0",
                "0",
                "0",
                "0",
                "-",
                "0",
                "0",
                "",
            ]
        ],
    }

    outcome = _extract_twse_margin_point(
        payload,
        symbol="1303",
        current_date=date(2026, 7, 8),
    )

    assert outcome.status == "parse_error"
    assert outcome.reason == "symbol_row_unparseable"
    assert outcome.point is None


def test_extract_twse_borrowed_short_marks_matching_unparseable_row_as_parse_error() -> None:
    payload = {
        "date": "20260708",
        "fields": ["placeholder"],
        "data": [
            [
                "1303",
                "南亞",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
                "bad",
                "0",
                "0",
                "0",
                "-",
                "0",
                " ",
            ]
        ],
    }

    outcome = _extract_twse_borrowed_short_point(
        payload,
        symbol="1303",
        current_date=date(2026, 7, 8),
    )

    assert outcome.status == "parse_error"
    assert outcome.reason == "symbol_row_unparseable"


def test_parse_all_margin_short_records_keeps_partial_symbol_rows() -> None:
    records = parse_all_margin_short_records(
        market="TWSE",
        current_date=date(2026, 7, 9),
        margin_payload={
            "date": "20260709",
            "fields": ["placeholder"],
            "data": [
                [
                    "1303",
                    "南亞",
                    "0",
                    "0",
                    "0",
                    "0",
                    "31,339",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "",
                ]
            ],
        },
        borrowed_payload={
            "date": "20260709",
            "fields": ["placeholder"],
            "data": [
                [
                    "2330",
                    "台積電",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    "1,000,000",
                    "0",
                    "0",
                    "0",
                    "1,100,000",
                    "0",
                    " ",
                ]
            ],
        },
        close_payload=None,
    )

    by_symbol = {record.symbol: record for record in records}
    assert set(by_symbol) == {"1303", "2330"}
    assert by_symbol["1303"].margin_balance_lots == pytest.approx(31_339.0)
    assert by_symbol["1303"].borrowed_short_balance_lots is None
    assert by_symbol["2330"].margin_balance_lots is None
    assert by_symbol["2330"].borrowed_short_balance_lots == pytest.approx(1_100.0)


def test_service_structured_logs_include_stock_id_and_http_outcome(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def capture_info(message: str, *args, extra=None, **kwargs) -> None:
        captured.append({"message": message, "extra": dict(extra or {})})

    monkeypatch.setattr(
        "app.services.tw_margin_short_service.logger.info",
        capture_info,
    )

    stock = _nan_ya_stock()
    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=_twse_margin_payload,
        twse_borrowed_short_fetcher=_twse_short_payload,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    service.get_margin_short("TW:1303", days=1)

    extras = [entry["extra"] for entry in captured if isinstance(entry["extra"], dict)]
    assert any(extra.get("event") == "tw_margin_short.daily_merge" for extra in extras)
    assert any(extra.get("stock_id") == "TW:1303" for extra in extras)
    assert any(extra.get("http_success") is True for extra in extras)
    assert any(extra.get("event") == "tw_margin_short.source_parse" for extra in extras)


def _raise_http_fetch_error() -> object:
    raise HttpFetchError("upstream unavailable")


def _raise_http_fetch_error_with(
    *,
    classification: str,
    status_code: int | None = None,
    content_type: str | None = None,
) -> object:
    raise HttpFetchError(
        "classified upstream failure",
        status_code=status_code,
        content_type=content_type,
        classification=classification,  # type: ignore[arg-type]
    )


def _globalwafers_stock():
    return stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="GlobalWafers",
        name_zh_tw="環球晶",
    )


def test_twse_stock_table_prefers_per_symbol_tables_envelope() -> None:
    payload = _load_fixture("twse_mi_margn_tables_success.json")
    table = _twse_stock_table(payload)

    assert table is not None
    assert "代號" in table["fields"]
    assert table["data"][0][0] == "1303"


def test_extract_twse_margin_point_reads_live_mi_margn_tables_fixture() -> None:
    outcome = _extract_twse_margin_point(
        _load_fixture("twse_mi_margn_tables_success.json"),
        symbol="1303",
        current_date=date(2026, 7, 31),
    )

    assert outcome.status == "found"
    assert outcome.point is not None
    assert outcome.point.margin_balance_lots == pytest.approx(33_805.0)
    assert outcome.point.short_margin_balance_lots == pytest.approx(3_013.0)


def test_extract_twse_borrowed_short_point_reads_twt93u_fixture() -> None:
    outcome = _extract_twse_borrowed_short_point(
        _load_fixture("twse_twt93u_success.json"),
        symbol="1303",
        current_date=date(2026, 7, 31),
    )

    assert outcome.status == "found"
    assert outcome.point is not None
    assert outcome.point.borrowed_short_balance_shares == 12_345_000
    assert outcome.point.previous_borrowed_short_balance_shares == 12_204_000


def test_resolve_twse_margin_columns_uses_duplicate_today_balance_labels() -> None:
    payload = _load_fixture("twse_mi_margn_tables_success.json")
    fields = payload["tables"][1]["fields"]
    columns = resolve_twse_margin_columns(fields)

    assert columns == (0, 1, 6, 12)


def test_service_parses_twse_tables_envelope_end_to_end() -> None:
    stock = _nan_ya_stock()
    margin_payload = _load_fixture("twse_mi_margn_tables_success.json")
    borrowed_payload = _load_fixture("twse_twt93u_success.json")

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: margin_payload,
        twse_borrowed_short_fetcher=lambda _target_date: borrowed_payload,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=1)

    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(33_805.0)
    assert result.summary.short_margin_balance_lots == pytest.approx(3_013.0)
    assert result.summary.borrowed_short_balance_lots == pytest.approx(12_345.0)


def test_service_parses_tpex_fixture_payloads_end_to_end() -> None:
    stock = _globalwafers_stock()

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        tpex_margin_fetcher=lambda _target_date: _load_fixture(
            "tpex_margin_balance_success.json"
        ),
        tpex_borrowed_short_fetcher=lambda _target_date: _load_fixture(
            "tpex_margin_sbl_success.json"
        ),
        twse_close_fetcher=lambda target_date: _twse_close_payload(
            target_date,
            price="510",
            market_label="櫃檯買賣中心",
        ),
        now_provider=lambda: datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:6488", days=1)

    assert result.market == "TPEx"
    assert result.summary is not None
    assert result.summary.margin_balance_lots == pytest.approx(10_200.0)
    assert result.summary.borrowed_short_balance_lots == pytest.approx(2_100.0)


def test_service_returns_empty_success_for_official_no_data() -> None:
    stock = _nan_ya_stock()
    no_data = _load_fixture("twse_official_no_data.json")

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: no_data,
        twse_borrowed_short_fetcher=lambda _target_date: no_data,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:1303", days=2)

    assert result.latest_date is None
    assert result.summary is None
    assert result.history == ()
    assert result.empty_reason == "not_published"


def test_service_returns_empty_success_for_tpex_official_no_data() -> None:
    stock = _globalwafers_stock()
    no_data = _load_fixture("tpex_official_no_data.json")

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        tpex_margin_fetcher=lambda _target_date: no_data,
        tpex_borrowed_short_fetcher=lambda _target_date: no_data,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:6488", days=2)

    assert result.history == ()
    assert result.summary is None
    assert result.empty_reason == "not_published"


def test_service_raises_when_soft_empty_days_mix_with_schema_failures() -> None:
    """Holiday no-data must not mask weekday schema failures into empty 200."""
    stock = _nan_ya_stock()
    no_data = _load_fixture("twse_official_no_data.json")
    unexpected = _load_fixture("unexpected_schema.json")

    def fetcher(target_date: date) -> object:
        # Mondays: schema drift. Other weekdays: official no-data (soft success).
        if target_date.weekday() == 0:
            return unexpected
        return no_data

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=fetcher,
        twse_borrowed_short_fetcher=fetcher,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),  # Monday
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short official response could not be interpreted.",
    ):
        service.get_margin_short("TW:1303", days=2)


def test_service_empty_reason_no_data_for_stock_when_symbol_absent() -> None:
    stock = stock_record(
        "TW:9999",
        market="TW",
        symbol="9999",
        name="測試股",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="Test Co",
        name_zh_tw="測試股",
    )
    margin = _load_fixture("twse_mi_margn_tables_success.json")
    borrowed = _load_fixture("twse_twt93u_success.json")

    # Fixtures contain other symbols, not 9999 → symbol_absent soft empty.
    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: margin,
        twse_borrowed_short_fetcher=lambda _target_date: borrowed,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),
    )

    result = service.get_margin_short("TW:9999", days=2)

    assert result.history == ()
    assert result.empty_reason == "no_data_for_stock"


def test_service_raises_schema_error_for_unexpected_fixture_envelope() -> None:
    stock = _nan_ya_stock()
    unexpected = _load_fixture("unexpected_schema.json")

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=lambda _target_date: unexpected,
        twse_borrowed_short_fetcher=lambda _target_date: unexpected,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(
        RuntimeError,
        match="Taiwan margin-short official response could not be interpreted.",
    ):
        service.get_margin_short("TW:1303", days=1)


@pytest.mark.parametrize(
    ("classification", "status_code", "match"),
    [
        ("auth_denied", 401, "official source denied access"),
        ("auth_denied", 403, "official source denied access"),
        ("rate_limited", 429, "official source is rate-limited"),
        ("network_failure", None, "temporarily unavailable from official sources"),
        ("unexpected_content", 200, "could not be interpreted"),
    ],
)
def test_service_raises_classified_http_failures(
    classification: str,
    status_code: int | None,
    match: str,
) -> None:
    stock = _nan_ya_stock()

    def failing(_target_date: date) -> object:
        return _raise_http_fetch_error_with(
            classification=classification,
            status_code=status_code,
            content_type="text/html" if classification == "unexpected_content" else "application/json",
        )

    service = TaiwanMarginShortService(
        quote_source=FakeQuoteSource(stock),
        twse_margin_fetcher=failing,
        twse_borrowed_short_fetcher=failing,
        twse_close_fetcher=_twse_close_payload,
        now_provider=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match=match):
        service.get_margin_short("TW:1303", days=1)


def test_upstream_error_for_scan_prefers_auth_then_rate_limit_then_schema() -> None:
    from app.services.tw_margin_short_service import _ScanDiagnostics

    diagnostics = _ScanDiagnostics(auth_denied=1, rate_limited=3, schema_errors=5, http_errors=9)
    assert "denied access" in _upstream_error_for_scan(diagnostics)

    diagnostics = _ScanDiagnostics(rate_limited=2, schema_errors=5, http_errors=9)
    assert "rate-limited" in _upstream_error_for_scan(diagnostics)

    diagnostics = _ScanDiagnostics(schema_errors=2, parse_errors=1, http_errors=9)
    assert "could not be interpreted" in _upstream_error_for_scan(diagnostics)

    diagnostics = _ScanDiagnostics(unexpected_content=2, http_errors=9)
    assert "could not be interpreted" in _upstream_error_for_scan(diagnostics)

    diagnostics = _ScanDiagnostics(http_errors=2)
    assert "temporarily unavailable" in _upstream_error_for_scan(diagnostics)


def test_http_failure_fixtures_match_classifier_contract() -> None:
    """Wire contract fixtures for 401/403/429/HTML/timeout into classification asserts."""
    from app.services.http_client import classify_http_failure

    for name, expected in (
        ("http_401.json", "auth_denied"),
        ("http_403.json", "auth_denied"),
        ("http_429.json", "rate_limited"),
    ):
        fixture = _load_fixture(name)
        assert isinstance(fixture, dict)
        assert (
            classify_http_failure(
                int(fixture["status_code"]),
                content_type=str(fixture.get("content_type")),
            )
            == expected
        )

    html = _load_fixture("html_response.html")
    assert isinstance(html, str)
    assert "<html" in html.lower()
    assert (
        classify_http_failure(200, content_type="text/html", json_parse_failed=True)
        == "unexpected_content"
    )

    timeout = _load_fixture("network_timeout.json")
    assert isinstance(timeout, dict)
    assert timeout["error_type"] == "ConnectTimeout"
    assert classify_http_failure(None) == "network_failure"


def test_fetch_twse_dated_payload_uses_browser_like_headers(monkeypatch) -> None:
    from app.services import taiwan_market_data
    from app.services.taiwan_market_data import _DEFAULT_HEADERS
    from app.services.tw_margin_short_service import _fetch_twse_dated_payload

    captured: dict[str, object] = {}

    def fake_fetch_json(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return {"stat": "OK", "tables": []}

    monkeypatch.setattr(taiwan_market_data, "fetch_json", fake_fetch_json)
    taiwan_market_data.taiwan_market_data_client.clear_cache()

    _fetch_twse_dated_payload(
        "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
        date(2026, 7, 31),
        operation_name="tw_margin_short_twse_margin_daily",
        select_type="ALL",
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("User-Agent") == _DEFAULT_HEADERS["User-Agent"]
    assert "application/json" in str(headers.get("Accept", ""))


def test_core_request_succeeded_treats_auth_and_rate_limit_as_hard_failures() -> None:
    found = _SourceOutcome(status="found")
    absent = _SourceOutcome(status="symbol_absent")
    auth = _SourceOutcome(status="auth_denied")
    rate = _SourceOutcome(status="rate_limited")

    assert _core_request_succeeded(found, auth) is True
    assert _core_request_succeeded(auth, absent) is False
    assert _core_request_succeeded(rate, absent) is False
    assert _core_request_succeeded(auth, rate) is False
