from app.api.routes.quotes import _batch_item_schema, _quote_schema, _stock_schema
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.quote_service import QuoteBatchItem


def test_stock_schema_uses_public_field_names() -> None:
    stock = StockRecord(
        id="TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="TSMC",
        name_zh_tw="台積電",
    )

    payload = _stock_schema(stock).model_dump()

    assert payload == {
        "id": "TW:2330",
        "market": "TW",
        "symbol": "2330",
        "name": "台積電",
        "exchange": "TWSE",
        "currency": "TWD",
        "timezone": "Asia/Taipei",
        "nameEn": "TSMC",
        "nameZhTw": "台積電",
    }


def test_quote_schema_maps_quote_record_fields() -> None:
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=187.23,
        change=1.5,
        change_percent=0.81,
        previous_close=185.73,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        open=186.1,
        high=188.0,
        low=185.2,
        volume=1_250_000,
    )

    payload = _quote_schema(quote).model_dump()

    assert payload == {
        "id": "US:AAPL",
        "lastPrice": 187.23,
        "change": 1.5,
        "changePercent": 0.81,
        "previousClose": 185.73,
        "timestamp": "2026-04-01T09:30:00-04:00",
        "isMarketOpen": True,
        "open": 186.1,
        "high": 188.0,
        "low": 185.2,
        "volume": 1_250_000,
    }


def test_batch_item_schema_includes_freshness_and_nullable_quote() -> None:
    stock = StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
        name_zh_tw=None,
    )
    item = QuoteBatchItem(
        stock=stock,
        quote=None,
        cached_at=None,
        age_seconds=None,
        stale=True,
        source="none",
        refresh_status="queued",
    )

    payload = _batch_item_schema(item).model_dump()

    assert payload["quote"] is None
    assert payload["freshness"] == {
        "cachedAt": None,
        "ageSeconds": None,
        "stale": True,
        "source": "none",
        "refreshStatus": "queued",
    }
