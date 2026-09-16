"""Unit tests for backend-derived key price levels (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.quote import CandleRecord
from app.services.stock_analysis_key_price_levels import (
    _collect_candidates,
    build_key_price_levels,
)


def _series_from_ohlc(
    rows: list[tuple[float, float, float, float]],
    *,
    start: datetime | None = None,
) -> list[CandleRecord]:
    """Build oldest→newest candles on weekdays from OHLC tuples."""
    anchor = start or datetime(2026, 8, 3, 16, 0)
    dates: list[datetime] = []
    day_offset = 0
    while len(dates) < len(rows):
        day = anchor - timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day)
    dates.reverse()
    return [
        CandleRecord(
            time=timestamp.isoformat(),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1_000,
        )
        for timestamp, (open_, high, low, close) in zip(dates, rows, strict=True)
    ]


def _flat_then_range(
    *,
    sessions: int,
    base: float,
    final_rows: list[tuple[float, float, float, float]],
) -> list[CandleRecord]:
    """Enough flat history for coverage, then distinctive final OHLC rows."""
    prefix_count = max(0, sessions - len(final_rows))
    prefix = [(base, base, base, base)] * prefix_count
    return _series_from_ohlc(prefix + final_rows)


def test_support_below_and_resistance_above_reference() -> None:
    candles = _flat_then_range(
        sessions=80,
        base=100.0,
        final_rows=[
            (100.0, 110.0, 90.0, 100.0),
            (100.0, 112.0, 88.0, 100.0),
            (100.0, 108.0, 92.0, 100.0),
        ],
    )
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )

    assert payload["availability"] == "ok"
    assert payload["referencePrice"] == 100.0
    assert payload["supports"]
    assert payload["resistances"]
    for zone in payload["supports"]:
        assert zone["upper"] < 100.0
    for zone in payload["resistances"]:
        assert zone["lower"] > 100.0


def test_sma_candidates_present_with_enough_history() -> None:
    # Gently rising series so SMAs sit below the latest close (supports).
    rows = [
        (100.0 + i * 0.2, 100.0 + i * 0.2, 100.0 + i * 0.2, 100.0 + i * 0.2)
        for i in range(220)
    ]
    candles = _series_from_ohlc(rows)
    labels = {label for _, label in _collect_candidates(candles, reference=100.0)}
    assert {"sma10", "sma20", "sma50", "sma60", "sma200"}.issubset(labels)

    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
    )
    evidence = {
        label
        for zone in payload["supports"] + payload["resistances"]
        for label in zone["evidence"]
    }
    # Max-3 ranking may drop mid-length MAs; at least one SMA should survive.
    assert evidence & {"sma10", "sma20", "sma50", "sma60", "sma200"}


def test_20d_and_60d_extremes() -> None:
    # Flat base, then a clear 60d high early in the window and a 20d high later.
    base = [(50.0, 50.0, 50.0, 50.0)] * 40
    spike_60 = [(50.0, 80.0, 50.0, 50.0)]  # becomes high_60d
    mid = [(50.0, 55.0, 45.0, 50.0)] * 25
    spike_20 = [(50.0, 70.0, 50.0, 50.0)]  # becomes high_20d
    tail = [(50.0, 52.0, 48.0, 50.0)] * 14
    candles = _series_from_ohlc(base + spike_60 + mid + spike_20 + tail)

    labels = {label for _, label in _collect_candidates(candles, reference=100.0)}
    assert "high_20d" in labels
    assert "high_60d" in labels
    # Identical window lows keep the longer label only.
    assert "low_60d" in labels or "low_20d" in labels
    assert not ({"low_20d", "low_60d"} <= labels)

    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=50.0,
    )
    evidence = {
        label
        for zone in payload["supports"] + payload["resistances"]
        for label in zone["evidence"]
    }
    assert "high_20d" in evidence or "high_60d" in evidence


def test_swing_detection() -> None:
    # Neighbors N=2: clear swing high at 120 and swing low at 80 around flat 100.
    pattern = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 102.0, 98.0, 100.0),
        (100.0, 120.0, 100.0, 100.0),  # swing high
        (100.0, 102.0, 98.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 102.0, 98.0, 100.0),
        (100.0, 100.0, 80.0, 100.0),  # swing low
        (100.0, 102.0, 98.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
    ]
    candles = _flat_then_range(sessions=70, base=100.0, final_rows=pattern)
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )
    evidence = {
        label
        for zone in payload["supports"] + payload["resistances"]
        for label in zone["evidence"]
    }
    assert "swing_high" in evidence
    assert "swing_low" in evidence


def test_nearby_candidates_cluster_into_one_zone() -> None:
    # Two resistance highs within ~0.3% should merge (tolerance 0.4% of mid).
    candles = _flat_then_range(
        sessions=70,
        base=100.0,
        final_rows=[
            (100.0, 110.0, 100.0, 100.0),
            (100.0, 110.3, 100.0, 100.0),
            (100.0, 105.0, 95.0, 100.0),
        ],
    )
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )
    matching = [
        zone
        for zone in payload["resistances"]
        if 109.0 <= (zone["lower"] + zone["upper"]) / 2 <= 111.0
    ]
    assert len(matching) == 1
    assert matching[0]["lower"] <= matching[0]["upper"]


def test_distant_candidates_stay_separate() -> None:
    from app.services.stock_analysis_key_price_levels import _cluster_candidates

    clusters = _cluster_candidates(
        [(110.0, "swing_high"), (130.0, "high_20d")],
        reference=100.0,
    )
    assert len(clusters) == 2
    mids = [(min(prices) + max(prices)) / 2 for prices, _ in clusters]
    assert any(abs(mid - 110.0) < 0.01 for mid in mids)
    assert any(abs(mid - 130.0) < 0.01 for mid in mids)


def test_single_price_level_has_equal_bounds() -> None:
    candles = _flat_then_range(
        sessions=70,
        base=100.0,
        final_rows=[
            (100.0, 100.0, 100.0, 100.0),
            (100.0, 115.0, 100.0, 100.0),
            (100.0, 100.0, 100.0, 100.0),
        ],
    )
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )
    singles = [
        zone
        for zone in payload["resistances"]
        if zone["lower"] == zone["upper"] and zone["lower"] == 115.0
    ]
    assert singles


def test_relative_tolerance_scales_with_price() -> None:
    # At ~10, 0.4% ≈ 0.04; at ~1000, 0.4% ≈ 4. Same relative gap should cluster.
    low_price = _flat_then_range(
        sessions=70,
        base=10.0,
        final_rows=[
            (10.0, 11.00, 10.0, 10.0),
            (10.0, 11.03, 10.0, 10.0),
            (10.0, 10.5, 9.5, 10.0),
        ],
    )
    high_price = _flat_then_range(
        sessions=70,
        base=1000.0,
        final_rows=[
            (1000.0, 1100.0, 1000.0, 1000.0),
            (1000.0, 1103.0, 1000.0, 1000.0),
            (1000.0, 1050.0, 950.0, 1000.0),
        ],
    )
    low_payload = build_key_price_levels(
        low_price, currency="USD", data_as_of="2026-08-03", reference_price=10.0
    )
    high_payload = build_key_price_levels(
        high_price, currency="USD", data_as_of="2026-08-03", reference_price=1000.0
    )

    def _cluster_count_near(payload: dict, lo: float, hi: float) -> int:
        return sum(
            1
            for zone in payload["resistances"]
            if lo <= (zone["lower"] + zone["upper"]) / 2 <= hi
        )

    assert _cluster_count_near(low_payload, 10.8, 11.2) == 1
    assert _cluster_count_near(high_payload, 1080.0, 1120.0) == 1


def test_max_three_zones_per_side() -> None:
    # Many well-separated resistance highs above reference.
    highs = [120.0, 130.0, 140.0, 150.0, 160.0, 170.0]
    final_rows = [(100.0, high, 100.0, 100.0) for high in highs]
    final_rows.append((100.0, 101.0, 99.0, 100.0))
    candles = _flat_then_range(sessions=80, base=100.0, final_rows=final_rows)
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )
    assert len(payload["resistances"]) <= 3
    assert len(payload["supports"]) <= 3
    ranks = [zone["rank"] for zone in payload["resistances"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_insufficient_history() -> None:
    short = _series_from_ohlc([(100.0, 101.0, 99.0, 100.0)] * 30)
    payload = build_key_price_levels(
        short,
        currency="TWD",
        data_as_of="2026-08-03",
    )
    assert payload["availability"] == "insufficient"
    assert payload["supports"] == []
    assert payload["resistances"] == []
    assert payload["currency"] == "TWD"

    tiny = _series_from_ohlc([(100.0, 101.0, 99.0, 100.0)] * 10)
    tiny_payload = build_key_price_levels(
        tiny,
        currency="USD",
        data_as_of="2026-08-03",
    )
    assert tiny_payload["availability"] == "unavailable"


def test_repeated_touch_and_consolidation_boundary_evidence() -> None:
    # Flat 10-day consolidation around 100, then a close at 100.
    final_rows = [(100.0, 101.0, 99.0, 100.0)] * 12
    candles = _flat_then_range(sessions=80, base=100.0, final_rows=final_rows)
    labels = {label for _, label in _collect_candidates(candles, reference=100.0)}
    assert "repeated_touch" in labels
    assert "consolidation_boundary" in labels


def test_role_reversal_evidence_when_level_is_crossed() -> None:
    # Prior close above 105, latest close below 105 → SMA/level flipped sides.
    candles = _flat_then_range(
        sessions=80,
        base=110.0,
        final_rows=[
            (110.0, 112.0, 108.0, 110.0),
            (110.0, 111.0, 100.0, 101.0),
        ],
    )
    labels = {label for price, label in _collect_candidates(candles, reference=101.0) if price == 110.0 or label == "role_reversal"}
    assert "role_reversal" in {label for _, label in _collect_candidates(candles, reference=101.0)}


def test_strength_scoring_maps_evidence_counts() -> None:
    from app.services.stock_analysis_key_price_levels import _score_strength

    assert _score_strength(["sma10"]) == "weak"
    assert _score_strength(["sma50", "swing_low"]) == "moderate"
    assert _score_strength(["sma200", "repeated_touch", "swing_low"]) == "strong"


def test_duplicate_window_extremes_do_not_inflate_strength() -> None:
    from app.services.stock_analysis_key_price_levels import _score_strength

    assert _score_strength(["high_20d", "high_60d"]) == "weak"
    assert _score_strength(["latest_high", "high_20d"]) == "weak"


def test_missing_and_nan_prices_discarded() -> None:
    rows = [(100.0, 100.0, 100.0, 100.0)] * 65
    candles = _series_from_ohlc(rows)
    # Inject invalid highs/lows on a middle bar; builder should not crash.
    bad = candles[30]
    candles[30] = CandleRecord(
        time=bad.time,
        open=float("nan"),
        high=float("nan"),
        low=-5.0,
        close=100.0,
        volume=1_000,
    )
    candles[-1] = CandleRecord(
        time=candles[-1].time,
        open=100.0,
        high=float("inf"),
        low=0.0,
        close=100.0,
        volume=1_000,
    )
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
        reference_price=100.0,
    )
    assert payload["availability"] == "ok"
    for zone in payload["supports"] + payload["resistances"]:
        assert zone["lower"] > 0
        assert zone["upper"] > 0


def test_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network should not be used")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    candles = _flat_then_range(
        sessions=70,
        base=100.0,
        final_rows=[(100.0, 110.0, 90.0, 100.0)],
    )
    payload = build_key_price_levels(
        candles,
        currency="USD",
        data_as_of="2026-08-03",
    )
    assert payload["availability"] == "ok"
