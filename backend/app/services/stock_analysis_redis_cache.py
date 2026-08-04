"""Redis cache for stock AI analysis payloads (narrative + citations only)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.services.ai_providers.base import AnalysisCitation, AnalysisSearchResult

logger = logging.getLogger(__name__)

_KEY_PREFIX = "stock_analysis:v1:"


def _redis_get(key: str) -> str | None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    return run_redis(
        f"stock analysis get {key}",
        lambda client: client.get(key),
        kind="read",
    )


def _redis_setex(key: str, ttl_seconds: int, value: str) -> None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    run_redis(
        f"stock analysis setex {key}",
        lambda client: client.setex(key, ttl_seconds, value),
        kind="write",
    )


def stock_analysis_cache_key(user_id: str, stock_id: str, provider_bucket: str) -> str:
    safe_bucket = provider_bucket.replace("\n", "").strip() or "unknown"
    return f"{_KEY_PREFIX}{user_id}:{stock_id}:{safe_bucket}"


@dataclass(frozen=True)
class CachedStockAnalysisPayload:
    analyzed_at: datetime
    ai_provider: str | None
    ai_model: str | None
    ai_analysis: str
    citations: tuple[AnalysisCitation, ...]
    search_results: tuple[AnalysisSearchResult, ...]


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_cached_stock_analysis(
    user_id: str,
    stock_id: str,
    provider_bucket: str,
) -> CachedStockAnalysisPayload | None:
    raw = _redis_get(stock_analysis_cache_key(user_id, stock_id, provider_bucket))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        analyzed_raw = data.get("analyzedAt")
        analysis_text = data.get("aiAnalysis")
        if not isinstance(analyzed_raw, str) or not isinstance(analysis_text, str):
            return None
        citations_in = data.get("citations") or []
        search_in = data.get("searchResults") or []
        citations: tuple[AnalysisCitation, ...] = ()
        if isinstance(citations_in, list):
            citations = tuple(
                AnalysisCitation(
                    url=str(item["url"]),
                    title=item.get("title"),
                    source=item.get("source"),
                )
                for item in citations_in
                if isinstance(item, dict) and item.get("url")
            )
        search_results: tuple[AnalysisSearchResult, ...] = ()
        if isinstance(search_in, list):
            search_results = tuple(
                AnalysisSearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    source=item.get("source"),
                )
                for item in search_in
                if isinstance(item, dict) and item.get("url")
            )
        return CachedStockAnalysisPayload(
            analyzed_at=_parse_iso_datetime(analyzed_raw),
            ai_provider=data.get("aiProvider") if isinstance(data.get("aiProvider"), str) else None,
            ai_model=data.get("aiModel") if isinstance(data.get("aiModel"), str) else None,
            ai_analysis=analysis_text,
            citations=citations,
            search_results=search_results,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis decode stock analysis %s:%s: %s", user_id, stock_id, exc)
        return None


def set_cached_stock_analysis(
    user_id: str,
    stock_id: str,
    provider_bucket: str,
    *,
    analyzed_at: datetime,
    ai_provider: str | None,
    ai_model: str | None,
    ai_analysis: str,
    citations: tuple[AnalysisCitation, ...],
    search_results: tuple[AnalysisSearchResult, ...],
) -> None:
    if not settings.redis_url:
        return
    at = analyzed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "analyzedAt": at,
        "aiProvider": ai_provider,
        "aiModel": ai_model,
        "aiAnalysis": ai_analysis,
        "citations": [
            {"url": c.url, "title": c.title, "source": c.source} for c in citations
        ],
        "searchResults": [
            {"title": s.title, "url": s.url, "source": s.source} for s in search_results
        ],
    }
    _redis_setex(
        stock_analysis_cache_key(user_id, stock_id, provider_bucket),
        settings.stock_analysis_cache_ttl_seconds,
        json.dumps(payload),
    )
