from app.services.quote_service import QuoteService
from tests.support import quote_record, stock_record


class StubUSProvider:
    market = "US"

    def __init__(self) -> None:
        self.quote_calls: list[str] = []
        self.live_quote_calls: list[str] = []
        self._stocks = {
            "US:AAPL": stock_record(
                "US:AAPL",
                market="US",
                symbol="AAPL",
                name="Apple Inc.",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Apple Inc.",
            ),
            "US:MSFT": stock_record(
                "US:MSFT",
                market="US",
                symbol="MSFT",
                name="Microsoft Corporation",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Microsoft Corporation",
            ),
        }

    def list_stocks(self):
        return list(self._stocks.values())

    def search(self, query: str):
        return []

    def get_stock(self, stock_id: str):
        return self._stocks.get(stock_id)

    def get_quote(self, stock_id: str):
        self.quote_calls.append(stock_id)
        raise AssertionError("get_quote should not be used for live-only US fetches")

    def get_live_quote(self, stock_id: str):
        self.live_quote_calls.append(stock_id)
        if stock_id == "US:MSFT":
            return quote_record(
                "US:MSFT",
                last_price=100.0,
                change=1.0,
                change_percent=1.0,
                previous_close=99.0,
                timestamp="2026-05-12T10:00:00-04:00",
                is_market_open=True,
                volume=300,
            )
        return None

    def get_history(self, stock_id: str, range_name: str):
        return []


def test_get_live_quotes_uses_live_us_provider_and_skips_missing_symbols() -> None:
    service = QuoteService()
    provider = StubUSProvider()
    service.providers = {"US": provider}

    envelopes = service.get_live_quotes(["US:AAPL", "US:MSFT"])

    assert [stock.symbol for stock, _quote in envelopes] == ["MSFT"]
    assert provider.live_quote_calls == ["US:AAPL", "US:MSFT"]
    assert provider.quote_calls == []
