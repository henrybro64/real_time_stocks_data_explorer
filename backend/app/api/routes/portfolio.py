from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.db.models import PortfolioAsset, PortfolioPosition
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.portfolio import (
    ComputedPortfolioAnalysisResponse,
    PortfolioAnalysisCurrencyExposureResponse,
    PortfolioAnalysisHoldingResponse,
    PortfolioAnalysisResponse,
    PortfolioAnalysisSkippedHoldingResponse,
    PortfolioConcentrationRiskResponse,
    PortfolioPositionCreate,
    PortfolioAssetCreate,
    PortfolioHistoryPointResponse,
    PortfolioHistoryResponse,
    PortfolioHistorySeriesResponse,
    PortfolioAssetResponse,
    PortfolioAssetSummaryResponse,
    PortfolioAssetUpdate,
    PortfolioPositionResponse,
    PortfolioPositionUpdate,
    PortfolioSummaryPosition,
    PortfolioSummaryResponse,
)
from app.core.config import settings
from app.schemas.quote import ChartLayoutResponse, QuoteResponse, quote_to_response
from app.schemas.stock import StockResponse
from app.services.chart_layout import build_value_chart_layout
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.portfolio_history_service import (
    PortfolioHistorySeries,
    portfolio_history_service,
)
from app.services.portfolio_analysis_service import (
    PortfolioAnalysisHolding,
    PortfolioAnalysisResult,
    portfolio_analysis_service,
)
from app.services.portfolio_service import portfolio_service
from app.services.quote_service import quote_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)

_MISSING_QUOTE = object()


