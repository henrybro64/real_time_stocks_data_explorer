"""Redis cache for stock AI analysis payloads (narrative + citations + news snapshot)."""

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


def stock_analysis_cache_key(
    user_id: str,
    stock_id: str,
    provider_bucket: str,
    language: str,
) -> str:
    safe_bucket = provider_bucket.replace("\n", "").strip() or "unknown"
    safe_language = language.replace("\n", "").strip() or "zh-TW"
    return f"{_KEY_PREFIX}{user_id}:{stock_id}:{safe_bucket}:{safe_language}"


@dataclass(frozen=True)
class CachedStockAnalysisPayload:
    analyzed_at: datetime
    ai_provider: str | None
    ai_model: str | None
    ai_analysis: str
    citations: tuple[AnalysisCitation, ...]
    search_results: tuple[AnalysisSearchResult, ...]
    recent_news_context: dict[str, object] | None = None
    language: str | None = None


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_citations(raw: object) -> tuple[AnalysisCitation, ...]:
    if not isinstance(raw, list):
        return ()
    citations: list[AnalysisCitation] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        published = item.get("publishedAt")
        citations.append(
            AnalysisCitation(
                url=str(item["url"]),
                title=item.get("title") if isinstance(item.get("title"), str) else None,
                source=item.get("source") if isinstance(item.get("source"), str) else None,
                published_at=published if isinstance(published, str) else None,
            )
        )
    return tuple(citations)


def _parse_search_results(raw: object) -> tuple[AnalysisSearchResult, ...]:
    if not isinstance(raw, list):
        return ()
    results: list[AnalysisSearchResult] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        results.append(
            AnalysisSearchResult(
                title=str(item.get("title") or ""),
                url=str(item["url"]),
                source=item.get("source") if isinstance(item.get("source"), str) else None,
            )
        )
    return tuple(results)


def get_cached_stock_analysis(
    user_id: str,
    stock_id: str,
    provider_bucket: str,
    language: str,
) -> CachedStockAnalysisPayload | None:
    raw = _redis_get(
        stock_analysis_cache_key(user_id, stock_id, provider_bucket, language),
    )
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
        news_raw = data.get("recentNewsContext")
        news_context = news_raw if isinstance(news_raw, dict) else None
        language_raw = data.get("language")
        return CachedStockAnalysisPayload(
            analyzed_at=_parse_iso_datetime(analyzed_raw),
            ai_provider=data.get("aiProvider") if isinstance(data.get("aiProvider"), str) else None,
            ai_model=data.get("aiModel") if isinstance(data.get("aiModel"), str) else None,
            ai_analysis=analysis_text,
            citations=_parse_citations(data.get("citations")),
            search_results=_parse_search_results(data.get("searchResults")),
            recent_news_context=news_context,
            language=language_raw if isinstance(language_raw, str) else language,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis decode stock analysis %s:%s: %s", user_id, stock_id, exc)
        return None


def set_cached_stock_analysis(
    user_id: str,
    stock_id: str,
    provider_bucket: str,
    language: str,
    *,
    analyzed_at: datetime,
    ai_provider: str | None,
    ai_model: str | None,
    ai_analysis: str,
    citations: tuple[AnalysisCitation, ...],
    search_results: tuple[AnalysisSearchResult, ...],
    recent_news_context: dict[str, object] | None = None,
) -> None:
    if not settings.redis_url:
        return
    at = analyzed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "analyzedAt": at,
        "language": language,
        "aiProvider": ai_provider,
        "aiModel": ai_model,
        "aiAnalysis": ai_analysis,
        "citations": [
            {
                "url": citation.url,
                "title": citation.title,
                "source": citation.source,
                "publishedAt": citation.published_at,
            }
            for citation in citations
        ],
        "searchResults": [
            {"title": item.title, "url": item.url, "source": item.source}
            for item in search_results
        ],
        "recentNewsContext": recent_news_context,
    }
    _redis_setex(
        stock_analysis_cache_key(user_id, stock_id, provider_bucket, language),
        settings.stock_analysis_cache_ttl_seconds,
        json.dumps(payload),
    )
