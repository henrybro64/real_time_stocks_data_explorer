from types import SimpleNamespace

import pytest

from app.models.quote import QuoteRecord
from app.services.portfolio_service import PortfolioService


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
