from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.services.alert_service as alert_service_module
from app.db.models import AlertEvent, Base, User
from app.services.alert_service import AlertService
from tests.support import quote_record, stock_record


def test_evaluate_rules_deduplicates_continuous_matches_and_preserves_history_on_delete(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(alert_service_module, "SessionLocal", session_local)

    price_state = {"last_price": 205.0}
    service = AlertService()
    service._stock_loader = lambda stock_id: stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    ) if stock_id == "US:AAPL" else None
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
