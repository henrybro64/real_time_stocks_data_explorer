"""#348: first-pass ownership kwargs + TypeError classification."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.tw_ownership_concentration_service import (
    tw_ownership_concentration_service,
)
from app.services.tw_research.first_pass import (
    _default_ownership,
    classify_field_reason,
    collect_first_pass_batch,
    provider_exception_reason,
)
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class _OwnPoint:
    date: str
    holders_400_lots_or_more_percent: float | None


@dataclass(frozen=True)
class _OwnResult:
    points: tuple
    as_of: str | None
    source: str
    stale: bool = False


@dataclass(frozen=True)
class _InstPoint:
    date: str
    foreign_net_lots: float = 1.0
    trust_net_lots: float = 0.0
    dealer_net_lots: float = 0.0
    total_net_lots: float = 1.0


@dataclass(frozen=True)
class _InstResult:
    history: tuple = ()
    latest_date: str | None = None
    source: str = "fixture"


@dataclass(frozen=True)
class _MarginResult:
    history: tuple = ()
    summary: object | None = None
    latest_date: str | None = None
    source: str = "fixture"
    empty_reason: str | None = None


def _candles(days: int = 40) -> list[CandleRecord]:
    start = datetime(2024, 1, 2, 13, 30, tzinfo=TZ)
    out: list[CandleRecord] = []
    price = 100.0
    for index in range(days):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price += 0.1
        out.append(
            CandleRecord(
                time=day.isoformat(),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000,
            )
        )
    return out


def test_real_ownership_service_signature_requires_range_name() -> None:
    """Real service rejects the production bug kwarg ``range=`` (#348)."""
    sig = inspect.signature(tw_ownership_concentration_service.get_ownership_concentration)
    params = sig.parameters
    assert "range_name" in params
    assert "range" not in params
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        tw_ownership_concentration_service.get_ownership_concentration(
            "TW:2330", range="3M"  # type: ignore[call-arg]
        )


def test_default_ownership_uses_range_name_kwarg(monkeypatch) -> None:
    """Live path must call range_name= — range= raises TypeError (#348)."""
    captured: dict[str, object] = {}

    def fake_get(stock_id: str, *, range_name: str = "1Y"):
        captured["stock_id"] = stock_id
        captured["range_name"] = range_name
        return _OwnResult(points=(), as_of=None, source="fixture")

    monkeypatch.setattr(
        "app.services.tw_research.first_pass.tw_ownership_concentration_service.get_ownership_concentration",
        fake_get,
    )
    result = _default_ownership("TW:2330")
    assert captured["stock_id"] == "TW:2330"
    assert captured["range_name"] == "3M"
    assert result.source == "fixture"


def test_typeerror_classified_as_programming_error_not_transient() -> None:
    reason = provider_exception_reason(TypeError("unexpected keyword argument 'range'"))
    assert reason.startswith("programming_error:")
    assert classify_field_reason(reason) == "permanent"


def test_typeerror_with_timeout_text_still_permanent() -> None:
    """Classifier must not let message heuristics override TypeError (#348 P1)."""
    reason = provider_exception_reason(TypeError("unexpected keyword argument 'timeout'"))
    assert reason == "programming_error:TypeError"
    assert classify_field_reason(reason) == "permanent"
    reason2 = provider_exception_reason(TypeError("connection object not iterable"))
    assert reason2 == "programming_error:TypeError"
    assert classify_field_reason(reason2) == "permanent"


def test_mixed_batch_keeps_good_peer_when_ownership_raises(monkeypatch) -> None:
    """One ownership TypeError must not invent failures for peer fields/symbols."""

    def stock_loader(stock_id: str):
        return stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )

    def ownership_loader(stock_id: str):
        if stock_id.endswith("2317"):
            raise TypeError("unexpected keyword argument 'range'")
        return _OwnResult(
            points=(_OwnPoint("2024-06-21", 40.0),),
            as_of="2024-06-21",
            source="fixture",
        )

    inst = _InstResult(
        history=(_InstPoint("2024-06-24"),),
        latest_date="2024-06-24",
        source="fixture",
    )

    result = collect_first_pass_batch(
        ["TW:2330", "TW:2317"],
        universe_version="uv",
        stock_loader=stock_loader,
        candles_loader=lambda _stock: _candles(),
        institutional_loader=lambda *_a, **_k: inst,
        margin_loader=lambda *_a, **_k: _MarginResult(
            latest_date="2024-06-24", source="fixture"
        ),
        ownership_loader=ownership_loader,
        key_stats_loader=lambda _sid: None,
    )
    by_id = {row.stock_id: row for row in result.records}
    good = by_id["TW:2330"]
    bad = by_id["TW:2317"]
    assert good.fields["tdcc_holders_ge_400_lots"].status in {"ok", "partial"}
    assert bad.fields["tdcc_holders_ge_400_lots"].status == "unavailable"
    assert "programming_error:TypeError" in str(
        bad.fields["tdcc_holders_ge_400_lots"].reason
    )
    # Peer fields still collected on the failing symbol — not invented zeros.
    assert bad.fields["institutional_5d"].status in {"ok", "partial", "unavailable"}
    assert bad.fields["margin"].status in {"ok", "partial", "unavailable"}
    assert bad.fields["institutional_5d"].status != "ok" or (
        bad.fields["institutional_5d"].value is not None
    )
    # Ownership fault must not zero-out institutional/margin provenance keys.
    assert "institutional_5d" in bad.fields
    assert "margin" in bad.fields
    assert classify_field_reason(
        str(bad.fields["tdcc_holders_ge_400_lots"].reason)
    ) == "permanent"


def test_malformed_tdcc_null_percent_stays_unavailable() -> None:
    """Null TDCC percents must not raise or invent a verified concentration."""

    def stock_loader(stock_id: str):
        return stock_record(
            stock_id,
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )

    def ownership_loader(_stock_id: str):
        return _OwnResult(
            points=(
                _OwnPoint("2024-06-14", None),
                _OwnPoint("2024-06-21", None),
            ),
            as_of="2024-06-21",
            source="fixture",
        )

    result = collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=stock_loader,
        candles_loader=lambda _stock: _candles(),
        institutional_loader=lambda *_a, **_k: _InstResult(),
        margin_loader=lambda *_a, **_k: _MarginResult(),
        ownership_loader=ownership_loader,
        key_stats_loader=lambda _sid: None,
    )
    row = result.records[0]
    tdcc = row.fields["tdcc_holders_ge_400_lots"]
    assert tdcc.status == "unavailable"
    assert tdcc.reason == "holders_400_lots_percent_missing"
    assert isinstance(tdcc.value, dict)
    assert tdcc.value.get("percent") is None
    assert tdcc.value.get("weeklyChangePercentPoints") is None
