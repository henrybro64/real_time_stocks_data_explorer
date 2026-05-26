from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Callable

from app.core.diagnostics import elapsed_ms
from app.db.models import PortfolioPosition
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.ai_providers import AIProvider, AIProviderError
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.portfolio_service import portfolio_service
from app.services.quote_service import quote_service
from app.services.user_ai_provider_service import resolve_user_ai_provider

logger = logging.getLogger(__name__)

AI_NOT_CONFIGURED_ERROR = (
    "AI is not configured. Please set up your AI provider in AI Settings."
)
EMPTY_PORTFOLIO_ERROR = "Portfolio is empty."
NO_ANALYZABLE_HOLDINGS_ERROR = (
    "Portfolio analysis requires at least one holding with an available quote."
)
AI_TEMPORARILY_UNAVAILABLE_WARNING = (
    "AI analysis is temporarily unavailable, but computed portfolio metrics are still available."
)
PORTFOLIO_ANALYSIS_DISCLAIMER = "AI-generated analysis, not financial advice."

PositionLoader = Callable[[str], list[PortfolioPosition]]
StockLoader = Callable[[str], StockRecord | None]
QuoteLoader = Callable[[str], QuoteRecord | None]
FxSnapshotLoader = Callable[[], dict[str, object]]
ProviderResolver = Callable[[str], AIProvider | None]


@dataclass(frozen=True)
class PortfolioAnalysisHolding:
    stock_id: str
    symbol: str
    name: str
    market: str
    exchange: str
    currency: str
    quantity: float
    average_cost: float
    last_price: float
    cost_basis: float
    cost_basis_usd: float
    cost_basis_twd: float
    market_value: float
    market_value_usd: float
    market_value_twd: float
    unrealized_pl: float
    unrealized_pl_usd: float
    unrealized_pl_twd: float
    unrealized_pl_percent: float
    portfolio_weight_percent: float = 0.0
    notes: str | None = None

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "stockId": self.stock_id,
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "exchange": self.exchange,
            "currency": self.currency,
            "quantity": self.quantity,
            "averageCost": self.average_cost,
            "lastPrice": self.last_price,
            "costBasis": self.cost_basis,
            "costBasisUsd": self.cost_basis_usd,
            "costBasisTwd": self.cost_basis_twd,
            "marketValue": self.market_value,
            "marketValueUsd": self.market_value_usd,
            "marketValueTwd": self.market_value_twd,
            "unrealizedPl": self.unrealized_pl,
            "unrealizedPlUsd": self.unrealized_pl_usd,
            "unrealizedPlTwd": self.unrealized_pl_twd,
            "unrealizedPlPercent": self.unrealized_pl_percent,
            "portfolioWeightPercent": self.portfolio_weight_percent,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class PortfolioAnalysisSkippedHolding:
    stock_id: str
    symbol: str | None
    reason: str

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "stockId": self.stock_id,
            "symbol": self.symbol,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PortfolioAnalysisCurrencyExposure:
    currency: str
    market_value: float
    market_value_usd: float
    market_value_twd: float
    portfolio_weight_percent: float

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "marketValue": self.market_value,
            "marketValueUsd": self.market_value_usd,
            "marketValueTwd": self.market_value_twd,
            "portfolioWeightPercent": self.portfolio_weight_percent,
        }


@dataclass(frozen=True)
class PortfolioConcentrationRisk:
    level: str
    top_holding_weight_percent: float
    top3_weight_percent: float
    top5_weight_percent: float

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "level": self.level,
            "topHoldingWeightPercent": self.top_holding_weight_percent,
            "top3WeightPercent": self.top3_weight_percent,
            "top5WeightPercent": self.top5_weight_percent,
        }


