from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.models.stock import StockRecord
from app.schemas.alert import (
    AlertEventResponse,
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
)
from app.schemas.stock import StockResponse
from app.services.alert_service import (
    AlertRuleNotFoundError,
    AlertValidationError,
    alert_service,
)
from app.services.quote_service import quote_service


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/rules", response_model=list[AlertRuleResponse])
def list_alert_rules(
    user_id: str = Depends(get_effective_user_id),
) -> list[AlertRuleResponse]:
    alert_service.evaluate_rules_for_user(user_id)
    return [_rule_response(rule) for rule in alert_service.list_rules(user_id)]


@router.post("/rules", response_model=AlertRuleResponse)
def create_alert_rule(
    payload: AlertRuleCreate,
    user_id: str = Depends(get_effective_user_id),
) -> AlertRuleResponse:
    try:
        rule = alert_service.create_rule(
            user_id,
            stock_id=payload.stockId,
            rule_type=payload.ruleType,
            threshold_value=payload.thresholdValue,
            enabled=payload.enabled,
        )
        alert_service.evaluate_rules_for_user(user_id)
        return _rule_response(alert_service.get_rule(user_id, rule.id))
    except AlertValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from app.api.http_quota import raise_quota_http

        raise_quota_http(exc)
        raise


@router.patch("/rules/{rule_id}", response_model=AlertRuleResponse)
def update_alert_rule(
    rule_id: int,
    payload: AlertRuleUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> AlertRuleResponse:
    try:
        alert_service.update_rule(
            user_id,
            rule_id=rule_id,
            stock_id=payload.stockId,
            rule_type=payload.ruleType,
            threshold_value=payload.thresholdValue,
            enabled=payload.enabled,
        )
        alert_service.evaluate_rules_for_user(user_id)
        return _rule_response(alert_service.get_rule(user_id, rule_id))
    except AlertRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlertValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}", status_code=204)
def delete_alert_rule(
    rule_id: int,
    user_id: str = Depends(get_effective_user_id),
) -> None:
    try:
        alert_service.delete_rule(user_id, rule_id)
    except AlertRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/events", response_model=list[AlertEventResponse])
def list_alert_events(
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(get_effective_user_id),
) -> list[AlertEventResponse]:
    alert_service.evaluate_rules_for_user(user_id)
    rows = alert_service.list_recent_events(user_id, limit=limit)
    return [_event_response(event) for event in rows]


@router.delete("/events", status_code=204)
def delete_alert_events(
    user_id: str = Depends(get_effective_user_id),
) -> None:
    alert_service.delete_events(user_id)


def _rule_response(rule) -> AlertRuleResponse:
    stock = quote_service.get_stock(rule.stock_id)
    return AlertRuleResponse(
        id=rule.id,
        stockId=rule.stock_id,
        stock=_stock_response(stock) if stock is not None else None,
        ruleType=rule.rule_type,
        thresholdValue=float(rule.threshold_value),
        enabled=rule.enabled,
        lastConditionMet=rule.last_condition_met,
        lastObservedPrice=(
            None if rule.last_observed_price is None else float(rule.last_observed_price)
        ),
        lastObservedQuoteTimestamp=rule.last_observed_quote_timestamp,
        lastEvaluatedAt=_iso(rule.last_evaluated_at),
        lastTriggeredAt=_iso(rule.last_triggered_at),
        createdAt=rule.created_at.isoformat(),
        updatedAt=rule.updated_at.isoformat(),
    )


def _event_response(event) -> AlertEventResponse:
    stock = quote_service.get_stock(event.stock_id)
    return AlertEventResponse(
        id=event.id,
        alertRuleId=event.alert_rule_id,
        stockId=event.stock_id,
        stock=_stock_response(stock) if stock is not None else None,
        ruleType=event.rule_type,
        thresholdValue=float(event.threshold_value),
        triggeredAt=event.triggered_at.isoformat(),
        observedPrice=float(event.observed_price),
        quoteTimestamp=event.quote_timestamp,
        message=event.message,
    )


def _stock_response(stock: StockRecord) -> StockResponse:
    return StockResponse(
        id=stock.id,
        market=stock.market,
        symbol=stock.symbol,
        name=stock.name,
        exchange=stock.exchange,
        currency=stock.currency,
        timezone=stock.timezone,
        nameEn=stock.name_en,
        nameZhTw=stock.name_zh_tw,
    )


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()
