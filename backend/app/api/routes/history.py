from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.core.config import settings
from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.schemas.analysis import (
    ComputedStockAnalysisResponse,
    StockAnalysisCitationResponse,
    StockAnalysisResponse,
    StockAnalysisSearchResultResponse,
)
from app.schemas.quote import (
    CandleResponse,
    ChartLayoutResponse,
    HistoryEnvelope,
)
from app.schemas.stock import StockResponse
from app.schemas.tw_insights import TWStockInsightsResponse
from app.schemas.us_insights import USStockInsightsResponse
from app.services.chart_layout import build_chart_layout
from app.services.key_stats_service import get_key_statistics
from app.services.news_service import news_service
from app.services.quote_service import quote_service
from app.services.ai_providers import AIProvider
from app.services.stock_analysis_redis_cache import (
    CachedStockAnalysisPayload,
    get_cached_stock_analysis,
    set_cached_stock_analysis,
)
from app.services.stock_analysis_service import (
    StockAnalysisResult,
    stock_analysis_service,
)
from app.services.tw_insights_service import get_tw_stock_insights
from app.services.us_insights_service import get_us_stock_insights
from app.services.user_ai_provider_service import resolve_user_ai_provider


router = APIRouter(prefix="/stocks", tags=["history"])


@router.get(
    "/{stock_id}/history",
    response_model=HistoryEnvelope,
    responses={
        404: {"description": "Stock not found"},
        502: {"description": "Official upstream temporarily unavailable"},
    },
)
def get_history(
    stock_id: str,
    range_name: str = Query(default="1D", alias="range"),
) -> HistoryEnvelope:
    try:
        data = quote_service.get_history(stock_id, range_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    stock, candles = data
    layout_raw = build_chart_layout(candles)
    chart_layout = ChartLayoutResponse(**layout_raw) if layout_raw else None
    return HistoryEnvelope(
        stock=_stock_schema(stock),
        range=range_name,
        candles=[_candle_schema(candle) for candle in candles],
        chartLayout=chart_layout,
    )


@router.get("/{stock_id}/key-statistics")
def get_stock_key_statistics(stock_id: str) -> dict[str, object]:
    data = get_key_statistics(stock_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return data


@router.get("/{stock_id}/analysis", response_model=StockAnalysisResponse)
def get_stock_analysis(
    stock_id: str,
    provider: str | None = Query(default=None),
    refresh: bool = Query(default=False),
    user_id: str = Depends(get_effective_user_id),
) -> StockAnalysisResponse:
    provider_override: AIProvider | None = None
    try:
        provider_override = resolve_user_ai_provider(user_id, provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    bucket = _stock_analysis_cache_bucket(provider_override)

    try:
        if refresh:
            if provider_override is None:
                result = stock_analysis_service.analyze_stock(stock_id, run_ai=True)
            else:
                result = stock_analysis_service.analyze_stock(
                    stock_id,
                    provider_override=provider_override,
                    run_ai=True,
                )
            if result.ai_analysis:
                now = datetime.now(timezone.utc)
                set_cached_stock_analysis(
                    user_id,
                    stock_id,
                    bucket,
                    analyzed_at=now,
                    ai_provider=result.ai_provider,
                    ai_model=result.ai_model,
                    ai_analysis=result.ai_analysis,
                    citations=result.citations,
                    search_results=result.search_results,
                )
                result = replace(result, analyzed_at=now)
        else:
            if provider_override is None:
                result = stock_analysis_service.analyze_stock(stock_id, run_ai=False)
            else:
                result = stock_analysis_service.analyze_stock(
                    stock_id,
                    provider_override=provider_override,
                    run_ai=False,
                )
            cached = get_cached_stock_analysis(user_id, stock_id, bucket)
            if cached is not None:
                result = _merge_cached_ai_into_result(result, cached)
    except ValueError as exc:
        if str(exc) == "Stock not found":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _stock_analysis_schema(result)


@router.get("/{stock_id}/news")
def get_stock_news(stock_id: str) -> list[dict[str, str]]:
    stock = quote_service.get_stock(stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return [
        {
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "publishedAt": item.published_at,
        }
        for item in news_service.get_recent_news(stock)
    ]


@router.get("/{stock_id}/tw-insights", response_model=TWStockInsightsResponse)
def get_tw_insights(stock_id: str) -> TWStockInsightsResponse:
    stock = quote_service.get_stock(stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    if stock.market != "TW":
        raise HTTPException(
            status_code=404,
            detail="Taiwan insights are only available for TW stocks",
        )
    return TWStockInsightsResponse(**get_tw_stock_insights(stock))


@router.get("/{stock_id}/us-insights", response_model=USStockInsightsResponse)
def get_us_insights(stock_id: str) -> USStockInsightsResponse:
    stock = quote_service.get_stock(stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    if stock.market != "US":
        raise HTTPException(
            status_code=404,
            detail="U.S. insights are only available for US stocks",
        )
    return USStockInsightsResponse(**get_us_stock_insights(stock))


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


def _candle_schema(candle: CandleRecord) -> CandleResponse:
    return CandleResponse(
        time=candle.time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def _stock_analysis_cache_bucket(provider_override: AIProvider | None) -> str:
    if provider_override is not None:
        name = getattr(provider_override, "provider_name", "unknown")
        model = getattr(provider_override, "model_name", None) or ""
        return f"{name}:{model}"
    ap = (settings.ai_provider or "none").strip().lower()
    am = (settings.ai_model or "none").strip().lower()
    return f"env:{ap}:{am}"


def _merge_cached_ai_into_result(
    base: StockAnalysisResult,
    cached: CachedStockAnalysisPayload,
) -> StockAnalysisResult:
    return replace(
        base,
        ai_provider=cached.ai_provider,
        ai_model=cached.ai_model,
        ai_analysis=cached.ai_analysis,
        citations=cached.citations,
        search_results=cached.search_results,
        analyzed_at=cached.analyzed_at,
    )


def _stock_analysis_analyzed_at_iso(result: StockAnalysisResult) -> str | None:
    value = result.analyzed_at
    if value is None:
        return None
    dt = value.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _stock_analysis_schema(result: StockAnalysisResult) -> StockAnalysisResponse:
    return StockAnalysisResponse(
        stockId=result.stock_id,
        ticker=result.ticker,
        computedData=ComputedStockAnalysisResponse(
            ticker=result.computed_data.ticker,
            companyName=result.computed_data.company_name,
            currency=result.computed_data.currency,
            sector=result.computed_data.sector,
            industry=result.computed_data.industry,
            marketCap=result.computed_data.market_cap,
            trailingPe=result.computed_data.trailing_pe,
            forwardPe=result.computed_data.forward_pe,
            currentPrice=result.computed_data.current_price,
            return1mPercent=result.computed_data.return_1m_percent,
            return3mPercent=result.computed_data.return_3m_percent,
            return6mPercent=result.computed_data.return_6m_percent,
            return1yPercent=result.computed_data.return_1y_percent,
            sma50=result.computed_data.sma_50,
            sma200=result.computed_data.sma_200,
            priceVsSma50Percent=result.computed_data.price_vs_sma_50_percent,
            priceVsSma200Percent=result.computed_data.price_vs_sma_200_percent,
            rsi14=result.computed_data.rsi_14,
            volatility30dAnnualized=result.computed_data.volatility_30d_annualized,
            maxDrawdown1yPercent=result.computed_data.max_drawdown_1y_percent,
            averageVolume30d=result.computed_data.average_volume_30d,
        ),
        aiProvider=result.ai_provider,
        aiModel=result.ai_model,
        aiAnalysis=result.ai_analysis,
        analyzedAt=_stock_analysis_analyzed_at_iso(result),
        citations=[
            StockAnalysisCitationResponse(
                url=item.url,
                title=item.title,
                source=item.source,
            )
            for item in result.citations
        ],
        searchResults=[
            StockAnalysisSearchResultResponse(
                title=item.title,
                url=item.url,
                source=item.source,
            )
            for item in result.search_results
        ],
        warnings=list(result.warnings),
        errors=list(result.errors),
    )
