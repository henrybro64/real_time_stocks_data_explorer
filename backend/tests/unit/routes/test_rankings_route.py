from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routes import rankings as rankings_route
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
    RankingsRefreshRequest,
)
from app.schemas.stock import StockResponse
from app.services.rankings_snapshot_service import RankingsRefreshError


def _sample_response() -> RankingResponse:
    return RankingResponse(
        market="TW",
        rankingType=RankingType.volume,
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
        timestamp=datetime(2026, 5, 11, 1, 30, tzinfo=timezone.utc).isoformat(),
        source="Quote rankings from the app's free best-effort quote providers.",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage="Free unofficial quote source; data may be delayed or unavailable.",
        unsupportedReason=None,
        usRankingsScope=None,
        twBoard="TWSE",
    )


def test_get_rankings_route_delegates_to_snapshot_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_rankings(market, ranking_type, limit, tw_board=None, universe=None):
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        captured["tw_board"] = tw_board
        captured["universe"] = universe
        return _sample_response()

    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "get_rankings",
        fake_get_rankings,
    )

    response = rankings_route.get_rankings(
        market="TW",
        ranking_type=RankingType.volume,
        limit=7,
        tw_board="TWSE",
        universe=None,
    )

    assert captured == {
        "market": "TW",
        "ranking_type": RankingType.volume,
        "limit": 7,
        "tw_board": "TWSE",
        "universe": "TWSE",
    }
    assert response.rankingType == RankingType.volume
    assert response.items[0].stock.symbol == "2330"


def test_get_rankings_route_accepts_all_board_for_price_limits(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_rankings(market, ranking_type, limit, tw_board=None, universe=None):
        captured["tw_board"] = tw_board
        captured["universe"] = universe
        captured["ranking_type"] = ranking_type
        return _sample_response()

    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "get_rankings",
        fake_get_rankings,
    )

    rankings_route.get_rankings(
        market="TW",
        ranking_type=RankingType.limit_down,
        limit=50,
        tw_board="all",
        universe=None,
    )

    assert captured == {
        "tw_board": "all",
        "universe": None,
        "ranking_type": RankingType.limit_down,
    }


def test_get_rankings_route_rejects_all_board_for_other_types() -> None:
    with pytest.raises(HTTPException) as exc_info:
        rankings_route.get_rankings(
            market="TW",
            ranking_type=RankingType.volume,
            limit=50,
            tw_board="all",
            universe=None,
        )
    assert exc_info.value.status_code == 422


def test_refresh_rankings_route_passes_all_board(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_refresh_rankings(market, ranking_type, limit, tw_board=None, universe=None):
        captured["tw_board"] = tw_board
        captured["universe"] = universe
        captured["ranking_type"] = ranking_type
        return _sample_response()

    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "refresh_rankings",
        fake_refresh_rankings,
    )

    rankings_route.refresh_rankings(
        RankingsRefreshRequest(
            market="TW",
            rankingType=RankingType.limit_up,
            universe="all",
            limit=50,
        )
    )

    assert captured == {
        "tw_board": "all",
        "universe": None,
        "ranking_type": RankingType.limit_up,
    }


def test_capture_rankings_snapshots_route_requires_configured_token(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.rankings_capture_token", "")

    try:
        rankings_route.capture_rankings_snapshots("any-token")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "Rankings snapshot capture endpoint is not configured"
    else:
        raise AssertionError("Expected rankings capture route to raise HTTPException")


def test_refresh_rankings_route_delegates_to_snapshot_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_refresh_rankings(market, ranking_type, limit, tw_board=None, universe=None):
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        captured["tw_board"] = tw_board
        captured["universe"] = universe
        return _sample_response()

    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "refresh_rankings",
        fake_refresh_rankings,
    )

    response = rankings_route.refresh_rankings(
        RankingsRefreshRequest(
            market="US",
            rankingType=RankingType.volume,
            universe="NASDAQ100",
            limit=20,
        )
    )

    assert captured == {
        "market": "US",
        "ranking_type": RankingType.volume,
        "limit": 20,
        "tw_board": None,
        "universe": "NASDAQ100",
    }
    assert response.items[0].stock.symbol == "2330"


def test_refresh_rankings_route_maps_failure_to_502(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "refresh_rankings",
        lambda **_kwargs: (_ for _ in ()).throw(RankingsRefreshError("upstream down")),
    )

    try:
        rankings_route.refresh_rankings(
            RankingsRefreshRequest(
                market="TW",
                rankingType=RankingType.volume,
                universe="TWSE",
                limit=50,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert exc.detail == "upstream down"
    else:
        raise AssertionError("Expected refresh route to raise HTTPException")


def test_refresh_rankings_route_maps_cooldown_to_429(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings_route.rankings_snapshot_service,
        "refresh_rankings",
        lambda **_kwargs: (_ for _ in ()).throw(
            RankingsRefreshError(
                "Rankings refresh is cooling down. Retry after a few seconds.",
                status_code=429,
                retry_after_seconds=12,
            )
        ),
    )

    try:
        rankings_route.refresh_rankings(
            RankingsRefreshRequest(
                market="US",
                rankingType=RankingType.volume,
                universe="DJIA",
                limit=50,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 429
        assert "cooling down" in str(exc.detail)
        assert exc.headers["Retry-After"] == "12"
    else:
        raise AssertionError("Expected refresh route to raise HTTPException")


def test_get_rankings_rejects_universe_that_does_not_match_market() -> None:
    try:
        rankings_route.get_rankings(
            market="TW",
            ranking_type=RankingType.volume,
            limit=5,
            tw_board="TWSE",
            universe="DJIA",
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "TWSE or TPEx" in str(exc.detail)
    else:
        raise AssertionError("Expected mismatched universe to raise HTTPException")


def test_rankings_history_rejects_live_only_price_limits() -> None:
    try:
        rankings_route.get_rankings_history(
            market="TW",
            ranking_type=RankingType.limit_up,
            trading_date=date(2026, 9, 25),
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == (
            "Taiwan limit_up and limit_down rankings are live-only and are not archived"
        )
    else:
        raise AssertionError("Expected live-only history to raise HTTPException")


def test_rankings_history_dates_rejects_live_only_price_limits() -> None:
    try:
        rankings_route.get_rankings_history_dates(
            market="TW",
            universe="TWSE",
            ranking_type=RankingType.limit_down,
        )
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "live-only" in str(exc.detail)
    else:
        raise AssertionError("Expected live-only history dates to raise HTTPException")

