"""Per-user quotas and rate limits (#273)."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import (
    AlertRule,
    Base,
    PortfolioAsset,
    PortfolioPosition,
    User,
    WatchlistItem,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.watchlist_repository import WatchlistRepository
from app.services.rate_limit_service import (
    RateLimitExceededError,
    check_rate_limit,
    clear_rate_limits_for_tests,
    require_rate_limit,
)
from app.services.user_quota_service import (
    UserQuotaExceededError,
    consume_server_ai_budget,
    enforce_alert_rule_cap,
    refund_server_ai_budget,
    enforce_portfolio_asset_cap,
    enforce_portfolio_position_cap,
    enforce_watchlist_cap,
    ensure_server_ai_budget_available,
)


@pytest.fixture(autouse=True)
def _clear_limits():
    clear_rate_limits_for_tests()
    yield
    clear_rate_limits_for_tests()


def test_memory_rate_limit_allows_then_blocks() -> None:
    assert check_rate_limit("t:a", limit=2, window_seconds=60).allowed
    assert check_rate_limit("t:a", limit=2, window_seconds=60).allowed
    decision = check_rate_limit("t:a", limit=2, window_seconds=60)
    assert not decision.allowed
    assert decision.retry_after_seconds >= 1
    with pytest.raises(RateLimitExceededError):
        require_rate_limit("t:a", limit=2, window_seconds=60, detail="slow down")


def test_watchlist_cap_raises_at_limit(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "user_watchlist_max_items", 2)
    enforce_watchlist_cap("u1", current_count=1)
    with pytest.raises(UserQuotaExceededError, match="Watchlist limit"):
        enforce_watchlist_cap("u1", current_count=2)


def test_alert_cap_uses_configured_limit(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "user_alert_max_rules", 3)
    enforce_alert_rule_cap("u1", current_count=2)
    with pytest.raises(UserQuotaExceededError, match="Alert rule limit"):
        enforce_alert_rule_cap("u1", current_count=3)


def test_position_and_asset_caps_raise_at_limit(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "user_portfolio_max_positions", 1)
    monkeypatch.setattr(config.settings, "user_portfolio_max_assets", 1)
    enforce_portfolio_position_cap("u1", current_count=0)
    enforce_portfolio_asset_cap("u1", current_count=0)
    with pytest.raises(UserQuotaExceededError, match="position limit"):
        enforce_portfolio_position_cap("u1", current_count=1)
    with pytest.raises(UserQuotaExceededError, match="Other-asset limit"):
        enforce_portfolio_asset_cap("u1", current_count=1)


def test_single_user_cap_defaults_stay_under_a_few_hundred() -> None:
    assert Settings.model_fields["user_watchlist_max_items"].default == 100
    assert Settings.model_fields["user_portfolio_max_positions"].default == 80
    assert Settings.model_fields["user_portfolio_max_assets"].default == 50
    assert Settings.model_fields["user_alert_max_rules"].default == 80


def test_server_ai_budget_is_not_consumed_until_asked(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "user_ai_server_key_daily_limit", 1)
    ensure_server_ai_budget_available("u1")
    ensure_server_ai_budget_available("u1")
    consume_server_ai_budget("u1")
    with pytest.raises(RateLimitExceededError, match="budget"):
        ensure_server_ai_budget_available("u1")


def test_server_ai_budget_refund_returns_the_reserved_slot(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "user_ai_server_key_daily_limit", 1)
    consume_server_ai_budget("u1")
    with pytest.raises(RateLimitExceededError, match="budget"):
        consume_server_ai_budget("u1")
    refund_server_ai_budget("u1")
    consume_server_ai_budget("u1")


def test_login_ip_uses_the_proxy_appended_address() -> None:
    from types import SimpleNamespace

    from app.api.routes.auth import _client_ip
    from app.core import config

    request = SimpleNamespace(
        headers={"x-forwarded-for": "spoofed, 203.0.113.8"},
        client=SimpleNamespace(host="10.0.0.8"),
    )
    original = config.settings.trusted_proxy_hops
    config.settings.trusted_proxy_hops = 1
    try:
        assert _client_ip(request) == "203.0.113.8"
        config.settings.trusted_proxy_hops = 0
        assert _client_ip(request) == "10.0.0.8"
    finally:
        config.settings.trusted_proxy_hops = original


def _sqlite_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            WatchlistItem.__table__,
            PortfolioPosition.__table__,
            PortfolioAsset.__table__,
            AlertRule.__table__,
        ],
    )
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(User(id="u1", username="u1"))
        session.commit()
    return factory


def test_overlapping_watchlist_adds_do_not_pass_the_cap(monkeypatch) -> None:
    from app.core import config

    factory = _sqlite_factory()
    monkeypatch.setattr(
        "app.repositories.watchlist_repository.require_session_factory",
        lambda: factory,
    )
    monkeypatch.setattr(config.settings, "user_watchlist_max_items", 1)
    repo = WatchlistRepository()
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def add(stock_id: str) -> None:
        barrier.wait()
        try:
            repo.add_if_missing("u1", stock_id)
        except UserQuotaExceededError:
            errors.append(stock_id)

    threads = [
        threading.Thread(target=add, args=("US:AAA",)),
        threading.Thread(target=add, args=("US:BBB",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with factory() as session:
        stored = session.execute(select(WatchlistItem.stock_id)).scalars().all()
    assert stored == ["US:AAA"] or stored == ["US:BBB"]
    assert len(stored) == 1
    assert len(errors) == 1


def test_position_asset_and_alert_caps_do_not_write_the_row(monkeypatch) -> None:
    from app.core import config

    factory = _sqlite_factory()
    monkeypatch.setattr(
        "app.repositories.portfolio_repository.require_session_factory",
        lambda: factory,
    )
    monkeypatch.setattr(config.settings, "user_portfolio_max_positions", 1)
    monkeypatch.setattr(config.settings, "user_portfolio_max_assets", 1)
    positions = PortfolioRepository()
    positions.create_position(
        user_id="u1",
        stock_id="US:AAA",
        quantity=1,
        average_cost=1,
        currency="USD",
        notes=None,
    )
    with pytest.raises(UserQuotaExceededError):
        positions.create_position(
            user_id="u1",
            stock_id="US:BBB",
            quantity=1,
            average_cost=1,
            currency="USD",
            notes=None,
        )
    positions.create_asset(
        user_id="u1",
        asset_type="cash",
        name="cash",
        currency="USD",
        value=1,
        notes=None,
    )
    with pytest.raises(UserQuotaExceededError):
        positions.create_asset(
            user_id="u1",
            asset_type="cash",
            name="more",
            currency="USD",
            value=1,
            notes=None,
        )

    alerts = AlertRepository()
    alerts.set_session_factory_override(factory)
    monkeypatch.setattr(config.settings, "user_alert_max_rules", 1)
    alerts.create_rule(
        user_id="u1",
        stock_id="US:AAA",
        rule_type="price_above",
        threshold_value=1,
        enabled=True,
    )
    with pytest.raises(UserQuotaExceededError):
        alerts.create_rule(
            user_id="u1",
            stock_id="US:BBB",
            rule_type="price_above",
            threshold_value=1,
            enabled=True,
        )
    with factory() as session:
        assert session.execute(select(PortfolioPosition)).scalars().all().__len__() == 1
        assert session.execute(select(PortfolioAsset)).scalars().all().__len__() == 1
        assert session.execute(select(AlertRule)).scalars().all().__len__() == 1