@dataclass(frozen=True)
class ComputedPortfolioAnalysis:
    holdings_count: int
    analyzed_holdings_count: int
    skipped_holdings_count: int
    total_cost_basis_usd: float
    total_cost_basis_twd: float
    total_market_value_usd: float
    total_market_value_twd: float
    total_unrealized_pl_usd: float
    total_unrealized_pl_twd: float
    total_unrealized_pl_percent: float
    usd_twd_rate: float
    fx_timestamp: str
    currency_exposure: tuple[PortfolioAnalysisCurrencyExposure, ...]
    largest_holdings: tuple[PortfolioAnalysisHolding, ...]
    holdings: tuple[PortfolioAnalysisHolding, ...]
    skipped_holdings: tuple[PortfolioAnalysisSkippedHolding, ...]
    concentration_risk: PortfolioConcentrationRisk

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "holdingsCount": self.holdings_count,
            "analyzedHoldingsCount": self.analyzed_holdings_count,
            "skippedHoldingsCount": self.skipped_holdings_count,
            "totalCostBasisUsd": self.total_cost_basis_usd,
            "totalCostBasisTwd": self.total_cost_basis_twd,
            "totalMarketValueUsd": self.total_market_value_usd,
            "totalMarketValueTwd": self.total_market_value_twd,
            "totalUnrealizedPlUsd": self.total_unrealized_pl_usd,
            "totalUnrealizedPlTwd": self.total_unrealized_pl_twd,
            "totalUnrealizedPlPercent": self.total_unrealized_pl_percent,
            "usdTwdRate": self.usd_twd_rate,
            "fxTimestamp": self.fx_timestamp,
            "currencyExposure": [
                item.to_prompt_payload() for item in self.currency_exposure
            ],
            "largestHoldings": [
                item.to_prompt_payload() for item in self.largest_holdings
            ],
            "holdings": [item.to_prompt_payload() for item in self.holdings],
            "skippedHoldings": [
                item.to_prompt_payload() for item in self.skipped_holdings
            ],
            "concentrationRisk": self.concentration_risk.to_prompt_payload(),
        }


@dataclass(frozen=True)
class PortfolioAnalysisResult:
    computed_summary: ComputedPortfolioAnalysis
    ai_report: str | None
    provider: str | None
    model: str | None
    warnings: tuple[str, ...]
    disclaimer: str = PORTFOLIO_ANALYSIS_DISCLAIMER


