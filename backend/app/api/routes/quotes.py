import re

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.core.config import settings
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.quote import QuoteEnvelope, QuoteFreshnessResponse, QuoteResponse, quote_to_response
from app.schemas.stock import StockResponse
from app.services.quote_service import QuoteBatchItem, quote_service


# US tickers, including class shares (BRK-B). Taiwan codes are 4–6 digits.
_QUOTE_ID_RE = re.compile(
    r"^(?:US:[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z][A-Z0-9]?)?|TW:\d{4,6})$"
)


def normalize_quote_ids(raw_ids: list[str]) -> list[str]:
    """Validate id shape before any catalog or upstream lookup (#272)."""
    normalized: list[str] = []
    for raw in raw_ids:
        token = raw.strip().upper()
        if not _QUOTE_ID_RE.fullmatch(token):
            raise ValueError(token)
        normalized.append(token)
    return normalized


router = APIRouter(
    prefix="/quotes",
    tags=["quotes"],
    dependencies=[Depends(get_effective_user_id)],
)


@router.get("", response_model=list[QuoteEnvelope])
def get_quotes(ids: str = Query(...)) -> list[QuoteEnvelope]:
    raw_ids = [item.strip() for item in ids.split(",") if item.strip()]
    try:
        stock_ids = normalize_quote_ids(raw_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid stock id") from exc
    max_ids = int(settings.quote_batch_ids_max)
    if len(stock_ids) > max_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Too many ids; maximum is {max_ids}",
        )
    return [
        _batch_item_schema(item)
        for item in quote_service.get_quotes_cache_first(stock_ids)
    ]


@router.get(
    "/{stock_id}",
    responses={
        404: {"description": "Stock not found"},
        502: {"description": "Quote provider unavailable"},
    },
)
def get_quote(stock_id: str) -> QuoteEnvelope:
    try:
        normalized = normalize_quote_ids([stock_id])[0]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid stock id") from exc
    try:
        item = quote_service.get_quote_detail(normalized)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Quote provider unavailable") from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    # Allow null quote with freshness when the refresher owns upstream (cold miss).
    return _batch_item_schema(item)


def _batch_item_schema(item: QuoteBatchItem) -> QuoteEnvelope:
    return QuoteEnvelope(
        stock=_stock_schema(item.stock),
        quote=_quote_schema(item.quote) if item.quote is not None else None,
        freshness=QuoteFreshnessResponse(
            cachedAt=item.cached_at,
            ageSeconds=item.age_seconds,
            stale=item.stale,
            source=item.source,
            refreshStatus=item.refresh_status,
        ),
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
