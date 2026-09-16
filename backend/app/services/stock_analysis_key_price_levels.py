"""Backend-derived support/resistance zones for AI stock analysis.

Levels are computed from completed daily candles only. The AI may explain
``keyPriceLevels`` but must not invent, move, or strengthen them.
"""

from __future__ import annotations

import math
from typing import Literal

from app.models.quote import CandleRecord
from app.services.moving_average import compute_sma_series

Availability = Literal["ok", "insufficient", "unavailable"]
Strength = Literal["weak", "moderate", "strong"]
Side = Literal["support", "resistance"]

_MIN_SESSIONS = 20
_GOOD_COVERAGE_SESSIONS = 60
_VISIBLE_EVIDENCE_SESSIONS = 260  # Match chart 1Y visible window; exclude SMA warm-up.
_NEAR_REFERENCE_PCT = 0.003  # 0.3% — skip (decision zone) for v1
_CLUSTER_PCT = 0.004  # 0.4% of mid
_MAX_ZONE_WIDTH_PCT = 0.03  # ~3% of reference
_MIN_TICK = 0.01
_SWING_NEIGHBORS = 2
_REPEATED_TOUCH_MIN = 3
_MAX_ZONES_PER_SIDE = 3
_CONSOLIDATION_LOOKBACK = 10
_CONSOLIDATION_MAX_WIDTH_PCT = 0.03
_SMA_WINDOWS: tuple[tuple[int, str], ...] = (
    (10, "sma10"),
    (20, "sma20"),
    (50, "sma50"),
    (60, "sma60"),
    (200, "sma200"),
)
_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}


def build_key_price_levels(
    candles: list[CandleRecord],
    *,
    currency: str,
    data_as_of: str,
    reference_price: float | None = None,
) -> dict:
    """Build support/resistance zones from daily OHLC candles.

    Strength is a simple score: unique evidence labels, with extras for longer
    MAs (sma50/sma60/sma200) and ``repeated_touch``. Identical latest and
    window extremes from the same candle keep unique labels but do not inflate
    the evidence count twice.
    """
    if not candles:
        return _empty_payload(
            reference_price=None,
            currency=currency,
            data_as_of=data_as_of,
            availability="unavailable",
        )

    if len(candles) < _MIN_SESSIONS:
        ref = _resolve_reference(candles, reference_price)
        return _empty_payload(
            reference_price=_round_price(ref) if ref is not None else None,
            currency=currency,
            data_as_of=data_as_of,
            availability="unavailable",
        )

    if len(candles) < _GOOD_COVERAGE_SESSIONS:
        ref = _resolve_reference(candles, reference_price)
        return _empty_payload(
            reference_price=_round_price(ref) if ref is not None else None,
            currency=currency,
            data_as_of=data_as_of,
            availability="insufficient",
        )

    reference = _resolve_reference(candles, reference_price)
    if reference is None or not _is_valid_price(reference):
        return _empty_payload(
            reference_price=None,
            currency=currency,
            data_as_of=data_as_of,
            availability="unavailable",
        )

    evidence_candles = (
        candles[-_VISIBLE_EVIDENCE_SESSIONS:]
        if len(candles) > _VISIBLE_EVIDENCE_SESSIONS
        else candles
    )
    # SMAs may use warm-up history; non-SMA evidence stays in the visible 1Y window.
    candidates = _collect_candidates(
        candles,
        evidence_candles=evidence_candles,
        reference=reference,
    )
    supports = _build_side_zones(candidates, reference=reference, side="support")
    resistances = _build_side_zones(candidates, reference=reference, side="resistance")

    return {
        "referencePrice": _round_price(reference),
        "currency": currency,
        "dataAsOf": data_as_of,
        "supports": supports,
        "resistances": resistances,
        "availability": "ok",
    }


def unavailable_key_price_levels(
    *,
    currency: str = "USD",
    data_as_of: str | None = None,
    reference_price: float | None = None,
) -> dict:
    """Stub used when key-level computation soft-fails or technical data is null."""
    return _empty_payload(
        reference_price=reference_price,
        currency=currency,
        data_as_of=data_as_of,
        availability="unavailable",
    )


