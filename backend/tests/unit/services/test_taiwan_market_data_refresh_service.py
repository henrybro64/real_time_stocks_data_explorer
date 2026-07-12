from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.repositories.taiwan_institutional_daily_repository import (
    InstitutionalDailyUpsertStats,
)
from app.repositories.taiwan_margin_short_daily_repository import (
    MarginShortDailyUpsertStats,
)
from app.services.http_client import HttpFetchError
from app.services.taiwan_market_data_refresh_service import TaiwanMarketDataRefreshService


class FakeInstitutionalRepo:
    def __init__(self) -> None:
        self.available = True
        self.calls: list[tuple[str, str, int]] = []

    def is_available(self) -> bool:
        return self.available

    def upsert_records(self, *, market, source, records):
        self.calls.append((market, source, len(records)))
        return InstitutionalDailyUpsertStats(inserted=len(records), updated=0)


class FakeMarginRepo:
    def __init__(self) -> None:
        self.available = True
        self.calls: list[tuple[str, str, int]] = []

    def is_available(self) -> bool:
        return self.available

    def upsert_records(self, *, market, source, records):
        self.calls.append((market, source, len(records)))
        return MarginShortDailyUpsertStats(inserted=len(records), updated=0)


def _twse_institutional_payload(symbol: str = "2330") -> dict:
    return {
        "date": "20260709",
        "fields": [
            "證券代號",
            "證券名稱",
            "外陸資買賣超股數(不含外資自營商)",
            "投信買賣超股數",
            "自營商買賣超股數",
            "自營商買賣超股數(自行買賣)",
            "自營商買賣超股數(避險)",
        ],
        "data": [
            [symbol, "台積電", "1,000", "200", "50", "20", "30"],
        ],
    }


def _tpex_institutional_payload(symbol: str = "6488") -> dict:
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
        symbol,
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
    return {
        "date": "2026/07/09",
        "tables": [{"date": "115/07/09", "fields": fields, "data": [row]}],
    }


def _no_data_payload() -> dict:
    return {"stat": "很抱歉，沒有符合條件的資料!"}


def _twse_margin_payload() -> dict:
    return {
        "date": "20260709",
        "fields": ["placeholder"],
        "data": [
            [
                "2330",
                "台積電",
                "0",
                "0",
                "0",
                "31,000",
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
    }


def _twse_borrowed_payload() -> dict:
    return {
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
                "12,204,000",
                "0",
                "0",
                "0",
                "12,345,000",
                "0",
                " ",
            ]
        ],
    }


def _empty_close_payload() -> dict:
    return {
        "date": "20260709",
        "fields": [
            "證券代號",
            "證券名稱",
            "前日借券餘額(1)股",
            "本日異動股借券(2)",
            "本日異動股還券(3)",
            "本日借券餘額股(4)=(1)+(2)-(3)",
            "本日收盤價(5)單位：元",
            "借券餘額市值單位：元(6)=(4)*(5)",
            "市場別",
        ],
        "data": [],
    }


def _service(
    *,
    institutional_repo=None,
    margin_repo=None,
    now: datetime | None = None,
    twse_institutional=None,
    tpex_institutional=None,
    twse_margin=None,
    twse_borrowed=None,
    twse_close=None,
    tpex_margin=None,
    tpex_borrowed=None,
) -> TaiwanMarketDataRefreshService:
    fixed_now = now or datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    return TaiwanMarketDataRefreshService(
        institutional_repository=institutional_repo or FakeInstitutionalRepo(),
        margin_short_repository=margin_repo or FakeMarginRepo(),
        now_provider=lambda: fixed_now,
        twse_institutional_fetcher=twse_institutional
        or (lambda _d: _twse_institutional_payload()),
        tpex_institutional_fetcher=tpex_institutional
        or (lambda _d: _tpex_institutional_payload()),
        twse_margin_fetcher=twse_margin or (lambda _d: _twse_margin_payload()),
        twse_borrowed_short_fetcher=twse_borrowed
        or (lambda _d: _twse_borrowed_payload()),
        twse_close_fetcher=twse_close or (lambda _d: _empty_close_payload()),
        tpex_margin_fetcher=tpex_margin or (lambda _d: _no_data_payload()),
        tpex_borrowed_short_fetcher=tpex_borrowed or (lambda _d: _no_data_payload()),
    )


