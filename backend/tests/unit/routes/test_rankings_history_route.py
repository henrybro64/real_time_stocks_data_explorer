"""Unit tests for rankings history APIs (#168)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import pytest

from app.api.routes import rankings as rankings_route
from app.db.archive_models import ArchiveBase
from app.repositories.rankings_archive_repository import RankingsArchiveRepository
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.rankings_archive_service import (
    RankingsArchiveService,
    RankingsArchiveUnavailableError,
)


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    ArchiveBase.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _stock(symbol: str = "AAPL") -> StockResponse:
    return StockResponse(
        id=f"US:{symbol}",
        market="US",
        symbol=symbol,
        name=symbol,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def _response(
    *,
    market: str = "US",
    universe: str = "DJIA",
    ranking_type: RankingType = RankingType.volume,
    symbol: str = "AAPL",
) -> RankingResponse:
    tw_board = universe if market == "TW" else None
    return RankingResponse(
        market=market,
        rankingType=ranking_type,
        twBoard=tw_board,  # type: ignore[arg-type]
        universe=universe,  # type: ignore[arg-type]
        items=[
            RankingItem(
                rank=1,
                stock=_stock(symbol) if market == "US" else StockResponse(
                    id=f"TW:{symbol}",
                    market="TW",
                    symbol=symbol,
                    name=symbol,
                    exchange=universe,
                    currency="TWD",
                    timezone="Asia/Taipei",
                ),
                lastPrice=100.0,
                changePercent=1.0,
                volume=1000,
                volumeDeltaPercent=None,
                netBuySellShares=50 if ranking_type in {RankingType.net_buy, RankingType.net_sell} else None,
                peRatio=12.0 if ranking_type == RankingType.valuation_low_pe else None,
                peRatioType="trailing" if ranking_type == RankingType.valuation_low_pe else None,
                peRatioSource="test" if ranking_type == RankingType.valuation_low_pe else None,
                valuationAsOf="2026-08-20" if ranking_type == RankingType.valuation_low_pe else None,
                valuationWarning=None,
                timestamp="2026-08-20T16:00:00-04:00",
            )
        ],
        timestamp="2026-08-20T20:00:00+00:00",
        source="unit-test-archive",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="EOD archive",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="djia" if universe == "DJIA" else ("nasdaq100" if universe == "NASDAQ100" else None),
        twRankingsScope=None,
    )


def _seed_archive(repo: RankingsArchiveRepository) -> None:
    cases = [
        ("US", "DJIA", RankingType.volume, date(2026, 8, 20), "AAPL"),
        ("US", "DJIA", RankingType.volume, date(2026, 8, 19), "MSFT"),
        ("US", "DJIA", RankingType.volume, date(2026, 8, 18), "IBM"),
        ("US", "DJIA", RankingType.volume, date(2026, 8, 17), "GS"),
        ("US", "DJIA", RankingType.volume, date(2026, 8, 14), "JPM"),  # Fri; gap over weekend
        ("US", "DJIA", RankingType.volume, date(2026, 8, 13), "V"),
        ("US", "NASDAQ100", RankingType.change_up, date(2026, 8, 20), "NVDA"),
        ("TW", "TWSE", RankingType.net_buy, date(2026, 8, 20), "2330"),
        ("TW", "TPEx", RankingType.valuation_low_pe, date(2026, 8, 20), "6488"),
    ]
    for market, universe, ranking_type, trading_date, symbol in cases:
        repo.upsert_eod_snapshot(
            market=market,
            universe=universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
            response=_response(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                symbol=symbol,
            ),
            captured_at=datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc),
        )


def test_list_available_trading_dates_newest_first_and_limited() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())
    _seed_archive(repo)

    dates = repo.list_available_trading_dates(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        limit=5,
    )
    assert dates == [
        date(2026, 8, 20),
        date(2026, 8, 19),
        date(2026, 8, 18),
        date(2026, 8, 17),
        date(2026, 8, 14),
    ]
    # Weekend (Aug 15–16) is absent — only archived trading dates.
    assert date(2026, 8, 16) not in dates
    assert date(2026, 8, 15) not in dates


def test_list_available_trading_dates_filters_by_ranking_type() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())
    _seed_archive(repo)
    # Seed an extra type on a different date so unfiltered lists diverge.
    repo.upsert_eod_snapshot(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.change_up,
        trading_date=date(2026, 8, 12),
        response=_response(ranking_type=RankingType.change_up, symbol="BA"),
        captured_at=datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc),
    )

    volume_dates = repo.list_available_trading_dates(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        limit=10,
    )
    change_dates = repo.list_available_trading_dates(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.change_up,
        limit=10,
    )
    assert date(2026, 8, 20) in volume_dates
    assert date(2026, 8, 12) not in volume_dates
    assert change_dates == [date(2026, 8, 12)]

def test_history_service_reconstructs_all_supported_scopes() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())
    _seed_archive(repo)
    service = RankingsArchiveService(repository=repo)

    us = service.get_history_rankings(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=date(2026, 8, 20),
    )
    assert us is not None
    assert us.tradingDate == "2026-08-20"
    assert us.captureScope == "end_of_day"
    assert us.isMarketOpen is False
    assert us.items[0].stock.symbol == "AAPL"

    nasdaq = service.get_history_rankings(
        market="US",
        universe="NASDAQ100",
        ranking_type=RankingType.change_up,
        trading_date=date(2026, 8, 20),
    )
    assert nasdaq is not None
    assert nasdaq.usRankingsScope == "nasdaq100"

    twse = service.get_history_rankings(
        market="TW",
        universe="TWSE",
        ranking_type=RankingType.net_buy,
        trading_date=date(2026, 8, 20),
    )
    assert twse is not None
    assert twse.twBoard == "TWSE"
    assert twse.items[0].netBuySellShares == 50

    tpex = service.get_history_rankings(
        market="TW",
        universe="TPEx",
        ranking_type=RankingType.valuation_low_pe,
        trading_date=date(2026, 8, 20),
    )
    assert tpex is not None
    assert tpex.items[0].peRatio == 12.0
    assert tpex.twRankingsScope == "quote"
    assert twse.twRankingsScope is None


def test_history_missing_date_and_unavailable_archive() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())
    _seed_archive(repo)
    service = RankingsArchiveService(repository=repo)

    assert (
        service.get_history_rankings(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 1, 1),
        )
        is None
    )

    repo.set_session_factory_override(None)
    assert (
        service.list_history_dates(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
        )
        == []
    )
    assert (
        service.get_history_rankings(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
        )
        is None
    )


def test_history_mid_query_failure_raises_unavailable(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    invalidated: list[str] = []

    def _capture_invalidate(*, reason: str = "outage") -> None:
        invalidated.append(reason)

    monkeypatch.setattr(
        "app.services.rankings_archive_service.invalidate_rankings_archive_engine",
        _capture_invalidate,
    )

    class _BoomRepo:
        def is_available(self) -> bool:
            return True

        def list_available_trading_dates(self, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("login timeout expired"))

        def load_eod_snapshot(self, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("login timeout expired"))

    service = RankingsArchiveService(repository=_BoomRepo())  # type: ignore[arg-type]
    with pytest.raises(RankingsArchiveUnavailableError):
        service.list_history_dates(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
        )
    with pytest.raises(RankingsArchiveUnavailableError):
        service.get_history_rankings(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
        )
    assert invalidated == ["history_dates_failed", "history_load_failed"]


def test_history_sql_syntax_failure_does_not_invalidate_engine(monkeypatch) -> None:
    from sqlalchemy.exc import ProgrammingError

    from app.services.rankings_archive_service import RankingsArchiveQueryError

    invalidated: list[str] = []

    monkeypatch.setattr(
        "app.services.rankings_archive_service.invalidate_rankings_archive_engine",
        lambda *, reason="outage": invalidated.append(reason),
    )

    class _SyntaxRepo:
        def is_available(self) -> bool:
            return True

        def list_available_trading_dates(self, **_kwargs):
            raise ProgrammingError(
                "SELECT TOP 5 DISTINCT ...",
                {},
                Exception("Incorrect syntax near the keyword 'DISTINCT'."),
            )

        def load_eod_snapshot(self, **_kwargs):
            raise ProgrammingError(
                "SELECT TOP 5 DISTINCT ...",
                {},
                Exception("Incorrect syntax near the keyword 'DISTINCT'."),
            )

    service = RankingsArchiveService(repository=_SyntaxRepo())  # type: ignore[arg-type]
    with pytest.raises(RankingsArchiveQueryError):
        service.list_history_dates(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
        )
    with pytest.raises(RankingsArchiveQueryError):
        service.get_history_rankings(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
        )
    assert invalidated == []


def test_history_dates_route_and_history_route(monkeypatch) -> None:
    class _Svc:
        def is_available(self) -> bool:
            return True

        def list_history_dates(self, *, market, universe, ranking_type, limit=5):
            assert market == "US"
            assert universe == "DJIA"
            assert ranking_type == RankingType.volume
            assert limit == 5
            return [date(2026, 8, 20), date(2026, 8, 19)]

        def get_history_rankings(self, **kwargs):
            assert kwargs["trading_date"] == date(2026, 8, 20)
            return _response()

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Svc())

    dates = rankings_route.get_rankings_history_dates(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        limit=5,
    )
    assert dates.dates == ["2026-08-20", "2026-08-19"]

    history = rankings_route.get_rankings_history(
        market="US",
        ranking_type=RankingType.volume,
        trading_date=date(2026, 8, 20),
        limit=50,
        tw_board=None,
        universe="DJIA",
    )
    assert history.items[0].stock.symbol == "AAPL"


def test_history_routes_unavailable_and_not_found(monkeypatch) -> None:
    class _Disabled:
        def is_available(self) -> bool:
            return False

        def is_configured(self) -> bool:
            return False

        def list_history_dates(self, **_kwargs):
            return []

        def get_history_rankings(self, **_kwargs):
            return None

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Disabled())
    disabled_dates = rankings_route.get_rankings_history_dates(
        market="TW",
        universe="TWSE",
        ranking_type=RankingType.net_buy,
    )
    assert disabled_dates.dates == []

    class _Unavailable:
        def is_available(self) -> bool:
            return False

        def is_configured(self) -> bool:
            return True

        def list_history_dates(self, **_kwargs):
            return []

        def get_history_rankings(self, **_kwargs):
            return None

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Unavailable())
    with pytest.raises(HTTPException) as exc_unavailable_dates:
        rankings_route.get_rankings_history_dates(
            market="TW",
            universe="TWSE",
            ranking_type=RankingType.net_buy,
        )
    assert exc_unavailable_dates.value.status_code == 503
    assert exc_unavailable_dates.value.headers.get("Retry-After") == "10"

    class _Missing:
        def is_available(self) -> bool:
            return True

        def get_history_rankings(self, **_kwargs):
            return None

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Missing())
    with pytest.raises(HTTPException) as exc_hist:
        rankings_route.get_rankings_history(
            market="US",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
            universe="DJIA",
        )
    assert exc_hist.value.status_code == 404


def test_history_routes_mid_query_failure_returns_503(monkeypatch) -> None:
    class _Failing:
        def is_available(self) -> bool:
            return True

        def list_history_dates(self, **_kwargs):
            raise RankingsArchiveUnavailableError("dates down")

        def get_history_rankings(self, **_kwargs):
            raise RankingsArchiveUnavailableError("load down")

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Failing())
    with pytest.raises(HTTPException) as exc_dates:
        rankings_route.get_rankings_history_dates(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
        )
    assert exc_dates.value.status_code == 503
    assert exc_dates.value.headers.get("Retry-After") == "10"

    with pytest.raises(HTTPException) as exc_hist:
        rankings_route.get_rankings_history(
            market="US",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
            universe="DJIA",
        )
    assert exc_hist.value.status_code == 503
    assert exc_hist.value.headers.get("Retry-After") == "10"


def test_history_routes_query_logic_failure_returns_500(monkeypatch) -> None:
    from app.services.rankings_archive_service import RankingsArchiveQueryError

    class _Broken:
        def is_available(self) -> bool:
            return True

        def list_history_dates(self, **_kwargs):
            raise RankingsArchiveQueryError("bad sql")

        def get_history_rankings(self, **_kwargs):
            raise RankingsArchiveQueryError("bad sql")

    monkeypatch.setattr(rankings_route, "rankings_archive_service", _Broken())
    with pytest.raises(HTTPException) as exc_dates:
        rankings_route.get_rankings_history_dates(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
        )
    assert exc_dates.value.status_code == 500
    assert exc_dates.value.headers is None or "Retry-After" not in (
        exc_dates.value.headers or {}
    )

    with pytest.raises(HTTPException) as exc_hist:
        rankings_route.get_rankings_history(
            market="US",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 20),
            universe="DJIA",
        )
    assert exc_hist.value.status_code == 500


def test_history_dates_rejects_mismatched_universe() -> None:
    with pytest.raises(HTTPException) as exc:
        rankings_route.get_rankings_history_dates(
            market="US",
            universe="TWSE",
            ranking_type=RankingType.volume,
        )
    assert exc.value.status_code == 422