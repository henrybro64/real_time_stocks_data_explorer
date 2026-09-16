from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.models.stock import StockRecord
from app.services.tdcc_shareholding import parse_tdcc_shareholding_csv, parse_tdcc_shareholding_rows
from app.services.tw_ownership_concentration_service import (
    TwOwnershipConcentrationService,
    parse_mi_qfiis_foreign_ownership,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tw_ownership"


class _FakeQuoteSource:
    def __init__(self, stock: StockRecord | None) -> None:
        self._stock = stock

    def get_stock(self, stock_id: str) -> StockRecord | None:
        if self._stock is None:
            return None
        if stock_id == self._stock.id:
            return self._stock
        return None

    def get_history(self, stock_id: str, range_name: str, *, limit: int | None = None):
        return None


def _tw_stock(stock_id: str = "TW:2330", symbol: str = "2330") -> StockRecord:
    return StockRecord(
        id=stock_id,
        symbol=symbol,
        name="台積電",
        market="TW",
        currency="TWD",
        exchange="TWSE",
        timezone="Asia/Taipei",
    )


def _load_text(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _load_json(name: str) -> object:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_tdcc_fixture_sums_400_lots_levels_and_skips_total() -> None:
    points = parse_tdcc_shareholding_csv(_load_text("tdcc_2330_multi_date.csv"), symbol="2330")
    assert [point.date for point in points] == ["2026-03-13", "2026-06-12", "2026-09-11"]
    assert points[-1].holders_400_lots_or_more_percent == pytest.approx(63.46)


def test_parse_tdcc_dedupes_by_date_and_sorts_ascending() -> None:
    csv_text = _load_text("tdcc_2330_multi_date.csv")
    # Duplicate latest block should collapse to one date without double-counting %.
    points = parse_tdcc_shareholding_csv(csv_text + csv_text, symbol="2330")
    assert [point.date for point in points] == ["2026-03-13", "2026-06-12", "2026-09-11"]
    assert points[-1].holders_400_lots_or_more_percent == pytest.approx(63.46)


def test_parse_tdcc_skips_malformed_rows() -> None:
    points = parse_tdcc_shareholding_csv(_load_text("tdcc_malformed_rows.csv"), symbol="2330")
    assert len(points) == 1
    assert points[0].holders_400_lots_or_more_percent == pytest.approx(50.0)


def test_parse_tdcc_shares_fallback_when_percent_missing() -> None:
    points = parse_tdcc_shareholding_csv(_load_text("tdcc_shares_fallback.csv"), symbol="2330")
    assert len(points) == 1
    assert points[0].holders_400_lots_or_more_percent == pytest.approx(100.0)


def test_parse_tdcc_rows_list_of_dicts() -> None:
    rows = [
        {
            "資料日期": "20260911",
            "證券代號": "2330",
            "持股分級": 12,
            "股數": 100,
            "占集保庫存數比例%": 10,
        },
        {
            "資料日期": "20260911",
            "證券代號": "2330",
            "持股分級": 15,
            "股數": 100,
            "占集保庫存數比例%": 20,
        },
        {
            "資料日期": "20260911",
            "證券代號": "2330",
            "持股分級": 999,
            "股數": 200,
            "占集保庫存數比例%": 100,
        },
    ]
    points = parse_tdcc_shareholding_rows(rows, symbol="2330")
    assert points[0].holders_400_lots_or_more_percent == pytest.approx(30.0)


def test_parse_mi_qfiis_foreign_ownership() -> None:
    parsed = parse_mi_qfiis_foreign_ownership(_load_json("mi_qfiis_success.json"))
    assert parsed is not None
    assert parsed["2330"] == pytest.approx(18.21)
    assert parse_mi_qfiis_foreign_ownership(_load_json("mi_qfiis_no_data.json")) is None


def test_ownership_service_range_filter_and_null_series() -> None:
    csv_text = _load_text("tdcc_2330_multi_date.csv")
    mi_payload = _load_json("mi_qfiis_success.json")

    def tdcc_fetcher() -> str:
        return csv_text

    def foreign_fetcher(current: date, _board: str) -> dict[str, float] | None:
        if current.isoformat() == "2026-09-11":
            return parse_mi_qfiis_foreign_ownership(mi_payload)
        return None

    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_csv_fetcher=tdcc_fetcher,
        foreign_ownership_fetcher=foreign_fetcher,
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )

    result = service.get_ownership_concentration("TW:2330", range_name="3M")
    assert result.range == "3M"
    assert result.source == "tdcc_portal+twse_mi_qfiis"
    assert result.stale is False
    assert result.as_of == "2026-09-11"
    # 3M from 2026-09-12 excludes March point.
    assert [point.date for point in result.points] == ["2026-06-12", "2026-09-11"]
    latest = result.points[-1]
    assert latest.holders_400_lots_or_more_percent == pytest.approx(63.46)
    assert latest.foreign_ownership_percent == pytest.approx(18.21)
    assert latest.investment_trust_ownership_percent is None
    assert latest.dealer_ownership_percent is None
    assert latest.director_ownership_percent is None
    assert latest.weekly_close is None


def test_ownership_service_cache_hit() -> None:
    calls = {"tdcc": 0}
    csv_text = _load_text("tdcc_2330_multi_date.csv")

    def tdcc_fetcher() -> str:
        calls["tdcc"] += 1
        return csv_text

    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_csv_fetcher=tdcc_fetcher,
        foreign_ownership_fetcher=lambda _current, _board: {"2330": 10.0},
        close_history_fetcher=lambda _stock_id, _range: {"2026-09-11": 88.1},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    first = service.get_ownership_concentration("TW:2330", range_name="1Y")
    second = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert calls["tdcc"] == 1
    assert first.points[-1].weekly_close == pytest.approx(88.1)
    assert second.stale is False


def test_ownership_service_stale_fallback_on_upstream_failure() -> None:
    csv_text = _load_text("tdcc_2330_multi_date.csv")
    state = {"fail": False}

    def tdcc_fetcher() -> str:
        if state["fail"]:
            raise RuntimeError("boom")
        return csv_text

    now = {"value": datetime(2026, 9, 12, 10, 0, tzinfo=UTC)}
    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_csv_fetcher=tdcc_fetcher,
        foreign_ownership_fetcher=lambda _current, _board: {"2330": 11.0},
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: now["value"],
        result_cache_ttl=timedelta(hours=12),
    )
    fresh = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert fresh.stale is False

    state["fail"] = True
    now["value"] = now["value"] + timedelta(hours=13)
    stale = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert stale.stale is True
    assert stale.points == fresh.points


