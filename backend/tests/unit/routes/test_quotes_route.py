import pytest

from app.api.routes.portfolio import _quote_schema as portfolio_quote_schema
from app.api.routes.quotes import _batch_item_schema, _quote_schema, _stock_schema, get_quote, get_quotes
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.quote_service import QuoteBatchItem, quote_service


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
        "priceLimitStatus": "unknown",
        "referencePrice": None,
        "limitUpPrice": None,
        "limitDownPrice": None,
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


def test_quote_schema_publishes_taiwan_limit_fields() -> None:
    quote = QuoteRecord(
        id="TW:2330",
        last_price=110.0,
        change=10.0,
        change_percent=10.0,
        previous_close=100.0,
        timestamp="2026-09-26T13:30:00+08:00",
        is_market_open=True,
        price_limit_status="limit_up",
        reference_price=100.0,
        limit_up_price=110.0,
        limit_down_price=90.0,
    )

    payload = _quote_schema(quote).model_dump()

    assert payload["priceLimitStatus"] == "limit_up"
    assert payload["referencePrice"] == pytest.approx(100.0)
    assert payload["limitUpPrice"] == pytest.approx(110.0)
    assert payload["limitDownPrice"] == pytest.approx(90.0)
    assert portfolio_quote_schema(quote).model_dump() == payload


def test_single_and_batch_quote_endpoints_share_price_limit_fields(monkeypatch) -> None:
    stock = StockRecord(
        id="TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    quote = QuoteRecord(
        id="TW:6488",
        last_price=90.0,
        change=-10.0,
        change_percent=-10.0,
        previous_close=100.0,
        timestamp="2026-09-26T13:30:00+08:00",
        is_market_open=True,
        price_limit_status="limit_down",
        reference_price=100.0,
        limit_up_price=110.0,
        limit_down_price=90.0,
    )
    item = QuoteBatchItem(
        stock=stock,
        quote=quote,
        cached_at="2026-09-26T05:30:00Z",
        age_seconds=2.0,
        stale=False,
        source="redis",
        refresh_status="idle",
    )
    monkeypatch.setattr(quote_service, "get_quotes_cache_first", lambda ids: [item])
    monkeypatch.setattr(quote_service, "get_quote_detail", lambda stock_id: item)

    batch = get_quotes("TW:6488")
    single = get_quote("TW:6488")

    assert batch[0].quote is not None
    assert single.quote is not None
    assert batch[0].quote.model_dump() == single.quote.model_dump()
    assert single.quote.priceLimitStatus == "limit_down"
    assert single.quote.limitDownPrice == pytest.approx(90.0)
