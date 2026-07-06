from types import SimpleNamespace

import pytest

from app.models.quote import QuoteRecord
from app.services.portfolio_service import PortfolioService


def test_build_cost_basis_rounds_position_value() -> None:
    position = SimpleNamespace(quantity=1.5, average_cost=100.125)

    cost_basis = PortfolioService.build_cost_basis(position)

    assert cost_basis == 150.19


def test_build_metrics_cost_basis_stays_consistent_with_rounded_helper() -> None:
    position = SimpleNamespace(quantity=1.333, average_cost=10.005)
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=10.777,
        change=0.0,
        change_percent=0.0,
        previous_close=10.7,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )

    cost_basis = PortfolioService.build_cost_basis(position)
    market_value, unrealized_pl, unrealized_pl_percent = PortfolioService.build_metrics(
        position,
        quote,
    )

    assert cost_basis == 13.34
    assert market_value == 14.37
    assert unrealized_pl == 1.03
    assert unrealized_pl_percent == 7.72
    assert round(market_value - unrealized_pl, 2) == cost_basis


def test_summarize_position_buckets_uses_true_cost_basis_for_gain_percent() -> None:
    true_cost_basis = 13.34
    back_calculated_cost_basis = round(14.37 - 1.03, 2)
    assert true_cost_basis == back_calculated_cost_basis

    totals = PortfolioService.summarize_position_buckets(
        [("USD", 14.37, true_cost_basis)],
        32.0,
    )

    assert totals["combinedGainUsd"] == 1.03
    assert totals["combinedGainPercent"] == 7.72


def test_build_metrics_calculates_market_value_and_pl() -> None:
    position = SimpleNamespace(quantity=2.0, average_cost=100.0)
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=110.0,
        change=0.0,
        change_percent=0.0,
        previous_close=109.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )

    metrics = PortfolioService.build_metrics(position, quote)

    assert metrics == (220.0, 20.0, 10.0)


def test_build_metrics_uses_cost_basis_when_quote_unavailable() -> None:
    position = SimpleNamespace(quantity=3.0, average_cost=900.0)

    metrics = PortfolioService.build_metrics(position, None)

    assert metrics == (2700.0, 0.0, 0.0)


def test_build_metrics_handles_zero_cost_basis() -> None:
    position = SimpleNamespace(quantity=3.0, average_cost=0.0)
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=50.0,
        change=0.0,
        change_percent=0.0,
        previous_close=50.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )

    metrics = PortfolioService.build_metrics(position, quote)

    assert metrics == (150.0, 150.0, 0.0)


def test_summarize_currency_buckets_combines_usd_and_twd() -> None:
    totals = PortfolioService.summarize_currency_buckets(
        [("USD", 100.0), ("TWD", 640.0), ("USD", 25.5)],
        32.0,
    )

    assert totals == {
        "totalUsd": 125.5,
        "totalTwd": 640.0,
        "combinedUsd": 145.5,
        "combinedTwd": 4656.0,
    }


def test_summarize_currency_buckets_avoids_conversion_when_fx_missing() -> None:
    totals = PortfolioService.summarize_currency_buckets(
        [("USD", 100.0), ("TWD", 640.0)],
        0.0,
    )

    assert totals == {
        "totalUsd": 100.0,
        "totalTwd": 640.0,
        "combinedUsd": 100.0,
        "combinedTwd": 640.0,
    }


def test_summarize_currency_buckets_normalizes_currency_labels() -> None:
    totals = PortfolioService.summarize_currency_buckets(
        [(" usd ", 100.0), ("twd", 640.0)],
        32.0,
    )

    assert totals == {
        "totalUsd": 100.0,
        "totalTwd": 640.0,
        "combinedUsd": 120.0,
        "combinedTwd": 3840.0,
    }


def test_summarize_allocation_percents_mixed_usd_and_twd_portfolio() -> None:
    allocations = PortfolioService.summarize_allocation_percents(
        [("USD", 250.0), ("TWD", 3200.0)],
        32.0,
    )

    assert allocations == [71.43, 28.57]
    assert round(sum(allocations), 2) == 100.0


def test_summarize_allocation_percents_returns_zero_when_total_is_zero() -> None:
    allocations = PortfolioService.summarize_allocation_percents(
        [("USD", 0.0), ("TWD", 0.0)],
        32.0,
    )

    assert allocations == [0.0, 0.0]


def test_validate_holding_currency_rejects_mismatch() -> None:
    stock = SimpleNamespace(currency="TWD")

    with pytest.raises(ValueError, match="does not match"):
        PortfolioService._validate_holding_currency(stock, "USD")


def test_validate_holding_currency_normalizes_supported_values() -> None:
    stock = SimpleNamespace(currency="USD")

    assert PortfolioService._validate_holding_currency(stock, " usd ") == "USD"


def test_summarize_position_buckets_mixed_usd_and_twd_portfolio() -> None:
    totals = PortfolioService.summarize_position_buckets(
        [("USD", 220.0, 200.0), ("TWD", 3200.0, 3000.0)],
        32.0,
    )

    assert totals["combinedGainUsd"] == 26.25
    assert totals["combinedGainTwd"] == 840.0
    assert totals["combinedGainPercent"] == 8.94


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (
            [("USD", 220.0, 200.0)],
            {
                "combinedGainUsd": 20.0,
                "combinedGainTwd": 640.0,
                "combinedGainPercent": 10.0,
            },
        ),
        (
            [("USD", 180.0, 200.0)],
            {
                "combinedGainUsd": -20.0,
                "combinedGainTwd": -640.0,
                "combinedGainPercent": -10.0,
            },
        ),
        (
            [("TWD", 3200.0, 3200.0)],
            {
                "combinedGainUsd": 0.0,
                "combinedGainTwd": 0.0,
                "combinedGainPercent": 0.0,
            },
        ),
        (
            [],
            {
                "combinedGainUsd": 0.0,
                "combinedGainTwd": 0.0,
                "combinedGainPercent": 0.0,
            },
        ),
        (
            [("USD", 150.0, 0.0)],
            {
                "combinedGainUsd": 150.0,
                "combinedGainTwd": 4800.0,
                "combinedGainPercent": 0.0,
            },
        ),
    ],
)
def test_summarize_position_buckets_calculates_combined_gain(
    entries: list[tuple[str, float, float]],
    expected: dict[str, float],
) -> None:
    totals = PortfolioService.summarize_position_buckets(entries, 32.0)

    assert totals["combinedGainUsd"] == expected["combinedGainUsd"]
    assert totals["combinedGainTwd"] == expected["combinedGainTwd"]
    assert totals["combinedGainPercent"] == expected["combinedGainPercent"]
