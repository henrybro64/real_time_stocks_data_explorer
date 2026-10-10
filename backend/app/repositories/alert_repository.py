from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete, select

from app.db.models import AlertEvent, AlertRule

from .session import (
    SessionBoundRepository,
    apply_field_updates,
    database_is_configured,
    persist_new,
    require_session_factory,
)

_UNSET = object()


class AlertRepository:
    def __init__(self) -> None:
        self._session_factory_override = _UNSET

    def set_session_factory_override(self, session_factory) -> None:
        self._session_factory_override = session_factory

    def is_available(self) -> bool:
        if self._session_factory_override is not _UNSET:
            return self._session_factory_override is not None
        return database_is_configured()

    def _session_factory(self):
        if self._session_factory_override is not _UNSET:
            if self._session_factory_override is None:
                raise RuntimeError("DATABASE_URL is not configured")
            return self._session_factory_override
        return require_session_factory()

    def list_rules(self, user_id: str) -> list[AlertRule]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(AlertRule)
                .where(AlertRule.user_id == user_id)
                .order_by(AlertRule.created_at.asc(), AlertRule.id.asc()),
            ).scalars()
            return list(rows)

    def list_distinct_enabled_stock_ids(self) -> list[str]:
        """Stock IDs with at least one enabled alert rule (for the quote refresher)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(AlertRule.stock_id)
                .where(AlertRule.enabled.is_(True))
                .distinct()
                .order_by(AlertRule.stock_id.asc()),
            ).scalars()
            return [str(stock_id) for stock_id in rows]

    def get_rule(self, user_id: str, rule_id: int) -> AlertRule | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            return session.execute(
                select(AlertRule).where(
                    AlertRule.id == rule_id,
                    AlertRule.user_id == user_id,
                ),
            ).scalar_one_or_none()

    def create_rule(
        self,
        *,
        user_id: str,
        stock_id: str,
        rule_type: str,
        threshold_value: float,
        enabled: bool,
    ) -> AlertRule:
        session_factory = self._session_factory()
        with session_factory() as session:
            return persist_new(
                session,
                AlertRule(
                    user_id=user_id,
                    stock_id=stock_id,
                    rule_type=rule_type,
                    threshold_value=threshold_value,
                    enabled=enabled,
                    last_condition_met=False,
                ),
            )

    def update_rule(
        self,
        user_id: str,
        rule_id: int,
        apply_changes: Callable[[AlertRule], None],
    ) -> AlertRule | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            rule = session.execute(
                select(AlertRule).where(
                    AlertRule.id == rule_id,
                    AlertRule.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if rule is None:
                return None
            apply_changes(rule)
            session.commit()
            session.refresh(rule)
            return rule

    def delete_rule(self, user_id: str, rule_id: int) -> bool:
        session_factory = self._session_factory()
        with session_factory() as session:
            rule = session.execute(
                select(AlertRule).where(
                    AlertRule.id == rule_id,
                    AlertRule.user_id == user_id,
                ),
            ).scalar_one_or_none()
            if rule is None:
                return False
            session.delete(rule)
            session.commit()
            return True

    def list_recent_events(
        self,
        user_id: str,
        *,
        limit: int = 20,
    ) -> list[AlertEvent]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(AlertEvent)
                .where(AlertEvent.user_id == user_id)
                .order_by(AlertEvent.triggered_at.desc(), AlertEvent.id.desc())
                .limit(limit),
            ).scalars()
            return list(rows)

    def delete_events(self, user_id: str) -> int:
        session_factory = self._session_factory()
        with session_factory() as session:
            result = session.execute(
                delete(AlertEvent).where(AlertEvent.user_id == user_id),
            )
            session.commit()
            return result.rowcount or 0

    def evaluate_enabled_rules_for_user(
        self,
        user_id: str,
        evaluator: Callable[[list[AlertRule]], list[AlertEvent]],
    ) -> int:
        session_factory = self._session_factory()
        with session_factory() as session:
            rules = list(
                session.execute(
                    select(AlertRule)
                    .where(
                        AlertRule.user_id == user_id,
                        AlertRule.enabled.is_(True),
                    )
                    .order_by(AlertRule.id.asc())
                ).scalars()
            )
            return self._persist_evaluation(session, rules, evaluator)

    def evaluate_enabled_rules_for_all_users(
        self,
        evaluator: Callable[[list[AlertRule]], list[AlertEvent]],
    ) -> int:
        session_factory = self._session_factory()
        with session_factory() as session:
            rules = list(
                session.execute(
                    select(AlertRule)
                    .where(AlertRule.enabled.is_(True))
                    .order_by(AlertRule.user_id.asc(), AlertRule.id.asc())
                ).scalars()
            )
            return self._persist_evaluation(session, rules, evaluator)

    @staticmethod
    def _persist_evaluation(
        session,
        rules: list[AlertRule],
        evaluator: Callable[[list[AlertRule]], list[AlertEvent]],
    ) -> int:
        if not rules:
            return 0
        events = evaluator(rules)
        if events:
            session.add_all(events)
        session.commit()
        return len(events)


alert_repository = AlertRepository()
