from __future__ import annotations

from datetime import datetime, timezone

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
from app.services.ranking_data_providers import RankingProviderRegistry, RankingQuery
from app.services.rankings_snapshot_service import rankings_snapshot_service


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
