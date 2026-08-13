from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import RankingSnapshot, RankingSnapshotItem
from app.main import app
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services import rankings_service as rankings_service_module
from app.services import rankings_snapshot_service as snapshot_service_module
from app.services import ranking_data_providers as ranking_data_providers_module
from app.services.quote_service import quote_service
from app.services import rankings_external_source as rankings_external_source_module
from app.services.ranking_data_providers import RankingProviderRegistry, RankingQuery
from app.services.rankings_snapshot_service import rankings_snapshot_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


class FakeProvider:
    def __init__(
        self,
        *,
        bucket_start: datetime | None,
        response: RankingResponse | None = None,
    ) -> None:
        self._bucket_start = bucket_start
        self._response = response
        self.fetch_calls = 0

    def current_bucket_start(self, query: RankingQuery, now: datetime) -> datetime | None:  # noqa: ARG002
        return self._bucket_start

    def fetch(self, query: RankingQuery, *, limit: int, now: datetime) -> RankingResponse:  # noqa: ARG002
        self.fetch_calls += 1
        if self._response is None:
            raise AssertionError("FakeProvider.fetch was called without a response")
        return self._response.model_copy(update={"items": self._response.items[:limit]})


class FakeRegistry(RankingProviderRegistry):
    def __init__(self, providers: dict[tuple[str, str, str | None], FakeProvider]) -> None:
        self._providers = providers

    def get_provider(self, query: RankingQuery) -> FakeProvider:
        provider = self._providers.get((query.market, query.ranking_type.value, query.tw_board))
        if provider is None:
            raise AssertionError(
                f"Missing fake provider for {(query.market, query.ranking_type.value, query.tw_board)}"
            )
        return provider


def _sample_stock() -> StockResponse:
    return StockResponse(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        nameEn="Apple Inc.",
        nameZhTw=None,
    )


def _sample_response() -> RankingResponse:
    return RankingResponse(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        items=[
            RankingItem(
                rank=1,
                stock=_sample_stock(),
                lastPrice=210.5,
                changePercent=1.2,
                volume=1_000_000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-07-03T09:35:00-04:00",
            ),
            RankingItem(
                rank=2,
                stock=StockResponse(
                    id="US:MSFT",
                    market="US",
                    symbol="MSFT",
                    name="Microsoft Corporation",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                    nameEn="Microsoft Corporation",
                    nameZhTw=None,
                ),
                lastPrice=430.0,
                changePercent=0.8,
                volume=900_000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-07-03T09:35:00-04:00",
            ),
        ],
        timestamp="2026-07-03T13:35:00+00:00",
        source="Quote rankings from the app's free best-effort quote providers.",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage="Free unofficial quote source; data may be delayed or unavailable.",
        unsupportedReason=None,
        usRankingsScope="quote",
    )


def test_rankings_endpoint_captures_missing_open_bucket_and_persists_snapshot(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
        response=_sample_response(),
    )
    monkeypatch.setattr(
        rankings_snapshot_service,
        "_provider_registry",
        FakeRegistry({("US", "volume", None): provider}),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "US", "type": "volume", "limit": 1},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceType"] == "free-unofficial"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["stock"]["symbol"] == "AAPL"
    db_session.expire_all()
    snapshots = list(db_session.execute(select(RankingSnapshot)).scalars())
    snapshot_items = list(db_session.execute(select(RankingSnapshotItem)).scalars())
    assert len(snapshots) == 1
    assert len(snapshot_items) == 2
    assert provider.fetch_calls == 1


