from app.services.providers import tw_provider, us_provider


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
