from app.services.quote_service import QuoteService
from app.services.providers import tw_provider, us_provider
from tests.support import stock_record


class StubSearchProvider:
    def __init__(self, results):
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        return list(self.results)


def test_us_provider_search_returns_known_us_stock_without_network(monkeypatch) -> None:
    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        lambda: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    provider = us_provider.USProvider()
    matches = provider.search("AAPL")

    assert matches
    assert matches[0].id == "US:AAPL"
    assert matches[0].symbol == "AAPL"


def test_tw_provider_search_matches_symbol_and_chinese_name_without_network(monkeypatch) -> None:
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    monkeypatch.setattr(
        tw_provider,
        "_load_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    provider = tw_provider.TWProvider()

    symbol_matches = provider.search("2330")
    name_matches = provider.search("台積")

    assert any(item.id == "TW:2330" for item in symbol_matches)
    assert any(item.id == "TW:2330" for item in name_matches)


def test_tw_provider_get_quote_returns_none_when_live_sources_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_quote", lambda self, stock: None)
    monkeypatch.setattr(tw_provider.TWProvider, "_fetch_yahoo_tw_quote", lambda self, stock: None)

    provider = tw_provider.TWProvider()

    assert provider.get_quote("TW:2330") is None


def test_quote_service_routes_numeric_searches_to_tw_provider_only() -> None:
    service = QuoteService()
    tw_search = StubSearchProvider(
        [
            stock_record(
                "TW:2330",
                market="TW",
                symbol="2330",
                name="台積電",
                exchange="TWSE",
                currency="TWD",
                timezone="Asia/Taipei",
                name_en="TSMC",
                name_zh_tw="台積電",
            )
        ]
    )
    us_search = StubSearchProvider(
        [
            stock_record(
                "US:AAPL",
                market="US",
                symbol="AAPL",
                name="Apple Inc.",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Apple Inc.",
            )
        ]
    )
    service.providers = {"US": us_search, "TW": tw_search}

    matches = service.search("2330")

    assert [item.id for item in matches] == ["TW:2330"]
    assert tw_search.queries == ["2330"]
    assert us_search.queries == []