def test_rankings_endpoint_returns_latest_cached_snapshot_when_market_is_closed(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    snapshot = RankingSnapshot(
        market="US",
        tw_board="",
        ranking_type="volume",
        bucket_start=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
        source="Quote rankings from the app's free best-effort quote providers.",
        source_type="free-unofficial",
        source_freshness="Market open; rankings use the latest quote snapshot.",
        is_market_open=True,
        warning_message="Free unofficial quote source; data may be delayed or unavailable.",
        unsupported_reason=None,
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        RankingSnapshotItem(
            snapshot_id=snapshot.id,
            rank=1,
            stock_id="US:AAPL",
            symbol="AAPL",
            name="Apple Inc.",
            last_price=210.5,
            change_percent=1.2,
            volume=1_000_000,
            volume_delta=None,
            volume_delta_percent=None,
            net_buy=None,
            net_sell=None,
            data_time="2026-07-03T09:35:00-04:00",
            raw_payload_json=_sample_response().items[0].model_dump(mode="json"),
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        rankings_snapshot_service,
        "_provider_registry",
        FakeRegistry({("US", "volume", None): FakeProvider(bucket_start=None, response=_sample_response())}),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "US", "type": "volume", "limit": 5},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceType"] == "cache"
    assert payload["dataFreshnessLabel"] == "Market is closed. Showing latest available free snapshot."
    assert payload["items"][0]["stock"]["symbol"] == "AAPL"


def test_us_institutional_rankings_remain_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)

    with TestClient(app) as client:
        net_buy = client.get("/api/rankings", params={"market": "US", "type": "net_buy", "limit": 5})
        net_sell = client.get("/api/rankings", params={"market": "US", "type": "net_sell", "limit": 5})

    assert net_buy.status_code == 200
    assert net_buy.json()["sourceType"] == "unsupported"
    assert net_buy.json()["unsupportedReason"] == "US institutional net-buy/net-sell is not available in this free-only app."
    assert net_sell.status_code == 200
    assert net_sell.json()["sourceType"] == "unsupported"
    assert net_sell.json()["unsupportedReason"] == "US institutional net-buy/net-sell is not available in this free-only app."


def test_rankings_capture_endpoint_requires_token_and_triggers_scheduler(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr("app.core.config.settings.rankings_capture_token", "test-token")
    monkeypatch.setattr(
        rankings_snapshot_service,
        "capture_scheduled_snapshots",
        lambda **_: 2,
    )

    with TestClient(app) as client:
        forbidden = client.post("/api/rankings/snapshots/capture")
        assert forbidden.status_code == 403

        response = client.post(
            "/api/rankings/snapshots/capture",
            headers={"X-Rankings-Capture-Token": "test-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"captured": 2}


def test_rankings_endpoint_validates_market(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)

    with TestClient(app) as client:
        response = client.get("/api/rankings", params={"market": "JP", "type": "volume", "limit": 5})

    assert response.status_code == 422


def test_rankings_endpoint_validates_ranking_type(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)

    with TestClient(app) as client:
        response = client.get("/api/rankings", params={"market": "TW", "type": "turnover", "limit": 5})

    assert response.status_code == 422


def _tw_sample_response() -> RankingResponse:
    return RankingResponse(
        market="TW",
        rankingType=RankingType.volume,
        twBoard="TWSE",
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="TW:2330",
                    market="TW",
                    symbol="2330",
                    name="台積電",
                    exchange="TWSE",
                    currency="TWD",
                    timezone="Asia/Taipei",
                    nameEn="TSMC",
                    nameZhTw="台積電",
                ),
                lastPrice=950.0,
                changePercent=1.2,
                volume=123456,
                volumeDeltaPercent=5.0,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-05-11T09:30:00+08:00",
            )
        ],
        timestamp="2026-05-11T01:30:00Z",
        source="Quote rankings from the app's free best-effort quote providers.",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage="Free unofficial quote source; data may be delayed or unavailable.",
        unsupportedReason=None,
        usRankingsScope=None,
    )


def test_rankings_endpoint_returns_response_shape(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr(
        rankings_snapshot_service,
        "get_rankings",
        lambda market, ranking_type, limit, now=None, tw_board=None: _tw_sample_response(),  # noqa: ARG005
    )

    with TestClient(app) as client:
        response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 1})

    assert response.status_code == 200
    assert response.json()["market"] == "TW"
    assert response.json()["items"][0]["stock"]["symbol"] == "2330"
    assert response.json()["sourceType"] == "free-unofficial"


def test_rankings_endpoint_passes_limit(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    captured: dict[str, object] = {}

    def fake_get_rankings(market, ranking_type, limit, now=None, tw_board=None):  # noqa: ARG001
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        captured["tw_board"] = tw_board
        return _tw_sample_response()

    monkeypatch.setattr(rankings_snapshot_service, "get_rankings", fake_get_rankings)

    with TestClient(app) as client:
        response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 7})

    assert response.status_code == 200
    assert captured == {
        "market": "TW",
        "ranking_type": RankingType.volume,
        "limit": 7,
        "tw_board": None,
    }


