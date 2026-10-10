from __future__ import annotations

from datetime import datetime, timezone
import logging
from threading import Event, Lock, Thread

from app.db.session import SessionLocal as DefaultSessionLocal
from app.db.models import AlertEvent, AlertRule
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.repositories.alert_repository import alert_repository
from app.services.quote_service import quote_service


logger = logging.getLogger(__name__)

SessionLocal = DefaultSessionLocal

SUPPORTED_ALERT_RULE_TYPES = {"price_above", "price_below"}


class AlertValidationError(ValueError):
    pass


class AlertRuleNotFoundError(ValueError):
    pass


def _sync_repository_sessionlocal() -> None:
    alert_repository.set_session_factory_override(SessionLocal)


class AlertService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._evaluation_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._quote_loader = quote_service.get_quote
        self._stock_loader = quote_service.get_stock
        self._stock_resolver = quote_service.resolve_stock_id

    def list_rules(self, user_id: str) -> list[AlertRule]:
        _sync_repository_sessionlocal()
        return alert_repository.list_rules(user_id)

    def get_rule(self, user_id: str, rule_id: int) -> AlertRule:
        _sync_repository_sessionlocal()
        rule = alert_repository.get_rule(user_id, rule_id)
        if rule is None:
            raise AlertRuleNotFoundError(f"Alert rule not found: {rule_id}")
        return rule

    def create_rule(
        self,
        user_id: str,
        *,
        stock_id: str,
        rule_type: str,
        threshold_value: float,
        enabled: bool,
    ) -> AlertRule:
        _sync_repository_sessionlocal()
        resolved_stock_id = self._resolve_stock_id(stock_id)
        self._validate_rule_input(resolved_stock_id, rule_type, threshold_value)
        return alert_repository.create_rule(
            user_id=user_id,
            stock_id=resolved_stock_id,
            rule_type=rule_type,
            threshold_value=threshold_value,
            enabled=enabled,
        )

    def update_rule(
        self,
        user_id: str,
        *,
        rule_id: int,
        stock_id: str | None = None,
        rule_type: str | None = None,
        threshold_value: float | None = None,
        enabled: bool | None = None,
    ) -> AlertRule:
        _sync_repository_sessionlocal()
        next_rule_type = rule_type
        next_threshold = threshold_value

        def apply_changes(rule: AlertRule) -> None:
            # Resolve after the repository loads the owned rule so missing/cross-user
            # updates stay 404 instead of becoming stock validation 400s.
            next_stock_id = self._resolve_stock_id(stock_id) if stock_id is not None else None
            resolved_stock_id = next_stock_id if next_stock_id is not None else rule.stock_id
            resolved_rule_type = next_rule_type if next_rule_type is not None else rule.rule_type
            resolved_threshold = (
                next_threshold if next_threshold is not None else float(rule.threshold_value)
            )
            self._validate_rule_input(
                resolved_stock_id,
                resolved_rule_type,
                resolved_threshold,
            )

            definition_changed = False
            if next_stock_id is not None and next_stock_id != rule.stock_id:
                rule.stock_id = next_stock_id
                definition_changed = True
            if rule_type is not None and rule_type != rule.rule_type:
                rule.rule_type = rule_type
                definition_changed = True
            if threshold_value is not None and threshold_value != float(rule.threshold_value):
                rule.threshold_value = threshold_value
                definition_changed = True
            if enabled is not None and enabled != rule.enabled:
                rule.enabled = enabled
                definition_changed = True

            if definition_changed:
                rule.last_condition_met = False

        rule = alert_repository.update_rule(user_id, rule_id, apply_changes)
        if rule is None:
            raise AlertRuleNotFoundError(f"Alert rule not found: {rule_id}")
        return rule

    def delete_rule(self, user_id: str, rule_id: int) -> None:
        _sync_repository_sessionlocal()
        deleted = alert_repository.delete_rule(user_id, rule_id)
        if not deleted:
            raise AlertRuleNotFoundError(f"Alert rule not found: {rule_id}")

    def list_recent_events(
        self,
        user_id: str,
        *,
        limit: int = 20,
    ) -> list[AlertEvent]:
        _sync_repository_sessionlocal()
        return alert_repository.list_recent_events(user_id, limit=limit)

    def delete_events(self, user_id: str) -> None:
        _sync_repository_sessionlocal()
        if not alert_repository.is_available():
            return
        with self._evaluation_lock:
            alert_repository.delete_events(user_id)

    def evaluate_rules_for_user(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        _sync_repository_sessionlocal()
        if not alert_repository.is_available():
            return 0
        with self._evaluation_lock:
            return alert_repository.evaluate_enabled_rules_for_user(
                user_id,
                lambda rules: self._evaluate_rules(rules, now=now),
            )

    def evaluate_rules_for_all_users(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        _sync_repository_sessionlocal()
        if not alert_repository.is_available():
            return 0
        with self._evaluation_lock:
            return alert_repository.evaluate_enabled_rules_for_all_users(
                lambda rules: self._evaluate_rules(rules, now=now),
            )

    def start_background_evaluator(self, poll_seconds: int) -> None:
        _sync_repository_sessionlocal()
        if not alert_repository.is_available():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = Event()
            self._thread = Thread(
                target=self._run_evaluator,
                args=(poll_seconds,),
                daemon=True,
                name="alert-rule-evaluator",
            )
            self._thread.start()

    def stop_background_evaluator(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._thread = None
            self._stop_event.set()
        thread.join(timeout=1.0)

    def _run_evaluator(self, poll_seconds: int) -> None:
        from app.jobs.health import run_guarded_tick

        while not self._stop_event.wait(poll_seconds):
            run_guarded_tick(
                "alert-rule-evaluator",
                event="alerts.evaluator.failed",
                tick=self.evaluate_rules_for_all_users,
            )

    def _evaluate_rules(
        self,
        rules: list[AlertRule],
        *,
        now: datetime | None,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        evaluated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        quotes_by_stock = self._load_quotes({rule.stock_id for rule in rules})

        for rule in rules:
            quote = quotes_by_stock.get(rule.stock_id)
            if quote is None:
                continue

            observed_price = float(quote.last_price)
            condition_met = self._condition_met(
                rule.rule_type,
                observed_price,
                float(rule.threshold_value),
            )

            rule.last_observed_price = observed_price
            rule.last_observed_quote_timestamp = quote.timestamp
            rule.last_evaluated_at = evaluated_at

            if condition_met and not rule.last_condition_met:
                events.append(
                    AlertEvent(
                        alert_rule_id=rule.id,
                        user_id=rule.user_id,
                        stock_id=rule.stock_id,
                        rule_type=rule.rule_type,
                        threshold_value=float(rule.threshold_value),
                        triggered_at=evaluated_at,
                        observed_price=observed_price,
                        quote_timestamp=quote.timestamp,
                        message=self._build_event_message(rule, observed_price),
                    )
                )
                rule.last_condition_met = True
                rule.last_triggered_at = evaluated_at
            elif not condition_met and rule.last_condition_met:
                rule.last_condition_met = False

        return events

    def _load_quotes(self, stock_ids: set[str]) -> dict[str, QuoteRecord | None]:
        quotes_by_stock: dict[str, QuoteRecord | None] = {}
        for stock_id in stock_ids:
            try:
                quotes_by_stock[stock_id] = self._quote_loader(stock_id)
            except Exception:
                logger.exception("Alert quote fetch failed for %s", stock_id)
                quotes_by_stock[stock_id] = None
        return quotes_by_stock

    def _validate_rule_input(
        self,
        stock_id: str,
        rule_type: str,
        threshold_value: float,
    ) -> None:
        if rule_type not in SUPPORTED_ALERT_RULE_TYPES:
            raise AlertValidationError(f"Unsupported alert rule type: {rule_type}")
        if threshold_value <= 0:
            raise AlertValidationError("Threshold value must be greater than 0")
        stock = self._stock_loader(stock_id)
        if stock is None:
            raise AlertValidationError(f"Unknown stock id: {stock_id}")

    def _resolve_stock_id(self, stock_id: str) -> str:
        try:
            return self._stock_resolver(stock_id)
        except ValueError as exc:
            raise AlertValidationError(str(exc)) from exc

    @staticmethod
    def _condition_met(rule_type: str, observed_price: float, threshold_value: float) -> bool:
        if rule_type == "price_above":
            return observed_price > threshold_value
        if rule_type == "price_below":
            return observed_price < threshold_value
        raise AlertValidationError(f"Unsupported alert rule type: {rule_type}")

    def _build_event_message(self, rule: AlertRule, observed_price: float) -> str:
        stock = self._safe_stock_lookup(rule.stock_id)
        label = stock.symbol if stock is not None else rule.stock_id
        comparator = "rose above" if rule.rule_type == "price_above" else "fell below"
        return (
            f"{label} {comparator} {float(rule.threshold_value):.2f} "
            f"(observed {observed_price:.2f})."
        )

    def _safe_stock_lookup(self, stock_id: str) -> StockRecord | None:
        try:
            return self._stock_loader(stock_id)
        except Exception:
            logger.exception("Alert stock lookup failed for %s", stock_id)
            return None


alert_service = AlertService()
