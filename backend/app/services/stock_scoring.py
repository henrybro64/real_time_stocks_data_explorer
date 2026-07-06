from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar


TStock = TypeVar("TStock")


@dataclass(frozen=True)
class FactorScore:
    factor_name: str
    raw_score: float
    weight: float
    weighted_score: float


@dataclass(frozen=True)
class StockScore:
    total_score: float
    factor_scores: tuple[FactorScore, ...]


class StockScoringFactor(Protocol[TStock]):
    name: str
    weight: float

    def score(self, stock: TStock) -> float: ...


ScoreCombiner = Callable[[Sequence[FactorScore]], float]


def weighted_sum_combiner(factor_scores: Sequence[FactorScore]) -> float:
    return sum(item.weighted_score for item in factor_scores)


class StockScoringEngine(Generic[TStock]):
    def __init__(
        self,
        factors: Sequence[StockScoringFactor[TStock]],
        *,
        combiner: ScoreCombiner = weighted_sum_combiner,
    ) -> None:
        self._factors = tuple(factors)
        self._combiner = combiner
        self._validate_factors()

    def score(self, stock: TStock) -> StockScore:
        factor_scores: list[FactorScore] = []
        for factor in self._factors:
            raw_score = float(factor.score(stock))
            weight = float(factor.weight)
            factor_scores.append(
                FactorScore(
                    factor_name=factor.name.strip(),
                    raw_score=raw_score,
                    weight=weight,
                    weighted_score=raw_score * weight,
                )
            )
        return StockScore(
            total_score=float(self._combiner(factor_scores)),
            factor_scores=tuple(factor_scores),
        )

    def _validate_factors(self) -> None:
        seen: set[str] = set()
        for factor in self._factors:
            name = factor.name.strip()
            if not name:
                raise ValueError("Scoring factor name must not be empty.")
            if name in seen:
                raise ValueError(f"Duplicate scoring factor name: {name}")
            seen.add(name)
