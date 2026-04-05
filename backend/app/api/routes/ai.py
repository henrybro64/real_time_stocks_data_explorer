from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.schemas.ai import (
    AiInsightResponse,
    ApiKeyStatusResponse,
    ApiKeyUpsertRequest,
)
from app.services.ai_insight_service import ai_insight_service
from app.services.api_key_service import api_key_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/keys/{provider}", response_model=ApiKeyStatusResponse)
def get_api_key_status(
    provider: str,
    user_id: str = Depends(get_effective_user_id),
) -> ApiKeyStatusResponse:
    try:
        configured, key_last4 = api_key_service.get_status(user_id, provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ApiKeyStatusResponse(
        provider=provider.lower(),
        configured=configured,
        keyLast4=key_last4,
    )


@router.put("/keys/{provider}", response_model=ApiKeyStatusResponse)
def upsert_api_key(
    provider: str,
    payload: ApiKeyUpsertRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ApiKeyStatusResponse:
    try:
        configured, key_last4 = api_key_service.upsert_key(
            user_id,
            provider,
            payload.apiKey,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ApiKeyStatusResponse(
        provider=provider.lower(),
        configured=configured,
        keyLast4=key_last4,
    )


@router.get("/insights/{stock_id}", response_model=AiInsightResponse)
def get_ai_insight(
    stock_id: str,
    provider: str = Query(default="openai"),
    user_id: str = Depends(get_effective_user_id),
) -> AiInsightResponse:
    try:
        content = ai_insight_service.generate_insight(
            user_id,
            stock_id,
            provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AiInsightResponse(
        provider=provider.lower(),
        stockId=stock_id,
        content=content,
    )