def _empty_payload(
    *,
    reference_price: float | None,
    currency: str,
    data_as_of: str | None,
    availability: Availability,
) -> dict:
    return {
        "referencePrice": reference_price,
        "currency": currency,
        "dataAsOf": data_as_of,
        "supports": [],
        "resistances": [],
        "availability": availability,
    }


def _resolve_reference(
    candles: list[CandleRecord],
    reference_price: float | None,
) -> float | None:
    if reference_price is not None and _is_valid_price(reference_price):
        return float(reference_price)
    close = float(candles[-1].close)
    return close if _is_valid_price(close) else None


def _is_valid_price(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _round_price(value: float) -> float:
    return round(float(value), 2)


def _collect_candidates(
    candles: list[CandleRecord],
    *,
    evidence_candles: list[CandleRecord],
    reference: float,
) -> list[tuple[float, str]]:
    """Return (price, evidence_type) pairs; invalid prices are dropped.

    ``candles`` may include SMA warm-up. ``evidence_candles`` should be the
    visible analysis window (e.g. ~260 1Y sessions) for swings/touches/windows.
    """
    candidates: list[tuple[float, str]] = []
    _append_session_extremes(candidates, evidence_candles)
    _append_sma_candidates(candidates, candles)
    _append_window_extremes(candidates, evidence_candles)
    for price in _swing_extremes(evidence_candles, kind="high"):
        _append_candidate(candidates, price, "swing_high")
    for price in _swing_extremes(evidence_candles, kind="low"):
        _append_candidate(candidates, price, "swing_low")
    for price in _repeated_touch_levels(evidence_candles):
        _append_candidate(candidates, price, "repeated_touch")
    for price in _consolidation_boundaries(evidence_candles):
        _append_candidate(candidates, price, "consolidation_boundary")
    _append_role_reversals(candidates, evidence_candles, reference=reference)
    return candidates


def _append_session_extremes(
    candidates: list[tuple[float, str]],
    candles: list[CandleRecord],
) -> None:
    if not candles:
        return
    latest = candles[-1]
    _append_candidate(candidates, float(latest.high), "latest_high")
    _append_candidate(candidates, float(latest.low), "latest_low")
    if len(candles) < 2:
        return
    prior = candles[-2]
    # Prior session extremes help clustering but have no dedicated evidence
    # label; only keep them when they later merge with labeled candidates.
    _append_candidate(candidates, float(prior.high), "_prior_high")
    _append_candidate(candidates, float(prior.low), "_prior_low")


def _append_sma_candidates(
    candidates: list[tuple[float, str]],
    candles: list[CandleRecord],
) -> None:
    for window, label in _SMA_WINDOWS:
        if len(candles) < window:
            continue
        points = compute_sma_series(candles, window)
        if not points:
            continue
        value = points[-1].value
        if value is not None:
            _append_candidate(candidates, float(value), label)


def _append_window_extremes(
    candidates: list[tuple[float, str]],
    candles: list[CandleRecord],
) -> None:
    high_20d = _window_extreme(candles, 20, kind="high")
    low_20d = _window_extreme(candles, 20, kind="low")
    high_60d = _window_extreme(candles, 60, kind="high")
    low_60d = _window_extreme(candles, 60, kind="low")

    # Prefer window labels over latest_* when they share the same candle extreme
    # so strength is not inflated twice for one bar.
    if high_20d is not None:
        _replace_or_append(candidates, high_20d, "high_20d", drop_labels={"latest_high"})
    if low_20d is not None:
        _replace_or_append(candidates, low_20d, "low_20d", drop_labels={"latest_low"})
    if high_60d is not None:
        _replace_or_append(
            candidates,
            high_60d,
            "high_60d",
            drop_labels={"high_20d", "latest_high"},
        )
    if low_60d is not None:
        _replace_or_append(
            candidates,
            low_60d,
            "low_60d",
            drop_labels={"low_20d", "latest_low"},
        )


def _append_role_reversals(
    candidates: list[tuple[float, str]],
    candles: list[CandleRecord],
    *,
    reference: float,
) -> None:
    if len(candles) < 2:
        return
    prior_close = float(candles[-2].close)
    if not _is_valid_price(prior_close):
        return
    for price, label in tuple(candidates):
        if label.startswith("_"):
            continue
        crossed = (prior_close - price) * (reference - price) < 0
        if crossed:
            _append_candidate(candidates, price, "role_reversal")


def _consolidation_boundaries(candles: list[CandleRecord]) -> list[float]:
    """High/low of a recent tight range, when the lookback stays within ~3% width."""
    if len(candles) < _CONSOLIDATION_LOOKBACK:
        return []
    trailing = candles[-_CONSOLIDATION_LOOKBACK:]
    high = max(float(candle.high) for candle in trailing)
    low = min(float(candle.low) for candle in trailing)
    if not _is_valid_price(high) or not _is_valid_price(low):
        return []
    mid = (high + low) / 2.0
    if mid <= 0:
        return []
    if (high - low) / mid > _CONSOLIDATION_MAX_WIDTH_PCT + 1e-12:
        return []
    return [high, low]

def _append_candidate(
    candidates: list[tuple[float, str]],
    price: float,
    label: str,
) -> None:
    if not _is_valid_price(price):
        return
    candidates.append((float(price), label))


def _replace_or_append(
    candidates: list[tuple[float, str]],
    price: float,
    label: str,
    *,
    drop_labels: set[str],
) -> None:
    """Add ``label`` at ``price`` and drop same-price competing labels."""
    if not _is_valid_price(price):
        return
    remaining = [
        (existing_price, existing_label)
        for existing_price, existing_label in candidates
        if not (
            existing_label in drop_labels
            and math.isclose(existing_price, price, rel_tol=0.0, abs_tol=1e-9)
        )
    ]
    candidates.clear()
    candidates.extend(remaining)
    candidates.append((float(price), label))


def _window_extreme(
    candles: list[CandleRecord],
    window: int,
    *,
    kind: Literal["high", "low"],
) -> float | None:
    if len(candles) < window:
        return None
    trailing = candles[-window:]
    if kind == "high":
        value = max(float(candle.high) for candle in trailing)
    else:
        value = min(float(candle.low) for candle in trailing)
    return value if _is_valid_price(value) else None


def _swing_extremes(
    candles: list[CandleRecord],
    *,
    kind: Literal["high", "low"],
) -> list[float]:
    n = _SWING_NEIGHBORS
    if len(candles) < 2 * n + 1:
        return []
    swings: list[float] = []
    for index in range(n, len(candles) - n):
        pivot = _swing_pivot(candles, index=index, kind=kind, neighbors=n)
        if pivot is not None:
            swings.append(pivot)
    return swings


def _swing_pivot(
    candles: list[CandleRecord],
    *,
    index: int,
    kind: Literal["high", "low"],
    neighbors: int,
) -> float | None:
    candle = candles[index]
    if kind == "high":
        pivot = float(candle.high)
        if not _is_valid_price(pivot):
            return None
        if all(
            pivot > float(candles[index - offset].high)
            and pivot > float(candles[index + offset].high)
            for offset in range(1, neighbors + 1)
        ):
            return pivot
        return None

    pivot = float(candle.low)
    if not _is_valid_price(pivot):
        return None
    if all(
        pivot < float(candles[index - offset].low)
        and pivot < float(candles[index + offset].low)
        for offset in range(1, neighbors + 1)
    ):
        return pivot
    return None


def _repeated_touch_levels(candles: list[CandleRecord]) -> list[float]:
    """Price levels touched as high or low at least three times within tolerance."""
    touches: list[float] = []
    for candle in candles:
        high = float(candle.high)
        low = float(candle.low)
        if _is_valid_price(high):
            touches.append(high)
        if _is_valid_price(low):
            touches.append(low)
    if len(touches) < _REPEATED_TOUCH_MIN:
        return []

    touches.sort()
    levels: list[float] = []
    cluster: list[float] = [touches[0]]
    for price in touches[1:]:
        # Bound width against the cluster anchor so single-linkage cannot chain
        # a trending series into one artificially wide "repeated" level.
        anchor = cluster[0]
        tolerance = _cluster_tolerance(anchor)
        if abs(price - anchor) <= tolerance:
            cluster.append(price)
            continue
        _maybe_add_repeated_level(levels, cluster)
        cluster = [price]
    _maybe_add_repeated_level(levels, cluster)
    return levels


def _maybe_add_repeated_level(levels: list[float], cluster: list[float]) -> None:
    if len(cluster) < _REPEATED_TOUCH_MIN:
        return
    levels.append(sum(cluster) / len(cluster))


def _cluster_tolerance(mid: float) -> float:
    return max(_CLUSTER_PCT * mid, _MIN_TICK)


def _build_side_zones(
    candidates: list[tuple[float, str]],
    *,
    reference: float,
    side: Side,
) -> list[dict]:
    near = reference * _NEAR_REFERENCE_PCT
    side_candidates: list[tuple[float, str]] = []
    for price, label in candidates:
        if side == "support":
            if price < reference - near:
                side_candidates.append((price, label))
        elif price > reference + near:
            side_candidates.append((price, label))

    clusters = _cluster_candidates(side_candidates, reference=reference)
    zones: list[dict] = []
    for prices, evidence in clusters:
        if not evidence:
            continue
        lower = min(prices)
        upper = max(prices)
        mid = (lower + upper) / 2.0
        strength = _score_strength(evidence)
        zones.append(
            {
                "lower": lower,
                "upper": upper,
                "mid": mid,
                "strength": strength,
                "evidence": evidence,
            }
        )

    zones.sort(
        key=lambda zone: (
            _STRENGTH_ORDER[zone["strength"]],
            abs(float(zone["mid"]) - reference),
        )
    )
    zones = zones[:_MAX_ZONES_PER_SIDE]

    ranked: list[dict] = []
    for index, zone in enumerate(zones, start=1):
        ranked.append(
            {
                "rank": index,
                "lower": _round_price(float(zone["lower"])),
                "upper": _round_price(float(zone["upper"])),
                "strength": zone["strength"],
                "evidence": list(zone["evidence"]),
            }
        )
    return ranked


def _cluster_candidates(
    candidates: list[tuple[float, str]],
    *,
    reference: float,
) -> list[tuple[list[float], list[str]]]:
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item[0])
    max_width = reference * _MAX_ZONE_WIDTH_PCT

    clusters: list[tuple[list[float], list[str]]] = []
    current_prices = [ordered[0][0]]
    current_labels = [ordered[0][1]]

    for price, label in ordered[1:]:
        mid = (current_prices[0] + price) / 2.0
        tolerance = _cluster_tolerance(mid)
        width_if_added = price - min(current_prices)
        nearby = abs(price - current_prices[-1]) <= tolerance
        within_cap = width_if_added <= max_width + 1e-12
        if nearby and within_cap:
            current_prices.append(price)
            current_labels.append(label)
            continue
        clusters.append((current_prices, _unique_evidence(current_labels)))
        current_prices = [price]
        current_labels = [label]

    clusters.append((current_prices, _unique_evidence(current_labels)))
    return clusters


