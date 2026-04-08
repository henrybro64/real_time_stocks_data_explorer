import pytest

from app.services.quote_service import quote_service
from tests.support import stock_record


pytestmark = pytest.mark.integration


def test_watchlist_add_remove_and_duplicate_add_remains_consistent(monkeypatch, client) -> None:
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock_record(
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
        if stock_id == "TW:2330"
        else None,
    )

    add_first = client.post("/api/watchlist/items", json={"id": "TW:2330"})
    add_second = client.post("/api/watchlist/items", json={"id": "TW:2330"})
    list_after_refresh = client.get("/api/watchlist")
    remove = client.delete("/api/watchlist/items/TW:2330")

    assert add_first.status_code == 200
    assert add_first.json() == {"ids": ["TW:2330"]}
    assert add_second.status_code == 200
    assert add_second.json() == {"ids": ["TW:2330"]}
    assert list_after_refresh.status_code == 200
    assert list_after_refresh.json() == {"ids": ["TW:2330"]}
    assert remove.status_code == 200
    assert remove.json() == {"ids": []}


def test_watchlist_rejects_unknown_stock_id(monkeypatch, client) -> None:
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: None)

    response = client.post("/api/watchlist/items", json={"id": "NOPE"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown stock id: NOPE"}
