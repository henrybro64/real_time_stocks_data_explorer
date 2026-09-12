from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.schemas.ai import (
    AiInsightResponse,
    AiProviderPreferenceRequest,
    AiProviderPreferenceResponse,
    ApiKeyStatusResponse,
    ApiKeyUpsertRequest,
)
from app.services.ai_insight_service import ai_insight_service
from app.services.api_key_service import api_key_service
from app.services.user_ai_preference_service import user_ai_preference_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/preferences", response_model=AiProviderPreferenceResponse)
def get_ai_provider_preference(
    user_id: str = Depends(get_effective_user_id),
) -> AiProviderPreferenceResponse:
    try:
        preferred = user_ai_preference_service.get_preferred_provider(user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AiProviderPreferenceResponse(preferredProvider=preferred)


@router.put("/preferences", response_model=AiProviderPreferenceResponse)
def set_ai_provider_preference(
    payload: AiProviderPreferenceRequest,
    user_id: str = Depends(get_effective_user_id),
) -> AiProviderPreferenceResponse:
    try:
        preferred = user_ai_preference_service.set_preferred_provider(
            user_id,
            payload.preferredProvider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AiProviderPreferenceResponse(preferredProvider=preferred)


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
    language: str | None = Query(
        default=None,
        description="Optional narrative language (en or zh-TW). Defaults by market.",
    ),
    user_id: str = Depends(get_effective_user_id),
) -> AiInsightResponse:
    try:
        content = ai_insight_service.generate_insight(
            user_id,
            stock_id,
            provider,
            language=language,
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