def _unique_evidence(labels: list[str]) -> list[str]:
    """Preserve first-seen order; drop internal-only prior labels."""
    seen: set[str] = set()
    ordered: list[str] = []
    for label in labels:
        if label.startswith("_") or label in seen:
            continue
        seen.add(label)
        ordered.append(label)
    return ordered


def _score_strength(evidence: list[str]) -> Strength:
    """Map evidence to weak/moderate/strong.

    Score ≈ unique labels, plus bonuses for longer MAs and repeated touches.
    ``latest_high``+``high_20d`` (or low pair) count once toward the base total
    when both appear, since they often share the same candle extreme.
    """
    labels = set(evidence)
    base = len(labels)
    if {"latest_high", "high_20d"} <= labels:
        base -= 1
    if {"latest_low", "low_20d"} <= labels:
        base -= 1
    if {"high_20d", "high_60d"} <= labels:
        base -= 1
    if {"low_20d", "low_60d"} <= labels:
        base -= 1

    score = base
    if "sma200" in labels:
        score += 2
    if "sma60" in labels:
        score += 1
    if "sma50" in labels:
        score += 1
    if "repeated_touch" in labels:
        score += 2
    if "consolidation_boundary" in labels:
        score += 1
    if "role_reversal" in labels:
        score += 1

    if score >= 5:
        return "strong"
    if score >= 3:
        return "moderate"
    return "weak"