@router.get("/positions", response_model=list[PortfolioPositionResponse])
def list_portfolio_positions(
    user_id: str = Depends(get_effective_user_id),
) -> list[PortfolioPositionResponse]:
    positions = portfolio_service.list_positions(user_id)
    if not positions:
        return []

    quote_by_stock: dict[str, QuoteRecord | None] = {}
    workers = min(16, max(1, len(positions)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_sid = {
            pool.submit(quote_service.get_quote, p.stock_id): p.stock_id
            for p in positions
        }
        for fut in as_completed(future_to_sid):
            sid = future_to_sid[fut]
            try:
                quote_by_stock[sid] = fut.result()
            except Exception:
                quote_by_stock[sid] = None

    rows: list[PortfolioPositionResponse] = []
    for position in positions:
        try:
            q = quote_by_stock.get(position.stock_id)
            rows.append(_to_response(position, q))
        except HTTPException:
            continue
    return rows


@router.post("/positions", response_model=PortfolioPositionResponse)
def add_portfolio_position(
    payload: PortfolioPositionCreate,
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioPositionResponse:
    try:
        position = portfolio_service.add_position(
            user_id,
            stock_id=payload.stockId,
            quantity=payload.quantity,
            average_cost=payload.averageCost,
            currency=payload.currency,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh_history_snapshot(user_id)
    return _to_response(position)


@router.patch(
    "/positions/{position_id}",
    response_model=PortfolioPositionResponse,
)
def update_portfolio_position(
    position_id: int,
    payload: PortfolioPositionUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioPositionResponse:
    try:
        position = portfolio_service.update_position(
            user_id,
            position_id=position_id,
            quantity=payload.quantity,
            average_cost=payload.averageCost,
            currency=payload.currency,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _refresh_history_snapshot(user_id)
    return _to_response(position)


@router.delete("/positions/{position_id}", status_code=204)
def delete_portfolio_position(
    position_id: int,
    user_id: str = Depends(get_effective_user_id),
) -> None:
    try:
        portfolio_service.delete_position(user_id, position_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _refresh_history_snapshot(user_id)


@router.get("/assets", response_model=list[PortfolioAssetResponse])
def list_portfolio_assets(
    user_id: str = Depends(get_effective_user_id),
) -> list[PortfolioAssetResponse]:
    assets = portfolio_service.list_assets(user_id)
    return [_to_asset_response(asset) for asset in assets]


@router.get("/summary", response_model=PortfolioSummaryResponse)
def get_portfolio_summary(
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioSummaryResponse:
    positions = portfolio_service.list_positions(user_id)
    fx_snapshot = get_usd_twd_fx_snapshot()
    usd_twd_rate = float(fx_snapshot["rate"])
    rows, position_entries, skipped_stock_ids = _summarize_positions(positions)
    totals = portfolio_service.summarize_position_buckets(position_entries, usd_twd_rate)
    allocation_percents = portfolio_service.summarize_allocation_percents(
        [(currency, market_value) for currency, market_value, _ in position_entries],
        usd_twd_rate,
    )
    rows_with_allocations = [
        row.model_copy(update={"allocationPercent": allocation_percent})
        for row, allocation_percent in zip(rows, allocation_percents, strict=True)
    ]

    return PortfolioSummaryResponse(
        totalUsd=totals["totalUsd"],
        totalTwd=totals["totalTwd"],
        combinedUsd=totals["combinedUsd"],
        combinedTwd=totals["combinedTwd"],
        totalCostUsd=totals["totalCostUsd"],
        totalCostTwd=totals["totalCostTwd"],
        combinedCostUsd=totals["combinedCostUsd"],
        combinedCostTwd=totals["combinedCostTwd"],
        combinedGainUsd=totals["combinedGainUsd"],
        combinedGainTwd=totals["combinedGainTwd"],
        combinedGainPercent=totals["combinedGainPercent"],
        usdTwdRate=usd_twd_rate,
        fxTimestamp=str(fx_snapshot["timestamp"]),
        positions=rows_with_allocations,
        skippedStockIds=skipped_stock_ids,
    )


@router.post("/analysis", response_model=PortfolioAnalysisResponse)
def analyze_portfolio(
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioAnalysisResponse:
    try:
        result = portfolio_analysis_service.analyze_portfolio(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _portfolio_analysis_schema(result)


@router.get("/assets/summary", response_model=PortfolioAssetSummaryResponse)
def get_portfolio_asset_summary(
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioAssetSummaryResponse:
    positions = portfolio_service.list_positions(user_id)
    assets = portfolio_service.list_assets(user_id)
    fx_snapshot = get_usd_twd_fx_snapshot()
    usd_twd_rate = float(fx_snapshot["rate"])

    _, position_entries, skipped_stock_ids = _summarize_positions(positions)
    market_entries = [(currency, market_value) for currency, market_value, _ in position_entries]
    all_entries = list(market_entries)
    liquid_entries = list(market_entries)

    for asset in assets:
        value = round(float(asset.value), 2)
        all_entries.append((asset.currency, value))
        if asset.asset_type != "real_estate":
            liquid_entries.append((asset.currency, value))

    totals = portfolio_service.summarize_currency_buckets(all_entries, usd_twd_rate)
    liquid_totals = portfolio_service.summarize_currency_buckets(liquid_entries, usd_twd_rate)

    return PortfolioAssetSummaryResponse(
        totalUsd=totals["totalUsd"],
        totalTwd=totals["totalTwd"],
        combinedUsd=totals["combinedUsd"],
        combinedTwd=totals["combinedTwd"],
        liquidTotalUsd=liquid_totals["totalUsd"],
        liquidTotalTwd=liquid_totals["totalTwd"],
        liquidCombinedUsd=liquid_totals["combinedUsd"],
        liquidCombinedTwd=liquid_totals["combinedTwd"],
        usdTwdRate=usd_twd_rate,
        fxTimestamp=str(fx_snapshot["timestamp"]),
        skippedStockIds=skipped_stock_ids,
    )


@router.get("/history/holdings", response_model=PortfolioHistoryResponse)
def get_portfolio_holdings_history(
    range_name: str = Query(default="1M", alias="range"),
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioHistoryResponse:
    try:
        history = portfolio_history_service.list_metric_history(
            user_id,
            metric="holdings",
            range_name=range_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _history_response(range_name, "holdings", history)


@router.get("/history/combined-assets", response_model=PortfolioHistoryResponse)
def get_portfolio_combined_assets_history(
    range_name: str = Query(default="1M", alias="range"),
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioHistoryResponse:
    try:
        history = portfolio_history_service.list_metric_history(
            user_id,
            metric="combinedAssets",
            range_name=range_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _history_response(range_name, "combinedAssets", history)


@router.post("/history/capture")
def capture_portfolio_history_snapshots(
    x_portfolio_history_token: str | None = Header(default=None),
) -> dict[str, int]:
    expected = settings.portfolio_history_capture_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="History capture endpoint is not configured",
        )
    if x_portfolio_history_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    captured = portfolio_history_service.capture_due_snapshots_for_all_users(
        trigger="capture_endpoint",
    )
    return {"captured": captured}

@router.post("/assets", response_model=PortfolioAssetResponse)
def add_portfolio_asset(
    payload: PortfolioAssetCreate,
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioAssetResponse:
    asset = portfolio_service.add_asset(
        user_id,
        asset_type=payload.assetType,
        name=payload.name,
        currency=payload.currency,
        value=payload.value,
        notes=payload.notes,
    )
    _refresh_history_snapshot(user_id)
    return _to_asset_response(asset)


@router.patch("/assets/{asset_id}", response_model=PortfolioAssetResponse)
def update_portfolio_asset(
    asset_id: int,
    payload: PortfolioAssetUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> PortfolioAssetResponse:
    try:
        asset = portfolio_service.update_asset(
            user_id,
            asset_id=asset_id,
            asset_type=payload.assetType,
            name=payload.name,
            currency=payload.currency,
            value=payload.value,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _refresh_history_snapshot(user_id)
    return _to_asset_response(asset)


@router.delete("/assets/{asset_id}", status_code=204)
def delete_portfolio_asset(
    asset_id: int,
    user_id: str = Depends(get_effective_user_id),
) -> None:
    try:
        portfolio_service.delete_asset(user_id, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _refresh_history_snapshot(user_id)


def _summarize_positions(
    positions: list[PortfolioPosition],
) -> tuple[list[PortfolioSummaryPosition], list[tuple[str, float, float]], list[str]]:
    rows: list[PortfolioSummaryPosition] = []
    entries: list[tuple[str, float, float]] = []
    skipped_stock_ids: list[str] = []

    for position in positions:
        stock = quote_service.get_stock(position.stock_id)
        if stock is None:
            skipped_stock_ids.append(position.stock_id)
            continue
        quote = quote_service.get_quote(position.stock_id)
        market_value, unrealized_pl, _ = portfolio_service.build_metrics(position, quote)
        cost_basis = portfolio_service.build_cost_basis(position)
        currency = portfolio_service.normalize_currency(stock.currency)
        entries.append((currency, market_value, cost_basis))
        rows.append(
            PortfolioSummaryPosition(
                stockId=position.stock_id,
                symbol=stock.symbol,
                currency=currency,
                quantity=position.quantity,
                lastPrice=quote.last_price if quote is not None else None,
                marketValue=market_value,
                allocationPercent=0.0,
                timestamp=quote.timestamp if quote is not None else None,
            )
        )

    return rows, entries, skipped_stock_ids


def _to_response(
    position: PortfolioPosition,
    quote: QuoteRecord | None | object = _MISSING_QUOTE,
) -> PortfolioPositionResponse:
    stock = quote_service.get_stock(position.stock_id)
    if stock is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown stock id: {position.stock_id}",
        )
    if quote is _MISSING_QUOTE:
        quote = quote_service.get_quote(position.stock_id)
    market_value, unrealized_pl, unrealized_pl_percent = portfolio_service.build_metrics(
        position, quote
    )
    return PortfolioPositionResponse(
        id=position.id,
        stockId=position.stock_id,
        quantity=position.quantity,
        averageCost=position.average_cost,
        currentPrice=quote.last_price if quote is not None else None,
        costBasis=portfolio_service.build_cost_basis(position),
        currency=position.currency,
        notes=position.notes,
        stock=_stock_schema(stock),
        quote=_quote_schema(quote) if quote is not None else None,
        marketValue=market_value,
        unrealizedPl=unrealized_pl,
        unrealizedPlPercent=unrealized_pl_percent,
    )


def _stock_schema(stock: StockRecord) -> StockResponse:
    return StockResponse(
        id=stock.id,
        market=stock.market,
        symbol=stock.symbol,
        name=stock.name,
        exchange=stock.exchange,
        currency=stock.currency,
        timezone=stock.timezone,
        nameEn=stock.name_en,
        nameZhTw=stock.name_zh_tw,
    )


def _quote_schema(quote: QuoteRecord) -> QuoteResponse:
    return quote_to_response(quote)


def _to_asset_response(asset: PortfolioAsset) -> PortfolioAssetResponse:
    return PortfolioAssetResponse(
        id=asset.id,
        assetType=asset.asset_type,
        name=asset.name,
        currency=asset.currency,
        value=asset.value,
        notes=asset.notes,
    )


def _history_response(
    range_name: str,
    metric: str,
    history: dict[str, PortfolioHistorySeries],
) -> PortfolioHistoryResponse:
    usd_history = history["USD"]
    twd_history = history["TWD"]
    return PortfolioHistoryResponse(
        range=range_name,
        metric=metric,
        usd=_history_series_response(usd_history),
        twd=_history_series_response(twd_history),
    )


def _history_series_response(series: PortfolioHistorySeries) -> PortfolioHistorySeriesResponse:
    points = list(series.points)
    layout_raw = build_value_chart_layout([point.value for point in points])
    chart_layout = ChartLayoutResponse(**layout_raw) if layout_raw else None
    return PortfolioHistorySeriesResponse(
        currency=series.currency,
        points=[
            PortfolioHistoryPointResponse(
                timestamp=point.timestamp,
                sourceWindow=point.source_window,
                fxRate=point.fx_rate,
                value=point.value,
            )
            for point in points
        ],
        chartLayout=chart_layout,
    )


def _portfolio_analysis_schema(
    result: PortfolioAnalysisResult,
) -> PortfolioAnalysisResponse:
    computed_summary = result.computed_summary
    return PortfolioAnalysisResponse(
        computedSummary=ComputedPortfolioAnalysisResponse(
            holdingsCount=computed_summary.holdings_count,
            analyzedHoldingsCount=computed_summary.analyzed_holdings_count,
            skippedHoldingsCount=computed_summary.skipped_holdings_count,
            totalCostBasisUsd=computed_summary.total_cost_basis_usd,
            totalCostBasisTwd=computed_summary.total_cost_basis_twd,
            totalMarketValueUsd=computed_summary.total_market_value_usd,
            totalMarketValueTwd=computed_summary.total_market_value_twd,
            totalUnrealizedPlUsd=computed_summary.total_unrealized_pl_usd,
            totalUnrealizedPlTwd=computed_summary.total_unrealized_pl_twd,
            totalUnrealizedPlPercent=computed_summary.total_unrealized_pl_percent,
            usdTwdRate=computed_summary.usd_twd_rate,
            fxTimestamp=computed_summary.fx_timestamp,
            currencyExposure=[
                PortfolioAnalysisCurrencyExposureResponse(
                    currency=item.currency,
                    marketValue=item.market_value,
                    marketValueUsd=item.market_value_usd,
                    marketValueTwd=item.market_value_twd,
                    portfolioWeightPercent=item.portfolio_weight_percent,
                )
                for item in computed_summary.currency_exposure
            ],
            largestHoldings=[
                _portfolio_analysis_holding_schema(item)
                for item in computed_summary.largest_holdings
            ],
            holdings=[
                _portfolio_analysis_holding_schema(item)
                for item in computed_summary.holdings
            ],
            skippedHoldings=[
                PortfolioAnalysisSkippedHoldingResponse(
                    stockId=item.stock_id,
                    symbol=item.symbol,
                    reason=item.reason,
                )
                for item in computed_summary.skipped_holdings
            ],
            concentrationRisk=PortfolioConcentrationRiskResponse(
                level=computed_summary.concentration_risk.level,
                topHoldingWeightPercent=computed_summary.concentration_risk.top_holding_weight_percent,
                top3WeightPercent=computed_summary.concentration_risk.top3_weight_percent,
                top5WeightPercent=computed_summary.concentration_risk.top5_weight_percent,
            ),
        ),
        aiReport=result.ai_report,
        provider=result.provider,
        model=result.model,
        warnings=list(result.warnings),
        disclaimer=result.disclaimer,
    )


def _portfolio_analysis_holding_schema(
    item: PortfolioAnalysisHolding,
) -> PortfolioAnalysisHoldingResponse:
    return PortfolioAnalysisHoldingResponse(
        stockId=item.stock_id,
        symbol=item.symbol,
        name=item.name,
        market=item.market,
        exchange=item.exchange,
        currency=item.currency,
        quantity=item.quantity,
        averageCost=item.average_cost,
        lastPrice=item.last_price,
        costBasis=item.cost_basis,
        costBasisUsd=item.cost_basis_usd,
        costBasisTwd=item.cost_basis_twd,
        marketValue=item.market_value,
        marketValueUsd=item.market_value_usd,
        marketValueTwd=item.market_value_twd,
        unrealizedPl=item.unrealized_pl,
        unrealizedPlUsd=item.unrealized_pl_usd,
        unrealizedPlTwd=item.unrealized_pl_twd,
        unrealizedPlPercent=item.unrealized_pl_percent,
        portfolioWeightPercent=item.portfolio_weight_percent,
        notes=item.notes,
    )


def _refresh_history_snapshot(user_id: str) -> None:
    try:
        portfolio_history_service.capture_due_snapshots_for_user(
            user_id,
            trigger="portfolio_refresh",
        )
    except Exception as exc:
        logger.warning(
            "Portfolio history refresh failed after portfolio mutation",
            extra={
                "event": "portfolio_history.refresh.failed",
                "user_id": user_id,
                "trigger": "portfolio_refresh",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        # Snapshot collection should never block primary CRUD paths.
        return
