from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.stock_analysis_institutional import build_institutional_analysis
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorRecord,
    TaiwanInstitutionalInvestorsResult,
)
from tests.support import stock_record


def _tw_stock(*, symbol: str = "2330") -> object:
    return stock_record(
        f"TW:{symbol}",
        market="TW",
        symbol=symbol,
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw="台積電",
    )


def _us_stock() -> object:
    return stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple",
    )


def _record(
    *,
    date: str,
    symbol: str = "2330",
    foreign: int,
    trust: int,
    dealer: int,
) -> TaiwanInstitutionalInvestorRecord:
    return TaiwanInstitutionalInvestorRecord(
        date=date,
        symbol=symbol,
        name="台積電",
        foreign_net_shares=foreign,
        investment_trust_net_shares=trust,
        dealer_self_net_shares=dealer,
        dealer_hedge_net_shares=0,
        dealer_net_shares=dealer,
        total_net_shares=foreign + trust + dealer,
    )


@dataclass
class FakeInstitutionalService:
    result: TaiwanInstitutionalInvestorsResult | None = None
    error: Exception | None = None
    calls: list[tuple[str, int]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def get_institutional_investors(self, stock_id: str, *, days: int = 90):
        assert self.calls is not None
        self.calls.append((stock_id, days))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_us_stock_returns_none() -> None:
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="US:AAPL",
            symbol="AAPL",
            market="TWSE",
            latest_date=None,
            summary=None,
            history=(),
            source="unused",
        )
    )

    assert build_institutional_analysis("US:AAPL", _us_stock(), institutional_service=service) is None
    assert service.calls == []


def test_soft_fail_when_service_raises() -> None:
    service = FakeInstitutionalService(error=RuntimeError("upstream down"))

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
    )

    # Classified reason only: raw upstream error text must not reach the model.
    assert payload == {
        "available": False,
        "reason": "provider_failed",
        "reasonDetail": "RuntimeError",
        "market": "TW",
    }


def test_soft_fail_when_history_empty() -> None:
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:2330",
            symbol="2330",
            market="TWSE",
            latest_date=None,
            summary=None,
            history=(),
            source="TWSE T86 daily institutional data",
        )
    )

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
    )

    assert payload is not None
    assert payload["available"] is False
    assert payload["market"] == "TW"
    assert payload["source"] == "TWSE T86 daily institutional data"


def test_tw_payload_converts_shares_to_lots_and_sums_windows() -> None:
    # Shares / 1000 = lots for equities.
    history = (
        _record(date="2026-07-01", foreign=1_000, trust=2_000, dealer=-500),
        _record(date="2026-07-02", foreign=2_000, trust=1_000, dealer=-500),
        _record(date="2026-07-03", foreign=3_000, trust=0, dealer=1_000),
        _record(date="2026-07-04", foreign=4_000, trust=-1_000, dealer=2_000),
        _record(date="2026-07-07", foreign=5_000, trust=1_000, dealer=1_000),
        # Extra older-style padding beyond 5d to exercise 20d sum.
        *[_record(date=f"2026-06-{day:02d}", foreign=1_000, trust=0, dealer=0) for day in range(1, 16)],
    )
    # Ensure chronological order oldest → newest as returned by the service.
    ordered = tuple(sorted(history, key=lambda item: item.date))
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:2330",
            symbol="2330",
            market="TWSE",
            latest_date=ordered[-1].date,
            summary=ordered[-1],
            history=ordered,
            source="TWSE T86 daily institutional data",
        )
    )

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
        days=90,
    )

    assert payload is not None
    assert payload["available"] is True
    assert payload["periodDays"] == 90
    assert payload["dataAsOf"] == ordered[-1].date
    assert payload["source"] == "TWSE T86 daily institutional data"
    assert service.calls == [("TW:2330", 90)]

    # Last 5 days foreign lots: sum of last 5 foreign share totals / 1000.
    last5 = ordered[-5:]
    expected_foreign_5d = round(sum(item.foreign_net_shares for item in last5) / 1000.0, 3)
    assert payload["foreignNetLots5d"] == pytest.approx(expected_foreign_5d)
    assert payload["totalNetLots5d"] == pytest.approx(
        round(sum(item.total_net_shares for item in last5) / 1000.0, 3)
    )
    assert payload["foreignNetLots20d"] == pytest.approx(
        round(sum(item.foreign_net_shares for item in ordered[-20:]) / 1000.0, 3)
    )


def test_agreement_broad_buy_and_consecutive_net_buy_days() -> None:
    history = tuple(
        _record(date=f"2026-07-{day:02d}", foreign=1_000, trust=500, dealer=200)
        for day in range(1, 6)
    )
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:2330",
            symbol="2330",
            market="TWSE",
            latest_date="2026-07-05",
            summary=history[-1],
            history=history,
            source="TWSE T86",
        )
    )

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
    )

    assert payload is not None
    assert payload["agreement"] == "broad_buy"
    assert payload["consecutiveNetBuyDays"] == 5
    assert payload["consecutiveNetSellDays"] == 0


def test_agreement_broad_sell_and_consecutive_net_sell_days() -> None:
    history = (
        _record(date="2026-07-01", foreign=5_000, trust=1_000, dealer=1_000),
        _record(date="2026-07-02", foreign=-2_000, trust=-1_500, dealer=-800),
        _record(date="2026-07-03", foreign=-3_000, trust=-1_500, dealer=-900),
        _record(date="2026-07-04", foreign=-4_000, trust=-1_500, dealer=-1_000),
    )
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:2330",
            symbol="2330",
            market="TWSE",
            latest_date="2026-07-04",
            summary=history[-1],
            history=history,
            source="TWSE T86",
        )
    )

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
    )

    assert payload is not None
    assert payload["agreement"] == "broad_sell"
    assert payload["consecutiveNetBuyDays"] == 0
    assert payload["consecutiveNetSellDays"] == 3


def test_agreement_mixed_when_groups_disagree() -> None:
    history = (
        _record(date="2026-07-01", foreign=1_000, trust=-500, dealer=200),
        _record(date="2026-07-02", foreign=2_000, trust=-1_000, dealer=100),
    )
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:2330",
            symbol="2330",
            market="TWSE",
            latest_date="2026-07-02",
            summary=history[-1],
            history=history,
            source="TWSE T86",
        )
    )

    payload = build_institutional_analysis(
        "TW:2330",
        _tw_stock(),
        institutional_service=service,
    )

    assert payload is not None
    assert payload["agreement"] == "mixed"


def test_fx_etf_uses_100_unit_lot_size() -> None:
    history = (_record(date="2026-07-01", symbol="00677K", foreign=1_000, trust=0, dealer=0),)
    service = FakeInstitutionalService(
        result=TaiwanInstitutionalInvestorsResult(
            stock_id="TW:00677K",
            symbol="00677K",
            market="TWSE",
            latest_date="2026-07-01",
            summary=history[0],
            history=history,
            source="TWSE T86",
        )
    )

    payload = build_institutional_analysis(
        "TW:00677K",
        _tw_stock(symbol="00677K"),
        institutional_service=service,
    )

    assert payload is not None
    # 1000 shares / 100 shares-per-lot = 10 張
    assert payload["foreignNetLots5d"] == pytest.approx(10.0)
    assert payload["totalNetLots5d"] == pytest.approx(10.0)