def test_ownership_service_rejects_non_tw_stock() -> None:
    us_stock = StockRecord(
        id="US:AAPL",
        symbol="AAPL",
        name="Apple",
        market="US",
        currency="USD",
        exchange="NASDAQ",
        timezone="America/New_York",
    )
    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(us_stock),
        tdcc_csv_fetcher=lambda: "",
        foreign_ownership_fetcher=lambda _current, _board: None,
        close_history_fetcher=lambda _stock_id, _range: {},
    )
    with pytest.raises(ValueError, match="only available for TW stocks"):
        service.get_ownership_concentration("US:AAPL", range_name="1Y")


def test_ownership_service_raises_when_no_cache_and_upstream_fails() -> None:
    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_csv_fetcher=lambda: (_ for _ in ()).throw(RuntimeError("down")),
        foreign_ownership_fetcher=lambda _current, _board: None,
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError, match="upstream temporarily unavailable"):
        service.get_ownership_concentration("TW:2330", range_name="1Y")


def test_normalize_percent_values_from_string_rows() -> None:
    # Ensure parser path still works when DictReader yields strings.
    text = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260911,2330,12,1,\"1,000\",10.5\n20260911,2330,15,1,2000,20.5\n"
    points = parse_tdcc_shareholding_csv(text, symbol="2330")
    assert points[0].holders_400_lots_or_more_percent == pytest.approx(31.0)


def test_parse_tdcc_portal_html_sums_400_lots_levels() -> None:
    from app.services.tdcc_shareholding import (
        extract_tdcc_synchronizer_token,
        parse_tdcc_qry_stock_html,
    )

    html = _load_text("tdcc_qry_stock_2330_20260904.html")
    point = parse_tdcc_qry_stock_html(html, symbol="2330", point_date="20260904")
    assert point is not None
    assert point.date == "2026-09-04"
    assert point.holders_400_lots_or_more_percent == pytest.approx(63.46)
    assert extract_tdcc_synchronizer_token(html) == "next-token-abc"


def test_ownership_service_uses_injected_tdcc_history_for_multi_week() -> None:
    from app.services.tdcc_shareholding import TdccConcentrationPoint

    def history_fetcher(symbol: str, start: date, end: date):
        assert symbol == "2330"
        return [
            TdccConcentrationPoint("2026-06-12", "2330", 61.5),
            TdccConcentrationPoint("2026-09-04", "2330", 63.46),
        ]

    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_history_fetcher=history_fetcher,
        foreign_ownership_fetcher=lambda current, _board: (
            {"2330": 18.21} if current.isoformat() == "2026-09-04" else None
        ),
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    result = service.get_ownership_concentration("TW:2330", range_name="3M")
    assert [point.date for point in result.points] == ["2026-06-12", "2026-09-04"]
    assert result.points[-1].holders_400_lots_or_more_percent == pytest.approx(63.46)
    assert result.points[-1].foreign_ownership_percent == pytest.approx(18.21)


def test_parse_tpex_qfii_foreign_ownership() -> None:
    from app.services.tw_ownership_concentration_service import parse_tpex_qfii_foreign_ownership

    parsed = parse_tpex_qfii_foreign_ownership(_load_json("tpex_qfii_sample.json"))
    assert parsed is not None
    assert parsed["8455"] == pytest.approx(87.84)


