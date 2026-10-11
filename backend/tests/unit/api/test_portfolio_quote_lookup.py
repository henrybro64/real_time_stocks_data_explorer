from types import SimpleNamespace

from app.api.routes.portfolio import _quote_for_stored_id


def test_stored_lowercase_id_finds_the_canonical_quote(monkeypatch) -> None:
    quote = SimpleNamespace(last_price=0.1235)
    monkeypatch.setattr(
        "app.api.routes.portfolio.quote_service.get_stock",
        lambda stock_id: SimpleNamespace(id="US:AAPL"),
    )

    found = _quote_for_stored_id({"US:AAPL": quote}, "US:aapl")

    assert found is quote
