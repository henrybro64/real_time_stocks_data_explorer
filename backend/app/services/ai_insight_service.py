from __future__ import annotations

from app.services.stock_analysis_service import stock_analysis_service
from app.services.user_ai_provider_service import resolve_user_ai_provider


class AiInsightService:
    def generate_insight(
        self,
        user_id: str,
        stock_id: str,
        provider: str,
    ) -> str:
        provider_override = resolve_user_ai_provider(user_id, provider)
        result = stock_analysis_service.analyze_stock(
            stock_id,
            provider_override=provider_override,
        )
        if result.ai_analysis:
            return result.ai_analysis
        raise RuntimeError("AI explanation is unavailable right now.")


ai_insight_service = AiInsightService()