def test_refresh_skips_no_data_days_and_continues() -> None:
    institutional_repo = FakeInstitutionalRepo()
    requested_dates: list[date] = []

    def twse_institutional(current_date: date):
        requested_dates.append(current_date)
        if current_date == date(2026, 7, 9):
            return _no_data_payload()
        return _twse_institutional_payload()

    service = _service(
        institutional_repo=institutional_repo,
        twse_institutional=twse_institutional,
        tpex_institutional=lambda _d: _no_data_payload(),
        twse_margin=lambda _d: _no_data_payload(),
        twse_borrowed=lambda _d: _no_data_payload(),
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )

    summary = service.refresh(days=2, trigger="test", include_margin_short=False)

    assert summary.error_count == 0
    assert any(
        item.market == "TWSE"
        and item.requested_date == "2026-07-09"
        and item.skipped_no_data
        for item in summary.results
    )
    assert institutional_repo.calls
    assert requested_dates[0] == date(2026, 7, 10)


def test_refresh_continues_when_one_source_fails() -> None:
    institutional_repo = FakeInstitutionalRepo()

    def failing_twse(_current_date: date):
        raise HttpFetchError("twse down")

    service = _service(
        institutional_repo=institutional_repo,
        twse_institutional=failing_twse,
        tpex_institutional=lambda _d: _tpex_institutional_payload(),
        twse_margin=lambda _d: _no_data_payload(),
        twse_borrowed=lambda _d: _no_data_payload(),
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )

    summary = service.refresh(days=1, trigger="test", include_margin_short=False)

    assert any(
        item.market == "TWSE" and item.dataset == "institutional" and item.error
        for item in summary.results
    )
    assert any(
        item.market == "TPEx"
        and item.dataset == "institutional"
        and item.rows_fetched > 0
        for item in summary.results
    )
    assert summary.error_count >= 1
    assert any(market == "TPEx" for market, _source, _count in institutional_repo.calls)


def test_refresh_respects_requested_day_window() -> None:
    seen: list[date] = []

    def track(current_date: date):
        seen.append(current_date)
        return _no_data_payload()

    service = _service(
        twse_institutional=track,
        tpex_institutional=track,
        twse_margin=lambda _d: _no_data_payload(),
        twse_borrowed=lambda _d: _no_data_payload(),
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )
    summary = service.refresh(days=3, trigger="window-test")

    assert summary.days == 3
    assert len(sorted(set(seen))) == 3


def test_refresh_upserts_margin_short_when_payloads_present() -> None:
    margin_repo = FakeMarginRepo()
    service = _service(
        margin_repo=margin_repo,
        twse_institutional=lambda _d: _no_data_payload(),
        tpex_institutional=lambda _d: _no_data_payload(),
        twse_margin=lambda _d: _twse_margin_payload(),
        twse_borrowed=lambda _d: _twse_borrowed_payload(),
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )

    summary = service.refresh(days=1, trigger="margin-test")
    twse_margin_results = [
        item
        for item in summary.results
        if item.dataset == "margin_short" and item.market == "TWSE"
    ]
    assert twse_margin_results
    assert twse_margin_results[0].rows_fetched >= 1
    assert margin_repo.calls
    assert margin_repo.calls[0][0] == "TWSE"


