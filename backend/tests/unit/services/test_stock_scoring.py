from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.stock_scoring import StockScoringEngine


@dataclass(frozen=True)
class _StockSnapshot:
    revenue_growth: float
    eps_growth: float
    analyst_rating: float


@dataclass(frozen=True)
class _RevenueGrowthFactor:
    name: str = "revenue_growth"
    weight: float = 0.6

    def score(self, stock: _StockSnapshot) -> float:
        return stock.revenue_growth


@dataclass(frozen=True)
class _EpsGrowthFactor:
    name: str = "eps_growth"
    weight: float = 0.4

    def score(self, stock: _StockSnapshot) -> float:
        return stock.eps_growth


def test_stock_scoring_engine_combines_pluggable_factor_scores() -> None:
    stock = _StockSnapshot(
        revenue_growth=8.0,
        eps_growth=5.0,
        analyst_rating=0.0,
    )
    engine = StockScoringEngine(
        [_RevenueGrowthFactor(), _EpsGrowthFactor()],
    )

    result = engine.score(stock)

    assert result.total_score == pytest.approx(6.8)
    assert [item.factor_name for item in result.factor_scores] == [
        "revenue_growth",
        "eps_growth",
    ]
    assert result.factor_scores[0].weighted_score == pytest.approx(4.8)
    assert result.factor_scores[1].weighted_score == pytest.approx(2.0)


def test_stock_scoring_engine_supports_new_factor_without_core_changes() -> None:
    @dataclass(frozen=True)
    class _AnalystRatingFactor:
        name: str = "analyst_rating"
        weight: float = 1.5

        def score(self, stock: _StockSnapshot) -> float:
            return stock.analyst_rating

    stock = _StockSnapshot(
        revenue_growth=0.0,
        eps_growth=0.0,
        analyst_rating=4.0,
    )
    engine = StockScoringEngine([_AnalystRatingFactor()])

    result = engine.score(stock)

    assert result.total_score == pytest.approx(6.0)
    assert result.factor_scores[0].factor_name == "analyst_rating"


def test_stock_scoring_engine_supports_custom_combiners() -> None:
    stock = _StockSnapshot(
        revenue_growth=8.0,
        eps_growth=5.0,
        analyst_rating=0.0,
    )
    engine = StockScoringEngine(
        [_RevenueGrowthFactor(), _EpsGrowthFactor()],
        combiner=lambda factor_scores: max(item.weighted_score for item in factor_scores),
    )

    result = engine.score(stock)

    assert result.total_score == pytest.approx(4.8)


def test_stock_scoring_engine_rejects_duplicate_factor_names() -> None:
    with pytest.raises(ValueError, match="Duplicate scoring factor name"):
        StockScoringEngine(
            [_RevenueGrowthFactor(), _RevenueGrowthFactor()],
        )


def test_stock_scoring_engine_rejects_whitespace_padded_duplicate_factor_names() -> None:
    @dataclass(frozen=True)
    class _PaddedRevenueGrowthFactor:
        name: str = " revenue_growth "
        weight: float = 0.6

        def score(self, stock: _StockSnapshot) -> float:
            return stock.revenue_growth

    with pytest.raises(ValueError, match="Duplicate scoring factor name: revenue_growth"):
        StockScoringEngine(
            [_RevenueGrowthFactor(), _PaddedRevenueGrowthFactor()],
        )


def test_stock_scoring_engine_normalizes_factor_names_in_results() -> None:
    @dataclass(frozen=True)
    class _PaddedRevenueGrowthFactor:
        name: str = " revenue_growth "
        weight: float = 1.0

        def score(self, stock: _StockSnapshot) -> float:
            return stock.revenue_growth

    stock = _StockSnapshot(
        revenue_growth=8.0,
        eps_growth=0.0,
        analyst_rating=0.0,
    )
    engine = StockScoringEngine([_PaddedRevenueGrowthFactor()])

    result = engine.score(stock)

    assert result.factor_scores[0].factor_name == "revenue_growth"


@pytest.mark.parametrize("factor_name", ["", "   "])
def test_stock_scoring_engine_rejects_empty_factor_names(factor_name: str) -> None:
    @dataclass(frozen=True)
    class _BlankNameFactor:
        name: str = factor_name
        weight: float = 1.0

        def score(self, stock: _StockSnapshot) -> float:
            return stock.revenue_growth

    with pytest.raises(ValueError, match="Scoring factor name must not be empty"):
        StockScoringEngine([_BlankNameFactor()])


def test_stock_scoring_engine_handles_empty_factor_list() -> None:
    stock = _StockSnapshot(
        revenue_growth=8.0,
        eps_growth=5.0,
        analyst_rating=4.0,
    )
    engine = StockScoringEngine([])

    result = engine.score(stock)

    assert result.total_score == 0.0
    assert result.factor_scores == ()
