from decimal import Decimal

import pytest

from app.services.tw_price_limits import (
    classify_taiwan_price_limit,
    restore_cached_price_limit,
    limit_down_price,
    limit_up_price,
    parse_price,
    parse_signed_decimal,
)


def test_equity_limit_prices_match_published_examples() -> None:
    # 2330 next-session band from TWSE TWT84U: reference 2475 -> 2720 / 2230.
    assert limit_up_price(Decimal("2475"), kind="equity") == Decimal("2720")
    assert limit_down_price(Decimal("2475"), kind="equity") == Decimal("2230")
    # Tick of the unrounded limit, not the reference: 9.15 * 1.1 = 10.065 -> 10.05.
    assert limit_up_price(Decimal("9.15"), kind="equity") == Decimal("10.05")
    assert limit_down_price(Decimal("9.15"), kind="equity") == Decimal("8.24")
    assert limit_up_price(Decimal("45.45"), kind="equity") == Decimal("49.95")
    assert limit_down_price(Decimal("45.45"), kind="equity") == Decimal("40.95")


@pytest.mark.parametrize(
    ("reference", "limit_up", "limit_down"),
    [
        # Tick of the unrounded limit, including the step up at 50 / 100 / 500 / 1000.
        (Decimal("20.1"), Decimal("22.10"), Decimal("18.10")),
        (Decimal("45.50"), Decimal("50.0"), Decimal("40.95")),
        (Decimal("60.5"), Decimal("66.5"), Decimal("54.5")),
        (Decimal("90.95"), Decimal("100.0"), Decimal("81.9")),
        (Decimal("200.5"), Decimal("220.5"), Decimal("180.5")),
        (Decimal("455"), Decimal("500"), Decimal("409.5")),
        (Decimal("600.5"), Decimal("660"), Decimal("541")),
        (Decimal("910"), Decimal("1000"), Decimal("819")),
    ],
)
def test_equity_ticks_follow_the_unrounded_limit_band(
    reference: Decimal,
    limit_up: Decimal,
    limit_down: Decimal,
) -> None:
    assert limit_up_price(reference, kind="equity") == limit_up
    assert limit_down_price(reference, kind="equity") == limit_down


def test_etf_limit_prices_use_the_finer_tick() -> None:
    # 0050: 112.40 * 1.1 = 123.64 floors to 123.60, not the equity 0.5 tick.
    assert limit_up_price(Decimal("112.40"), kind="etf") == Decimal("123.60")
    assert limit_down_price(Decimal("112.40"), kind="etf") == Decimal("101.20")
    assert limit_up_price(Decimal("49.54"), kind="etf") == Decimal("54.45")
    assert limit_down_price(Decimal("49.54"), kind="etf") == Decimal("44.59")


def test_authoritative_limit_beats_a_ten_percent_guess() -> None:
    status, _reference, limit_up, _limit_down = classify_taiwan_price_limit(
        last_price=Decimal("11.10"),
        reference_price=Decimal("10.15"),
        limit_up_price_value=Decimal("11.10"),
        limit_down_price_value=Decimal("9.12"),
        traded=True,
        symbol="1441",
    )
    assert status == "limit_up"
    assert limit_up == Decimal("11.10")
    # 10.15 * 1.1 floors to 11.15 on the equity tick. That price is not the limit.
    near_ten_percent, *_rest = classify_taiwan_price_limit(
        last_price=Decimal("11.15"),
        reference_price=Decimal("10.15"),
        limit_up_price_value=Decimal("11.10"),
        limit_down_price_value=Decimal("9.12"),
        traded=True,
        symbol="1441",
    )
    assert near_ten_percent == "none"