def test_refresh_upserts_tpex_margin_short_when_payloads_present() -> None:
    margin_repo = FakeMarginRepo()
    tpex_margin = {
        "date": "20260709",
        "tables": [
            {
                "date": "115/07/09",
                "fields": [
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
                ],
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
    tpex_borrowed = {
        "date": "20260709",
        "tables": [
            {
                "date": "115/07/09",
                "fields": ["placeholder"] * 15,
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
    service = _service(
        margin_repo=margin_repo,
        twse_institutional=lambda _d: _no_data_payload(),
        tpex_institutional=lambda _d: _no_data_payload(),
        twse_margin=lambda _d: _no_data_payload(),
        twse_borrowed=lambda _d: _no_data_payload(),
        tpex_margin=lambda _d: tpex_margin,
        tpex_borrowed=lambda _d: tpex_borrowed,
    )

    summary = service.refresh(days=1, trigger="tpex-margin-test")
    tpex_results = [
        item
        for item in summary.results
        if item.dataset == "margin_short" and item.market == "TPEx"
    ]
    assert tpex_results
    assert tpex_results[0].rows_fetched >= 1
    assert any(market == "TPEx" for market, _source, _count in margin_repo.calls)


def test_refresh_continues_when_optional_close_fetch_fails() -> None:
    margin_repo = FakeMarginRepo()

    def failing_close(_current_date: date):
        raise HttpFetchError("close feed down")

    service = _service(
        margin_repo=margin_repo,
        twse_institutional=lambda _d: _no_data_payload(),
        tpex_institutional=lambda _d: _no_data_payload(),
        twse_margin=lambda _d: _twse_margin_payload(),
        twse_borrowed=lambda _d: _twse_borrowed_payload(),
        twse_close=failing_close,
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )

    summary = service.refresh(days=1, trigger="close-optional")
    twse_ok = [
        item
        for item in summary.results
        if item.dataset == "margin_short"
        and item.market == "TWSE"
        and item.rows_fetched > 0
        and item.error is None
    ]
    assert twse_ok
    assert margin_repo.calls


def test_refresh_continues_when_upsert_raises() -> None:
    class FailingInstitutionalRepo(FakeInstitutionalRepo):
        def upsert_records(self, *, market, source, records):
            raise RuntimeError("upsert boom")

    institutional_repo = FailingInstitutionalRepo()
    service = _service(
        institutional_repo=institutional_repo,
        twse_institutional=lambda _d: _twse_institutional_payload(),
        tpex_institutional=lambda _d: _tpex_institutional_payload(),
        twse_margin=lambda _d: _no_data_payload(),
        twse_borrowed=lambda _d: _no_data_payload(),
        tpex_margin=lambda _d: _no_data_payload(),
        tpex_borrowed=lambda _d: _no_data_payload(),
    )

    summary = service.refresh(days=1, trigger="upsert-fail", include_margin_short=False)
    assert summary.error_count >= 1
    assert all(
        item.error is not None
        for item in summary.results
        if item.dataset == "institutional"
    )


def test_refresh_reports_database_unavailable() -> None:
    institutional_repo = FakeInstitutionalRepo()
    institutional_repo.available = False
    service = _service(
        institutional_repo=institutional_repo,
        twse_institutional=lambda _d: _twse_institutional_payload(),
        tpex_institutional=lambda _d: _tpex_institutional_payload(),
    )
    summary = service.refresh(days=1, trigger="db-down", include_margin_short=False)
    assert all(item.error == "database_unavailable" for item in summary.results)
    assert institutional_repo.calls == []


def test_sampler_skips_when_database_unavailable() -> None:
    institutional_repo = FakeInstitutionalRepo()
    institutional_repo.available = False
    service = TaiwanMarketDataRefreshService(
        institutional_repository=institutional_repo,
        margin_short_repository=FakeMarginRepo(),
    )
    service.start_background_sampler(60, refresh_hour=18, recent_days=7)
    assert service._thread is None


def test_sampler_skips_before_refresh_hour(monkeypatch) -> None:
    calls: list[str] = []
    early = datetime(2026, 7, 10, 2, 0, tzinfo=UTC)  # 10:00 Asia/Taipei
    service = TaiwanMarketDataRefreshService(
        institutional_repository=FakeInstitutionalRepo(),
        margin_short_repository=FakeMarginRepo(),
        now_provider=lambda: early,
    )
    monkeypatch.setattr(
        service,
        "refresh_recent",
        lambda **kwargs: calls.append(kwargs["trigger"]),
    )

    wait_state = {"entered": False}

    def fake_wait(timeout=None):
        if timeout == 0:
            if wait_state["entered"]:
                return True
            wait_state["entered"] = True
            return False
        return True

    monkeypatch.setattr(service._stop_event, "wait", fake_wait)
    service._run_sampler(poll_seconds=1, refresh_hour=18, recent_days=7)

    assert early.astimezone(ZoneInfo("Asia/Taipei")).hour < 18
    assert calls == []


def test_sampler_runs_after_refresh_hour(monkeypatch) -> None:
    calls: list[str] = []
    late = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)  # 20:00 Asia/Taipei
    service = TaiwanMarketDataRefreshService(
        institutional_repository=FakeInstitutionalRepo(),
        margin_short_repository=FakeMarginRepo(),
        now_provider=lambda: late,
    )
    monkeypatch.setattr(
        service,
        "refresh_recent",
        lambda **kwargs: calls.append(kwargs["trigger"]),
    )

    wait_state = {"entered": False}

    def fake_wait(timeout=None):
        if timeout == 0:
            if wait_state["entered"]:
                return True
            wait_state["entered"] = True
            return False
        return True

    monkeypatch.setattr(service._stop_event, "wait", fake_wait)
    service._run_sampler(poll_seconds=1, refresh_hour=18, recent_days=7)

    assert calls == ["scheduler"]
    assert service._last_scheduler_local_date == date(2026, 7, 10)


def test_sampler_skips_second_run_same_local_day(monkeypatch) -> None:
    calls: list[str] = []
    late = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    service = TaiwanMarketDataRefreshService(
        institutional_repository=FakeInstitutionalRepo(),
        margin_short_repository=FakeMarginRepo(),
        now_provider=lambda: late,
    )
    service._last_scheduler_local_date = date(2026, 7, 10)
    monkeypatch.setattr(
        service,
        "refresh_recent",
        lambda **kwargs: calls.append(kwargs["trigger"]),
    )

    wait_state = {"entered": False}

    def fake_wait(timeout=None):
        if timeout == 0:
            if wait_state["entered"]:
                return True
            wait_state["entered"] = True
            return False
        return True

    monkeypatch.setattr(service._stop_event, "wait", fake_wait)
    service._run_sampler(poll_seconds=1, refresh_hour=18, recent_days=7)

    assert calls == []