def test_rankings_endpoint_returns_twse_valuation_low_pe_via_service(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", None)
    twse_stock = stock_record(
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
    twse_quote = quote_record(
        "TW:2330",
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=942.0,
        high=960.0,
        low=948.0,
        volume=310_000,
    )

    class _VolumeUniverseSource:
        def fetch(self, market, ranking_type, limit, now=None, tw_board=None):  # noqa: ARG002
            assert market == "TW"
            assert ranking_type == RankingType.volume
            assert limit == 100
            assert tw_board == "TWSE"
            return [(twse_stock, twse_quote)]

    volume_source = _VolumeUniverseSource()
    monkeypatch.setattr(
        rankings_external_source_module,
        "rankings_external_source",
        volume_source,
    )
    monkeypatch.setattr(
        ranking_data_providers_module.ranking_provider_registry._quote_provider._service,
        "_external_source",
        volume_source,
    )
    monkeypatch.setattr(
        rankings_service_module,
        "get_valuation_snapshots",
        lambda envelopes: {
            stock.id: {
                "peRatio": 18.5 if stock.id == "TW:2330" else None,
                "peRatioType": "official_daily" if stock.id == "TW:2330" else None,
                "peRatioSource": (
                    "TWSE official daily P/E" if stock.id == "TW:2330" else None
                ),
                "valuationAsOf": "2026-07-03" if stock.id == "TW:2330" else None,
                "valuationWarning": None,
            }
            for stock, _ in envelopes
        },
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "TW", "type": "valuation_low_pe", "limit": 3, "twBoard": "TWSE"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rankingType"] == "valuation_low_pe"
    assert payload["twBoard"] == "TWSE"
    assert payload["items"][0]["stock"]["symbol"] == "2330"
    assert payload["items"][0]["peRatio"] == 18.5
    assert payload["items"][0]["peRatioSource"] == "TWSE official daily P/E"
    assert payload["items"][0]["valuationAsOf"] == "2026-07-03"
    assert all(item["stock"]["exchange"] == "TWSE" for item in payload["items"])


def _install_tpex_institutional_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    net_buy_sell_shares: int,
) -> None:
    tpex_stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="GlobalWafers",
        name_zh_tw="環球晶",
    )
    tpex_quote = quote_record(
        "TW:6488",
        last_price=480.0,
        change=1.5,
        change_percent=1.5,
        previous_close=478.5,
        timestamp="2026-05-12T10:00:00+08:00",
        is_market_open=True,
        volume=654321,
    )

    def get_stock(stock_id: str):
        return tpex_stock if stock_id == "TW:6488" else None

    def get_quotes(stock_ids: list[str]):
        if "TW:6488" in stock_ids:
            return [(tpex_stock, tpex_quote)]
        return []

    monkeypatch.setattr(quote_service, "get_stock", get_stock)
    monkeypatch.setattr(quote_service, "get_quotes", get_quotes)
    monkeypatch.setattr(
        rankings_service_module.RankingsService,
        "_fetch_tpex_institutional_snapshot",
        lambda self, now, tw_board="TWSE": (  # noqa: ARG002
            date(2026, 5, 11),
            {"TW:6488": net_buy_sell_shares},
        ),
    )


@pytest.mark.parametrize(
    ("ranking_type", "net_buy_sell_shares"),
    [
        ("net_buy", 500000),
        ("net_sell", -500000),
    ],
)
def test_rankings_endpoint_returns_tpex_institutional_response_shape(
    monkeypatch,
    ranking_type: str,
    net_buy_sell_shares: int,
) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", None)
    _install_tpex_institutional_fixtures(
        monkeypatch,
        net_buy_sell_shares=net_buy_sell_shares,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "TW", "type": ranking_type, "limit": 50, "twBoard": "TPEx"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "TW"
    assert payload["rankingType"] == ranking_type
    assert payload["twBoard"] == "TPEx"
    assert payload["items"][0]["stock"]["symbol"] == "6488"
    assert payload["items"][0]["stock"]["exchange"] == "TPEx"
    assert payload["items"][0]["netBuySellShares"] == net_buy_sell_shares
    assert payload["items"][0]["lastPrice"] == 480.0
    assert payload["source"] == "TPEx institutional daily data"
    assert payload["sourceType"] == "free-public"
    assert "TPEx daily institutional ranking" in payload["dataFreshnessLabel"]
    assert payload["warningMessage"] == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert payload["unsupportedReason"] is None


@pytest.mark.parametrize("ranking_type", ["net_buy", "net_sell"])
def test_rankings_endpoint_returns_tpex_institutional_unavailable_shape(
    monkeypatch,
    ranking_type: str,
) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", None)
    monkeypatch.setattr(
        rankings_service_module.RankingsService,
        "_fetch_tpex_institutional_snapshot",
        lambda self, now, tw_board="TWSE": None,  # noqa: ARG002
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "TW", "type": ranking_type, "limit": 50, "twBoard": "TPEx"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["source"] == "TPEx institutional daily data"
    assert payload["sourceType"] == "unsupported"
    assert (
        payload["dataFreshnessLabel"]
        == "TPEx institutional daily ranking is unavailable from the free public source right now."
    )
    assert payload["warningMessage"] == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert (
        payload["unsupportedReason"]
        == "TPEx institutional daily data is temporarily unavailable from the free public source."
    )


@pytest.mark.parametrize("ranking_type", ["net_buy", "net_sell"])
def test_tw_institutional_rankings_include_daily_warning(
    monkeypatch,
    ranking_type: str,
) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_snapshot_enabled", False)
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", None)
    _install_tpex_institutional_fixtures(monkeypatch, net_buy_sell_shares=250_000)

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={"market": "TW", "type": ranking_type, "limit": 5, "twBoard": "TPEx"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warningMessage"] == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert payload["sourceType"] == "free-public"


def test_tw_institutional_rankings_use_applicable_snapshot_before_live_fetch(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.rankings_snapshot_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            frozen = datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc)
            if tz is None:
                return frozen.replace(tzinfo=None)
            return frozen.astimezone(tz)

    monkeypatch.setattr(snapshot_service_module, "datetime", _FrozenDateTime)
    bucket_start = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    snapshot = RankingSnapshot(
        market="TW",
        tw_board="TWSE",
        ranking_type="net_buy",
        bucket_start=bucket_start,
        source="TWSE T86 daily institutional data",
        source_type="free-public",
        source_freshness=(
            "Showing the latest saved TW institutional daily snapshot."
        ),
        is_market_open=False,
        warning_message="台股三大法人買賣超為日資料，非盤中即時資料。",
        unsupported_reason=None,
        capture_scope="on_demand",
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(
        RankingSnapshotItem(
            snapshot_id=snapshot.id,
            rank=1,
            stock_id="TW:2330",
            symbol="2330",
            name="TSMC",
            last_price=101.0,
            change_percent=None,
            volume=None,
            volume_delta=None,
            volume_delta_percent=None,
            net_buy=500_000,
            net_sell=None,
            data_time="2026-07-02T18:00:00+08:00",
            raw_payload_json={
                "stock": {
                    "id": "TW:2330",
                    "market": "TW",
                    "symbol": "2330",
                    "name": "TSMC",
                    "exchange": "TWSE",
                    "currency": "TWD",
                    "timezone": "Asia/Taipei",
                    "nameEn": "TSMC",
                    "nameZhTw": None,
                }
            },
        )
    )
    db_session.commit()
    provider = FakeProvider(bucket_start=bucket_start, response=None)
    monkeypatch.setattr(
        rankings_snapshot_service,
        "_provider_registry",
        FakeRegistry({("TW", "net_buy", "TWSE"): provider}),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/rankings",
            params={
                "market": "TW",
                "type": "net_buy",
                "limit": 5,
                "twBoard": "TWSE",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceType"] == "cache"
    assert payload["items"][0]["stock"]["symbol"] == "2330"
    assert provider.fetch_calls == 0