def test_regular_quote_does_not_calculate_a_limit_when_the_official_band_is_missing() -> None:
    """Rankings may calculate; a quote with no official u/w must stay unknown."""
    calculated, *_calculated_prices = classify_taiwan_price_limit(
        last_price=Decimal("110"),
        reference_price=Decimal("100"),
        limit_up_price_value=None,
        limit_down_price_value=None,
        traded=True,
        symbol="2330",
    )
    assert calculated == "limit_up"

    status, reference, limit_up, limit_down = classify_taiwan_price_limit(
        last_price=Decimal("110"),
        reference_price=Decimal("100"),
        limit_up_price_value=None,
        limit_down_price_value=None,
        traded=True,
        symbol="2330",
        allow_calculated=False,
    )
    assert status == "unknown"
    assert reference == Decimal("100")
    assert limit_up is None
    assert limit_down is None


def test_calculated_boundary_is_not_a_rounded_percentage() -> None:
    status, _reference, limit_up, _limit_down = classify_taiwan_price_limit(
        last_price=Decimal("10.05"),
        reference_price=Decimal("9.15"),
        limit_up_price_value=None,
        limit_down_price_value=None,
        traded=True,
        symbol="1101",
    )
    assert status == "limit_up"
    assert limit_up == Decimal("10.05")
    just_off, *_rest = classify_taiwan_price_limit(
        last_price=Decimal("10.06"),
        reference_price=Decimal("9.15"),
        limit_up_price_value=None,
        limit_down_price_value=None,
        traded=True,
        symbol="1101",
    )
    assert just_off == "none"


@pytest.mark.parametrize(
    ("last", "reference", "limit_up", "limit_down", "traded", "symbol", "expected"),
    [
        (Decimal("90"), Decimal("100"), Decimal("110"), Decimal("90"), True, "2330", "limit_down"),
        (Decimal("100"), Decimal("100"), Decimal("110"), Decimal("90"), True, "2330", "none"),
        (None, Decimal("100"), Decimal("110"), Decimal("90"), False, "2330", "unknown"),
        (Decimal("110"), Decimal("100"), None, Decimal("90"), True, "2330", "unknown"),
        (Decimal("110"), None, None, None, True, "03001", "unknown"),
        (Decimal("10"), Decimal("10"), Decimal("9999.95"), Decimal("0.01"), True, "00402", "none"),
    ],
)
def test_classify_missing_suspended_and_no_limit_band(
    last: Decimal | None,
    reference: Decimal | None,
    limit_up: Decimal | None,
    limit_down: Decimal | None,
    traded: bool,
    symbol: str,
    expected: str,
) -> None:
    status, *_prices = classify_taiwan_price_limit(
        last_price=last,
        reference_price=reference,
        limit_up_price_value=limit_up,
        limit_down_price_value=limit_down,
        traded=traded,
        symbol=symbol,
    )
    assert status == expected


def test_parse_signed_decimal_keeps_negative_change_and_rejects_prices() -> None:
    assert parse_signed_decimal("-9.94") == Decimal("-9.94")
    assert parse_price("-9.94") is None
    assert parse_signed_decimal("—") is None


def test_restore_cached_price_limit_keeps_a_consistent_payload() -> None:
    status, reference, limit_up, limit_down = restore_cached_price_limit(
        last_price=110.0,
        status="limit_up",
        reference_price=100,
        limit_up_price=110,
        limit_down_price=90,
    )
    assert status == "limit_up"
    assert reference == pytest.approx(100)
    assert limit_up == pytest.approx(110)
    assert limit_down == pytest.approx(90)


def test_restore_cached_price_limit_drops_inconsistent_or_legacy_payloads() -> None:
    assert restore_cached_price_limit(
        last_price=105.0,
        status=None,
        reference_price=None,
        limit_up_price=None,
        limit_down_price=None,
    ) == (None, None, None, None)
    assert restore_cached_price_limit(
        last_price=105.0,
        status="limit_up",
        reference_price=100,
        limit_up_price=110,
        limit_down_price=90,
    ) == ("unknown", None, None, None)
    assert restore_cached_price_limit(
        last_price=110.0,
        status="limit_down",
        reference_price=100,
        limit_up_price=110,
        limit_down_price=None,
    ) == ("unknown", None, None, None)
