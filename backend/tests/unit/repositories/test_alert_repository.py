from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.models import AlertEvent, AlertRule, Base, User
from app.repositories.alert_repository import AlertRepository


def _build_session_local():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return session_local


def test_alert_repository_session_override_supports_crud() -> None:
    session_local = _build_session_local()
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    repository = AlertRepository()
    repository.set_session_factory_override(session_local)

    assert repository.is_available() is True
    assert repository.list_rules("user-1") == []

    created = repository.create_rule(
        user_id="user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )

    rules = repository.list_rules("user-1")
    assert len(rules) == 1
    assert rules[0].id == created.id
    assert rules[0].stock_id == "US:AAPL"

    fetched = repository.get_rule("user-1", created.id)
    assert fetched is not None
    assert fetched.rule_type == "price_above"

    updated = repository.update_rule(
        "user-1",
        created.id,
        lambda rule: setattr(rule, "enabled", False),
    )
    assert updated is not None
    assert updated.enabled is False

    assert repository.delete_rule("user-1", created.id) is True
    assert repository.get_rule("user-1", created.id) is None
    assert repository.delete_rule("user-1", created.id) is False


def test_alert_repository_evaluate_enabled_rules_for_user(monkeypatch) -> None:
    session_local = _build_session_local()
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    repository = AlertRepository()
    repository.set_session_factory_override(session_local)
    repository.create_rule(
        user_id="user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )

    def evaluator(rules: list[AlertRule]):
        return []

    assert repository.evaluate_enabled_rules_for_user("user-1", evaluator) == 0


def test_alert_repository_delete_events_is_user_scoped() -> None:
    session_local = _build_session_local()
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.add(User(id="user-2", username="user-2", password_hash=None))
        session.commit()

    repository = AlertRepository()
    repository.set_session_factory_override(session_local)

    rule_one = repository.create_rule(
        user_id="user-1",
        stock_id="US:AAPL",
        rule_type="price_above",
        threshold_value=200.0,
        enabled=True,
    )
    rule_two = repository.create_rule(
        user_id="user-2",
        stock_id="US:MSFT",
        rule_type="price_below",
        threshold_value=300.0,
        enabled=True,
    )

    with session_local() as session:
        session.add_all(
            [
                AlertEvent(
                    alert_rule_id=rule_one.id,
                    user_id="user-1",
                    stock_id="US:AAPL",
                    rule_type="price_above",
                    threshold_value=200.0,
                    triggered_at=rule_one.created_at,
                    observed_price=205.0,
                    quote_timestamp="2026-05-26T09:30:00-04:00",
                    message="user-1 event",
                ),
                AlertEvent(
                    alert_rule_id=rule_two.id,
                    user_id="user-2",
                    stock_id="US:MSFT",
                    rule_type="price_below",
                    threshold_value=300.0,
                    triggered_at=rule_two.created_at,
                    observed_price=290.0,
                    quote_timestamp="2026-05-26T09:30:00-04:00",
                    message="user-2 event",
                ),
            ]
        )
        session.commit()

    deleted_count = repository.delete_events("user-1")
    assert deleted_count == 1
    assert repository.list_recent_events("user-1") == []
    assert len(repository.list_recent_events("user-2")) == 1
    assert repository.list_rules("user-1")[0].id == rule_one.id
    assert repository.list_rules("user-2")[0].id == rule_two.id

    assert repository.delete_events("user-1") == 0


def test_alert_repository_session_override_none_marks_unavailable() -> None:
    repository = AlertRepository()
    repository.set_session_factory_override(None)

    assert repository.is_available() is False
