from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.services.alert_service as alert_service_module
from app.db.models import AlertEvent, Base, User
from app.services.alert_service import AlertRuleNotFoundError, AlertService, AlertValidationError
from tests.support import quote_record, stock_record


def _stub_resolve_stock_id(get_stock):
    def resolve_stock_id(raw: str) -> str:
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError(f"Unknown stock id: {raw}")
        if ":" in cleaned:
            market, symbol = cleaned.split(":", 1)
            market = market.strip().upper()
            symbol = symbol.strip().upper() if market == "US" else symbol.strip()
            candidate = f"{market}:{symbol}"
        elif cleaned.isdigit():
            candidate = f"TW:{cleaned}"
        else:
            candidate = f"US:{cleaned.upper()}"
        if get_stock(candidate) is not None:
            return candidate
        raise ValueError(f"Unknown stock id: {cleaned}")

    return resolve_stock_id


def _build_test_alert_service(monkeypatch, *, initial_price: float = 205.0):
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(alert_service_module, "SessionLocal", session_local)

    price_state = {"last_price": initial_price}
    service = AlertService()

    def get_stock(stock_id: str):
        return (
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
            if stock_id == "US:AAPL"
            else None
        )

    service._stock_loader = get_stock
    service._stock_resolver = _stub_resolve_stock_id(get_stock)
    service._quote_loader = lambda stock_id: quote_record(
        "US:AAPL",
        last_price=price_state["last_price"],
        change=1.0,
        change_percent=0.5,
        previous_close=price_state["last_price"] - 1,
        timestamp="2026-05-26T09:30:00-04:00",
        is_market_open=True,
    ) if stock_id == "US:AAPL" else None

    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    return service, session_local, price_state


def test_evaluate_rules_deduplicates_continuous_matches_and_preserves_history_on_delete(
    monkeypatch,
) -> None:
    service, session_local, price_state = _build_test_alert_service(
        monkeypatch,
        initial_price=205.0,
    )

    rule = service.create_rule(
        "user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )

    triggered_first = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc),
    )
    triggered_second = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 31, tzinfo=timezone.utc),
    )

    assert triggered_first == 1
    assert triggered_second == 0
    assert len(service.list_recent_events("user-1")) == 1

    price_state["last_price"] = 199.0
    reset_count = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 32, tzinfo=timezone.utc),
    )
    price_state["last_price"] = 207.0
    retrigger_count = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 33, tzinfo=timezone.utc),
    )

    assert reset_count == 0
    assert retrigger_count == 1
    events_before_delete = service.list_recent_events("user-1")
    assert len(events_before_delete) == 2
    assert events_before_delete[0].stock_id == "US:AAPL"
    assert events_before_delete[0].rule_type == "price_above"
    assert events_before_delete[0].threshold_value == 200.0

    service.delete_rule("user-1", rule.id)

    remaining_events = service.list_recent_events("user-1")
    assert len(remaining_events) == 2
    assert {event.alert_rule_id for event in remaining_events} == {None}

    with session_local() as session:
        rows = list(session.query(AlertEvent).all())
    assert len(rows) == 2


def test_delete_events_preserves_rule_state_and_suppresses_duplicate_events(
    monkeypatch,
) -> None:
    service, _session_local, price_state = _build_test_alert_service(
        monkeypatch,
        initial_price=205.0,
    )

    service.create_rule(
        "user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )

    triggered = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc),
    )
    assert triggered == 1
    assert len(service.list_recent_events("user-1")) == 1
    rule_before = service.list_rules("user-1")[0]
    assert rule_before.last_condition_met is True

    service.delete_events("user-1")

    assert service.list_recent_events("user-1") == []
    rule_after = service.list_rules("user-1")[0]
    assert rule_after.last_condition_met is True
    assert rule_after.last_triggered_at == rule_before.last_triggered_at

    price_state["last_price"] = 206.0
    recreated = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 40, tzinfo=timezone.utc),
    )
    assert recreated == 0
    assert service.list_recent_events("user-1") == []


def test_delete_events_runs_under_evaluation_lock(monkeypatch) -> None:
    service, _session_local, _price_state = _build_test_alert_service(monkeypatch)
    lock_held: list[bool] = []

    def spy_delete(user_id: str) -> int:
        lock_held.append(service._evaluation_lock.locked())
        return 0

    monkeypatch.setattr(
        "app.services.alert_service.alert_repository.delete_events",
        spy_delete,
    )

    service.delete_events("user-1")

    assert lock_held == [True]


def test_create_rule_resolves_bare_and_lowercase_tickers(monkeypatch) -> None:
    service, _session_local, _price_state = _build_test_alert_service(monkeypatch)

    for raw in ("AAPL", "aapl", "  AAPL  "):
        rule = service.create_rule(
            "user-1",
            stock_id=raw,
            rule_type="price_above",
            threshold_value=200.0,
            enabled=True,
        )
        assert rule.stock_id == "US:AAPL"


def test_update_missing_rule_prefers_not_found_over_stock_validation(monkeypatch) -> None:
    service, _session_local, _price_state = _build_test_alert_service(monkeypatch)

    with pytest.raises(AlertRuleNotFoundError, match="Alert rule not found: 999"):
        service.update_rule(
            "user-1",
            rule_id=999,
            stock_id="ZZZZZ",
            threshold_value=210.0,
        )


def test_update_existing_rule_still_validates_unknown_stock(monkeypatch) -> None:
    service, _session_local, _price_state = _build_test_alert_service(monkeypatch)
    rule = service.create_rule(
        "user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )

    with pytest.raises(AlertValidationError, match="Unknown stock id: ZZZZZ"):
        service.update_rule(
            "user-1",
            rule_id=rule.id,
            stock_id="ZZZZZ",
        )


def test_price_below_rule_uses_strict_threshold_boundary(monkeypatch) -> None:
    service, _session_local, price_state = _build_test_alert_service(
        monkeypatch,
        initial_price=200.0,
    )

    service.create_rule(
        "user-1",
        stock_id="US:AAPL",
        rule_type="price_below",
        threshold_value=200.0,
        enabled=True,
    )

    equal_boundary = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 30, tzinfo=timezone.utc),
    )
    assert equal_boundary == 0
    assert service.list_recent_events("user-1") == []

    price_state["last_price"] = 199.99
    below_boundary = service.evaluate_rules_for_user(
        "user-1",
        now=datetime(2026, 5, 26, 13, 31, tzinfo=timezone.utc),
    )
    assert below_boundary == 1

    events = service.list_recent_events("user-1")
    assert len(events) == 1
    assert events[0].rule_type == "price_below"
    assert events[0].threshold_value == 200.0
