from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from zoneinfo import ZoneInfo

from app.api.deps import get_effective_user_id
from app.db.models import PortfolioAsset, PortfolioPosition
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.portfolio import (
    PortfolioPositionCreate,
    PortfolioAssetCreate,
    PortfolioAssetResponse,
    PortfolioAssetUpdate,
    PortfolioPositionResponse,
    PortfolioPositionUpdate,
    PortfolioSummaryPosition,
    PortfolioSummaryResponse,
)
from app.schemas.quote import QuoteResponse
from app.schemas.stock import StockResponse
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.portfolio_service import portfolio_service
from app.services.quote_service import quote_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

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

    total_usd = 0.0
    total_twd = 0.0
    rows: list[PortfolioSummaryPosition] = []
    skipped_stock_ids: list[str] = []

    for position in positions:
        stock = quote_service.get_stock(position.stock_id)
        if stock is None:
            skipped_stock_ids.append(position.stock_id)
            continue
        quote = quote_service.get_quote(position.stock_id)
        if quote is None:
            quote = _synthetic_quote_for_holdings(stock, position)
        market_value = position.quantity * quote.last_price
        if stock.currency == "USD":
            total_usd += market_value
        elif stock.currency == "TWD":
            total_twd += market_value

        rows.append(
            PortfolioSummaryPosition(
                stockId=position.stock_id,
                symbol=stock.symbol,
                currency=stock.currency,
                quantity=position.quantity,
                lastPrice=quote.last_price,
                marketValue=round(market_value, 2),
                timestamp=quote.timestamp,
            )
        )

    combined_usd = total_usd + (
        total_twd / usd_twd_rate if usd_twd_rate > 0 else 0.0
    )
    combined_twd = total_twd + (
        total_usd * usd_twd_rate if usd_twd_rate > 0 else 0.0
    )

    return PortfolioSummaryResponse(
        totalUsd=round(total_usd, 2),
        totalTwd=round(total_twd, 2),
        combinedUsd=round(combined_usd, 2),
        combinedTwd=round(combined_twd, 2),
        usdTwdRate=usd_twd_rate,
        fxTimestamp=str(fx_snapshot["timestamp"]),
        positions=rows,
        skippedStockIds=skipped_stock_ids,
    )


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


def _synthetic_quote_for_holdings(
    stock: StockRecord, position: PortfolioPosition
) -> QuoteRecord:
    tz = ZoneInfo(stock.timezone)
    now = datetime.now(tz)
    price = float(position.average_cost)
    return QuoteRecord(
        id=stock.id,
        last_price=price,
        change=0.0,
        change_percent=0.0,
        previous_close=price,
        timestamp=now.isoformat(),
        is_market_open=False,
        open=price,
        high=price,
        low=price,
        volume=None,
    )


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
    if quote is None:
        quote = _synthetic_quote_for_holdings(stock, position)
    market_value, unrealized_pl, unrealized_pl_percent = (
        portfolio_service.build_metrics(position, quote)
    )
    return PortfolioPositionResponse(
        id=position.id,
        stockId=position.stock_id,
        quantity=position.quantity,
        averageCost=position.average_cost,
        currency=position.currency,
        notes=position.notes,
        stock=_stock_schema(stock),
        quote=_quote_schema(quote),
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
    return QuoteResponse(
        id=quote.id,
        lastPrice=quote.last_price,
        change=quote.change,
        changePercent=quote.change_percent,
        previousClose=quote.previous_close,
        open=quote.open,
        high=quote.high,
        low=quote.low,
        volume=quote.volume,
        timestamp=quote.timestamp,
        isMarketOpen=quote.is_market_open,
    )


def _to_asset_response(asset: PortfolioAsset) -> PortfolioAssetResponse:
    return PortfolioAssetResponse(
        id=asset.id,
        assetType=asset.asset_type,
        name=asset.name,
        currency=asset.currency,
        value=asset.value,
        notes=asset.notes,
    )