def test_foreign_probes_prefer_newest_dates_within_cap() -> None:
    from app.services.tdcc_shareholding import TdccConcentrationPoint

    probed: list[str] = []

    def history_fetcher(symbol: str, start: date, end: date):
        return [
            TdccConcentrationPoint((end - timedelta(days=offset * 7)).isoformat(), symbol, 60.0)
            for offset in range(40)
        ]

    def foreign_fetcher(current: date, _board: str) -> dict[str, float] | None:
        probed.append(current.isoformat())
        return {"2330": 10.0}

    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_history_fetcher=history_fetcher,
        foreign_ownership_fetcher=foreign_fetcher,
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
        max_foreign_probes=5,
    )
    result = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert len(probed) == 5
    assert probed == sorted(probed, reverse=True)
    assert result.points[-1].foreign_ownership_percent == pytest.approx(10.0)


def test_one_foreign_probe_failure_keeps_other_dates() -> None:
    from app.services.http_client import HttpFetchError
    from app.services.tdcc_shareholding import TdccConcentrationPoint

    def history_fetcher(symbol: str, start: date, end: date):
        return [
            TdccConcentrationPoint("2026-06-12", symbol, 61.5),
            TdccConcentrationPoint("2026-09-04", symbol, 63.46),
        ]

    def foreign_fetcher(current: date, _board: str) -> dict[str, float] | None:
        if current.isoformat() == "2026-06-12":
            raise HttpFetchError("timeout", status_code=504)
        return {"2330": 18.21}

    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_history_fetcher=history_fetcher,
        foreign_ownership_fetcher=foreign_fetcher,
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    result = service.get_ownership_concentration("TW:2330", range_name="3M")
    by_date = {point.date: point.foreign_ownership_percent for point in result.points}
    assert by_date["2026-06-12"] is None
    assert by_date["2026-09-04"] == pytest.approx(18.21)


def test_stale_fallback_backs_off_before_retrying() -> None:
    csv_text = _load_text("tdcc_2330_multi_date.csv")
    calls = {"count": 0}

    def tdcc_fetcher() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return csv_text
        raise RuntimeError("boom")

    now = {"value": datetime(2026, 9, 12, 10, 0, tzinfo=UTC)}
    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        tdcc_csv_fetcher=tdcc_fetcher,
        foreign_ownership_fetcher=lambda _current, _board: {"2330": 11.0},
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: now["value"],
        result_cache_ttl=timedelta(hours=12),
        stale_refresh_backoff=timedelta(minutes=15),
    )
    fresh = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert fresh.stale is False

    now["value"] = now["value"] + timedelta(hours=13)
    stale = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert stale.stale is True
    assert calls["count"] == 2

    now["value"] = now["value"] + timedelta(minutes=5)
    again = service.get_ownership_concentration("TW:2330", range_name="1Y")
    assert again.stale is True
    assert calls["count"] == 2


def test_tpex_stock_uses_tpex_source_label() -> None:
    stock = StockRecord(
        id="TW:8455",
        symbol="8455",
        name="大拓-KY",
        market="TW",
        currency="TWD",
        exchange="TPEx",
        timezone="Asia/Taipei",
    )
    service = TwOwnershipConcentrationService(
        quote_source=_FakeQuoteSource(stock),
        tdcc_csv_fetcher=lambda: "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260911,8455,15,1,1,50.00\n20260911,8455,16,1,1,50.00\n",
        foreign_ownership_fetcher=lambda _current, board: (
            {"8455": 87.84} if board == "TPEx" else None
        ),
        close_history_fetcher=lambda _stock_id, _range: {},
        now_provider=lambda: datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    result = service.get_ownership_concentration("TW:8455", range_name="3M")
    assert result.source == "tdcc_portal+tpex_qfii"
    assert result.points[-1].foreign_ownership_percent == pytest.approx(87.84)


def test_select_portal_dates_newest_first_and_capped() -> None:
    from app.services.tw_ownership_concentration_service import _select_portal_dates

    selected = _select_portal_dates(
        ["2026-01-02", "2026-09-04", "2026-06-12", "2025-01-03"],
        start_date=date(2026, 3, 1),
        end_date=date(2026, 9, 12),
        max_weeks=2,
    )
    assert selected == ["2026-09-04", "2026-06-12"]


def test_missing_synchronizer_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services.tw_ownership_concentration_service import _fetch_tdcc_portal_history

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, _url: str):
            return _FakeResponse("<html><body>no token here</body></html>")

        def post(self, *_args, **_kwargs):
            raise AssertionError("POST must not run without a CSRF token")

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    with pytest.raises(RuntimeError, match="upstream temporarily unavailable"):
        _fetch_tdcc_portal_history(
            symbol="2330",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 9, 12),
            max_weeks=4,
        )