class PortfolioAnalysisService:
    def __init__(
        self,
        *,
        position_loader: PositionLoader | None = None,
        stock_loader: StockLoader | None = None,
        quote_loader: QuoteLoader | None = None,
        fx_snapshot_loader: FxSnapshotLoader = get_usd_twd_fx_snapshot,
        provider_resolver: ProviderResolver = resolve_user_ai_provider,
    ) -> None:
        self._position_loader = position_loader or portfolio_service.list_positions
        self._stock_loader = stock_loader or quote_service.get_stock
        self._quote_loader = quote_loader or quote_service.get_quote
        self._fx_snapshot_loader = fx_snapshot_loader
        self._provider_resolver = provider_resolver

    def analyze_portfolio(self, user_id: str) -> PortfolioAnalysisResult:
        started_at = perf_counter()
        positions = self._position_loader(user_id)
        if not positions:
            raise ValueError(EMPTY_PORTFOLIO_ERROR)

        fx_snapshot = self._fx_snapshot_loader()
        usd_twd_rate = float(fx_snapshot["rate"])
        fx_timestamp = str(fx_snapshot["timestamp"])

        holdings: list[PortfolioAnalysisHolding] = []
        skipped_holdings: list[PortfolioAnalysisSkippedHolding] = []
        warnings: list[str] = []

        for position in positions:
            stock = self._stock_loader(position.stock_id)
            if stock is None:
                skipped_holdings.append(
                    PortfolioAnalysisSkippedHolding(
                        stock_id=position.stock_id,
                        symbol=None,
                        reason="Stock metadata is unavailable.",
                    )
                )
                continue

            if stock.currency.strip().upper() not in {"USD", "TWD"}:
                skipped_holdings.append(
                    PortfolioAnalysisSkippedHolding(
                        stock_id=position.stock_id,
                        symbol=stock.symbol,
                        reason="Stock currency is not supported for USD/TWD exposure analysis.",
                    )
                )
                continue

            if position.currency.strip().upper() != stock.currency.strip().upper():
                skipped_holdings.append(
                    PortfolioAnalysisSkippedHolding(
                        stock_id=position.stock_id,
                        symbol=stock.symbol,
                        reason="Holding currency does not match the stock currency.",
                    )
                )
                continue

            quote = self._quote_loader(position.stock_id)
            if quote is None:
                skipped_holdings.append(
                    PortfolioAnalysisSkippedHolding(
                        stock_id=position.stock_id,
                        symbol=stock.symbol,
                        reason="Current quote is unavailable.",
                    )
                )
                continue

            holdings.append(
                _build_holding(position, stock, quote, usd_twd_rate)
            )

        if not holdings:
            raise ValueError(NO_ANALYZABLE_HOLDINGS_ERROR)

        holdings = _apply_portfolio_weights(holdings)
        holdings.sort(key=lambda item: item.market_value_usd, reverse=True)
        exposures = _build_currency_exposure(holdings, usd_twd_rate)
        concentration_risk = _build_concentration_risk(holdings)
        computed_summary = ComputedPortfolioAnalysis(
            holdings_count=len(positions),
            analyzed_holdings_count=len(holdings),
            skipped_holdings_count=len(skipped_holdings),
            total_cost_basis_usd=round(sum(item.cost_basis_usd for item in holdings), 2),
            total_cost_basis_twd=round(sum(item.cost_basis_twd for item in holdings), 2),
            total_market_value_usd=round(sum(item.market_value_usd for item in holdings), 2),
            total_market_value_twd=round(sum(item.market_value_twd for item in holdings), 2),
            total_unrealized_pl_usd=round(sum(item.unrealized_pl_usd for item in holdings), 2),
            total_unrealized_pl_twd=round(sum(item.unrealized_pl_twd for item in holdings), 2),
            total_unrealized_pl_percent=_total_unrealized_pl_percent(holdings),
            usd_twd_rate=usd_twd_rate,
            fx_timestamp=fx_timestamp,
            currency_exposure=tuple(exposures),
            largest_holdings=tuple(holdings[:5]),
            holdings=tuple(holdings),
            skipped_holdings=tuple(skipped_holdings),
            concentration_risk=concentration_risk,
        )

        if skipped_holdings:
            warnings.append(
                "Skipped holdings from AI analysis because stock metadata or current quotes were unavailable: "
                + ", ".join(item.stock_id for item in skipped_holdings)
            )

        provider = self._provider_resolver(user_id)
        if provider is None:
            raise ValueError(AI_NOT_CONFIGURED_ERROR)

        provider_name = getattr(provider, "provider_name", None)
        model_name = getattr(provider, "model_name", None)
        ai_report: str | None = None

        ai_started_at = perf_counter()
        try:
            ai_result = provider.generate_portfolio_analysis(
                computed_summary.to_prompt_payload()
            )
            ai_report = ai_result.content
            provider_name = ai_result.provider
            model_name = ai_result.model
            logger.info(
                "Portfolio AI analysis completed",
                extra={
                    "event": "portfolio_analysis.ai.completed",
                    "user_id": user_id,
                    "provider": provider_name,
                    "model": model_name,
                    "duration_ms": elapsed_ms(ai_started_at),
                },
            )
        except AIProviderError as exc:
            logger.warning(
                "Portfolio AI provider failed",
                extra={
                    "event": "portfolio_analysis.ai.failed",
                    "user_id": user_id,
                    "provider": provider_name,
                    "model": model_name,
                    "duration_ms": elapsed_ms(ai_started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            warnings.append(AI_TEMPORARILY_UNAVAILABLE_WARNING)

        result = PortfolioAnalysisResult(
            computed_summary=computed_summary,
            ai_report=ai_report,
            provider=provider_name,
            model=model_name,
            warnings=tuple(dict.fromkeys(warnings)),
        )
        logger.info(
            "Portfolio analysis completed",
            extra={
                "event": "portfolio_analysis.completed",
                "user_id": user_id,
                "provider": result.provider,
                "model": result.model,
                "duration_ms": elapsed_ms(started_at),
                "position_count": len(positions),
                "analyzed_holdings_count": result.computed_summary.analyzed_holdings_count,
                "skipped_holdings_count": result.computed_summary.skipped_holdings_count,
                "warning_count": len(result.warnings),
                "ai_generated": bool(result.ai_report),
            },
        )
        return result


def _build_holding(
    position: PortfolioPosition,
    stock: StockRecord,
    quote: QuoteRecord,
    usd_twd_rate: float,
) -> PortfolioAnalysisHolding:
    currency = stock.currency.strip().upper()
    cost_basis = round(position.quantity * position.average_cost, 2)
    market_value = round(position.quantity * quote.last_price, 2)
    unrealized_pl = round(market_value - cost_basis, 2)
    unrealized_pl_percent = (
        0.0 if cost_basis == 0 else round((unrealized_pl / cost_basis) * 100, 2)
    )
    cost_basis_usd = round(_convert_to_usd(currency, cost_basis, usd_twd_rate), 2)
    cost_basis_twd = round(_convert_to_twd(currency, cost_basis, usd_twd_rate), 2)
    market_value_usd = round(_convert_to_usd(currency, market_value, usd_twd_rate), 2)
    market_value_twd = round(_convert_to_twd(currency, market_value, usd_twd_rate), 2)
    unrealized_pl_usd = round(market_value_usd - cost_basis_usd, 2)
    unrealized_pl_twd = round(market_value_twd - cost_basis_twd, 2)

    return PortfolioAnalysisHolding(
        stock_id=position.stock_id,
        symbol=stock.symbol,
        name=stock.name_en or stock.name_zh_tw or stock.name,
        market=stock.market,
        exchange=stock.exchange,
        currency=currency,
        quantity=position.quantity,
        average_cost=position.average_cost,
        last_price=quote.last_price,
        cost_basis=cost_basis,
        cost_basis_usd=cost_basis_usd,
        cost_basis_twd=cost_basis_twd,
        market_value=market_value,
        market_value_usd=market_value_usd,
        market_value_twd=market_value_twd,
        unrealized_pl=unrealized_pl,
        unrealized_pl_usd=unrealized_pl_usd,
        unrealized_pl_twd=unrealized_pl_twd,
        unrealized_pl_percent=unrealized_pl_percent,
        notes=position.notes,
    )


def _apply_portfolio_weights(
    holdings: list[PortfolioAnalysisHolding],
) -> list[PortfolioAnalysisHolding]:
    total_market_value_usd = sum(item.market_value_usd for item in holdings)
    if total_market_value_usd <= 0:
        return holdings

    return [
        replace(
            item,
            portfolio_weight_percent=round(
                (item.market_value_usd / total_market_value_usd) * 100,
                2,
            ),
        )
        for item in holdings
    ]


def _build_currency_exposure(
    holdings: list[PortfolioAnalysisHolding],
    usd_twd_rate: float,
) -> list[PortfolioAnalysisCurrencyExposure]:
    total_market_value_usd = sum(item.market_value_usd for item in holdings)
    by_currency: dict[str, float] = {}
    for item in holdings:
        by_currency[item.currency] = by_currency.get(item.currency, 0.0) + item.market_value

    ordered_currencies = ["USD", "TWD"] + sorted(
        currency for currency in by_currency if currency not in {"USD", "TWD"}
    )
    exposures: list[PortfolioAnalysisCurrencyExposure] = []
    for currency in ordered_currencies:
        if currency not in by_currency:
            continue
        market_value = round(by_currency[currency], 2)
        market_value_usd = round(_convert_to_usd(currency, market_value, usd_twd_rate), 2)
        market_value_twd = round(_convert_to_twd(currency, market_value, usd_twd_rate), 2)
        weight = (
            0.0
            if total_market_value_usd <= 0
            else round((market_value_usd / total_market_value_usd) * 100, 2)
        )
        exposures.append(
            PortfolioAnalysisCurrencyExposure(
                currency=currency,
                market_value=market_value,
                market_value_usd=market_value_usd,
                market_value_twd=market_value_twd,
                portfolio_weight_percent=weight,
            )
        )
    return exposures


def _build_concentration_risk(
    holdings: list[PortfolioAnalysisHolding],
) -> PortfolioConcentrationRisk:
    weights = sorted(
        (item.portfolio_weight_percent for item in holdings),
        reverse=True,
    )
    top_holding = round(weights[0] if weights else 0.0, 2)
    top3 = round(sum(weights[:3]), 2)
    top5 = round(sum(weights[:5]), 2)
    return PortfolioConcentrationRisk(
        level=_classify_concentration(top_holding, top3),
        top_holding_weight_percent=top_holding,
        top3_weight_percent=top3,
        top5_weight_percent=top5,
    )


def _classify_concentration(top_holding: float, top3: float) -> str:
    if top_holding >= 50 or top3 >= 80:
        return "high"
    if top_holding >= 30 or top3 >= 60:
        return "moderate"
    return "low"


def _total_unrealized_pl_percent(
    holdings: list[PortfolioAnalysisHolding],
) -> float:
    total_cost_basis_usd = sum(item.cost_basis_usd for item in holdings)
    total_unrealized_pl_usd = sum(item.unrealized_pl_usd for item in holdings)
    if total_cost_basis_usd <= 0:
        return 0.0
    return round((total_unrealized_pl_usd / total_cost_basis_usd) * 100, 2)


def _convert_to_usd(currency: str, value: float, usd_twd_rate: float) -> float:
    normalized = currency.strip().upper()
    if normalized == "USD":
        return value
    if normalized == "TWD":
        return value / usd_twd_rate if usd_twd_rate > 0 else 0.0
    return 0.0


def _convert_to_twd(currency: str, value: float, usd_twd_rate: float) -> float:
    normalized = currency.strip().upper()
    if normalized == "TWD":
        return value
    if normalized == "USD":
        return value * usd_twd_rate
    return 0.0


portfolio_analysis_service = PortfolioAnalysisService()
