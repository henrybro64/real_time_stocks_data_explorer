from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.diagnostics import elapsed_ms
from app.models.stock import StockRecord

_REQUEST_TIMEOUT_SECONDS = 8
_GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
_MAX_ITEMS = 12
_ANALYSIS_MAX_FETCH = 40
_ANALYSIS_MAX_SELECTED = 5
_ANALYSIS_PRIMARY_WINDOW_DAYS = 7
_ANALYSIS_FALLBACK_WINDOW_DAYS = 30
_WINDOW_HOURS = 24
_ZHTW_CHAR_RANGE = ("\u4e00", "\u9fff")
_TITLE_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
# Google News appends " - Publisher" / " — Publisher" to syndicated headlines.
# Single separator spaces only: the source format is fixed, and unbounded \s+
# on both sides of the dash makes the match super-linear.
_PUBLISHER_SUFFIX_RE = re.compile(r" [-–—] [^-–—]{2,40}$")
_SYNDICATION_SIMILARITY = 0.8
_SYNDICATION_MIN_TOKENS = 4
# Short/common tickers that often appear as ordinary words; require a name match.
_COMMON_OR_AMBIGUOUS_SYMBOLS = frozenset(
    {
        "A",
        "AI",
        "ALL",
        "AM",
        "AN",
        "ARE",
        "AS",
        "AT",
        "BE",
        "BIG",
        "BY",
        "CAN",
        "DD",
        "DO",
        "FOR",
        "GO",
        "HAS",
        "IT",
        "LOW",
        "NEW",
        "NOW",
        "OE",
        "ON",
        "ONE",
        "OR",
        "SO",
        "T",
        "TO",
        "TOP",
        "TWO",
        "UP",
    }
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str


@dataclass(frozen=True)
class AnalysisNewsItem:
    id: str  # N1, N2, ...
    title: str
    url: str
    source: str
    published_at: str  # ISO


@dataclass(frozen=True)
class AnalysisNewsContext:
    available: bool
    window_start: str | None
    window_end: str | None
    data_as_of: str | None
    items: tuple[AnalysisNewsItem, ...]
    fetched_count: int = 0
    selected_count: int = 0
    failure_reason: str | None = None
    window_days: int | None = None

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "recentNewsContext": {
                "available": self.available,
                "windowDays": self.window_days,
                "windowStart": self.window_start,
                "windowEnd": self.window_end,
                "dataAsOf": self.data_as_of,
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "source": item.source,
                        "publishedAt": item.published_at,
                        "url": item.url,
                    }
                    for item in self.items
                ],
                "failureReason": self.failure_reason,
            }
        }


class NewsService:
    def get_recent_news(self, stock: StockRecord) -> list[NewsItem]:
        queries = _build_queries(stock)
        profile = _market_news_profile(stock)
        for query in queries:
            items = _fetch_google_news(
                query,
                profile=profile,
                max_items=_MAX_ITEMS,
                stock_id=stock.id,
                window_days=1,
            )
            if items:
                return items
        logger.info(
            "No recent news items were found",
            extra={
                "event": "news.fetch.empty",
                "provider": "google_news",
                "stock_id": stock.id,
                "query_count": len(queries),
            },
        )
        return []

    def select_for_analysis(
        self,
        stock: StockRecord,
        *,
        now: datetime | None = None,
        fetch_items: Callable[..., list[NewsItem]] | None = None,
    ) -> AnalysisNewsContext:
        return select_news_for_analysis(stock, now=now, fetch_items=fetch_items)


news_service = NewsService()


def select_news_for_analysis(
    stock: StockRecord,
    *,
    now: datetime | None = None,
    fetch_items: Callable[..., list[NewsItem]] | None = None,
) -> AnalysisNewsContext:
    """Select compact, relevant headlines for AI analysis context.

    Issues a single upstream fetch covering the widest window, then prefers the
    last 7 calendar days in memory and widens to 30 only if nothing relevant is
    found. A failed fetch is never retried, so Refresh analysis stays bounded by
    one round of requests. Never raises: news is optional context.
    """
    as_of = _ensure_utc(now or datetime.now(UTC))
    fetch = fetch_items or _fetch_news_for_window

    try:
        fetched = list(fetch(stock, window_days=_ANALYSIS_FALLBACK_WINDOW_DAYS) or [])
    except Exception as exc:  # noqa: BLE001 - soft-fail path for analysis
        logger.warning(
            "Analysis news fetch failed",
            extra={
                "event": "news.analysis.fetch_failed",
                "provider": "google_news",
                "stock_id": stock.id,
                "window_days": _ANALYSIS_FALLBACK_WINDOW_DAYS,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return _unavailable_news_context(stock, "fetch_failed", fetched_count=0)

    if not fetched:
        return _unavailable_news_context(stock, "empty", fetched_count=0)

    for window_days in (_ANALYSIS_PRIMARY_WINDOW_DAYS, _ANALYSIS_FALLBACK_WINDOW_DAYS):
        window_start = as_of - timedelta(days=window_days)
        selected = _select_relevant_items(
            stock,
            fetched,
            window_start=window_start,
            window_end=as_of,
        )
        if not selected:
            continue
        logger.info(
            "Analysis news selection completed",
            extra={
                "event": "news.analysis.selected",
                "provider": "google_news",
                "stock_id": stock.id,
                "window_days": window_days,
                "fetched_count": len(fetched),
                "selected_count": len(selected),
            },
        )
        return AnalysisNewsContext(
            available=True,
            window_days=window_days,
            window_start=_to_iso_z(window_start),
            window_end=_to_iso_z(as_of),
            data_as_of=_to_iso_z(as_of),
            items=tuple(selected),
            fetched_count=len(fetched),
            selected_count=len(selected),
            failure_reason=None,
        )

    return _unavailable_news_context(stock, "no_relevant_items", fetched_count=len(fetched))


def _unavailable_news_context(
    stock: StockRecord,
    failure_reason: str,
    *,
    fetched_count: int,
) -> AnalysisNewsContext:
    logger.info(
        "Analysis news selection unavailable",
        extra={
            "event": "news.analysis.unavailable",
            "provider": "google_news",
            "stock_id": stock.id,
            "fetched_count": fetched_count,
            "selected_count": 0,
            "failure_reason": failure_reason,
            "window_days": _ANALYSIS_FALLBACK_WINDOW_DAYS,
        },
    )
    return AnalysisNewsContext(
        available=False,
        window_days=None,
        window_start=None,
        window_end=None,
        data_as_of=None,
        items=(),
        fetched_count=fetched_count,
        selected_count=0,
        failure_reason=failure_reason,
    )


def _fetch_news_for_window(stock: StockRecord, *, window_days: int) -> list[NewsItem]:
    """Fetch every query and merge. A generic first hit must not hide a later
    company-name query that actually mentions the stock.
    """
    queries = _build_queries(stock)
    profile = _market_news_profile(stock)
    merged: list[NewsItem] = []
    seen_urls: set[str] = set()
    for query in queries:
        items = _fetch_google_news(
            query,
            profile=profile,
            max_items=_ANALYSIS_MAX_FETCH,
            stock_id=stock.id,
            window_days=window_days,
            # The narrative language follows the UI (#212), so a publisher whose
            # name is in the other script is still usable evidence here.
            enforce_source_language=False,
        )
        for item in items:
            url = (item.url or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(item)
    return merged


def _select_relevant_items(
    stock: StockRecord,
    items: list[NewsItem],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[AnalysisNewsItem]:
    scored = [
        row
        for row in (
            _score_news_item(stock, item, window_start=window_start, window_end=window_end)
            for item in items
        )
        if row is not None
    ]
    scored.sort(key=lambda row: (-row[0], -row[1].timestamp()))
    return _dedupe_scored_items(scored)


def _score_news_item(
    stock: StockRecord,
    item: NewsItem,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[int, datetime, NewsItem] | None:
    """Normalized, in-window, on-topic item with its relevance score, or ``None``."""
    title = (item.title or "").strip()
    url = (item.url or "").strip()
    if not title or not _is_valid_http_url(url):
        return None

    published = _parse_iso_datetime(item.published_at)
    if published is None or published < window_start or published > window_end:
        return None

    score = _relevance_score(stock, title)
    if score <= 0:
        return None

    return (
        score,
        published,
        NewsItem(
            title=title,
            url=url,
            source=(item.source or "").strip() or "Google News",
            published_at=_to_iso_z(published),
        ),
    )


def _dedupe_scored_items(
    scored: list[tuple[int, datetime, NewsItem]],
) -> list[AnalysisNewsItem]:
    """Keep the highest-scoring copy of each distinct story, up to the cap."""
    selected: list[AnalysisNewsItem] = []
    seen_titles: set[str] = set()
    seen_tokens: list[frozenset[str]] = []
    for _score, _published, item in scored:
        normalized = _normalize_title(item.title)
        if not normalized or normalized in seen_titles:
            continue
        tokens = _title_similarity_tokens(normalized)
        if _is_syndicated_duplicate(tokens, seen_tokens):
            continue
        seen_titles.add(normalized)
        seen_tokens.append(tokens)
        selected.append(
            AnalysisNewsItem(
                id=f"N{len(selected) + 1}",
                title=item.title,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
            )
        )
        if len(selected) >= _ANALYSIS_MAX_SELECTED:
            break
    return selected


def _relevance_score(stock: StockRecord, title: str) -> int:
    name_matched = _title_matches_any_name(title, stock)
    symbol_matched = _title_contains_symbol(title, stock.symbol)
    require_name = _requires_name_match(stock.symbol)

    if require_name:
        if not name_matched:
            return 0
        return 3 if symbol_matched else 2

    if name_matched and symbol_matched:
        return 3
    if name_matched:
        return 2
    if symbol_matched:
        return 1
    return 0


def _requires_name_match(symbol: str) -> bool:
    normalized = (symbol or "").strip().upper()
    if len(normalized) <= 2:
        return True
    return normalized in _COMMON_OR_AMBIGUOUS_SYMBOLS


def _title_matches_any_name(title: str, stock: StockRecord) -> bool:
    candidates = (stock.name, stock.name_en, stock.name_zh_tw)
    return any(_title_contains_name(title, name) for name in candidates if name)


def _title_contains_name(title: str, name: str) -> bool:
    needle = name.strip()
    if len(needle) < 2:
        return False
    return needle.casefold() in title.casefold()


def _title_contains_symbol(title: str, symbol: str) -> bool:
    needle = (symbol or "").strip()
    if not needle:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])"
    return re.search(pattern, title, flags=re.IGNORECASE) is not None


def _normalize_title(title: str) -> str:
    lowered = _strip_publisher_suffix(title).casefold()
    without_punct = _TITLE_PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", without_punct).strip()


def _strip_publisher_suffix(title: str) -> str:
    """Drop the ``- Publisher`` tail Google News appends to syndicated copies."""
    match = _PUBLISHER_SUFFIX_RE.search(title)
    if match is None:
        return title.strip()
    trimmed = title[: match.start()].strip()
    return trimmed or title.strip()


def _title_similarity_tokens(normalized_title: str) -> frozenset[str]:
    """Comparable tokens: words for latin scripts, character bigrams for CJK."""
    if _contains_cjk(normalized_title):
        compact = normalized_title.replace(" ", "")
        return frozenset(compact[index : index + 2] for index in range(len(compact) - 1))
    return frozenset(token for token in normalized_title.split() if token)


def _is_syndicated_duplicate(
    tokens: frozenset[str],
    seen_tokens: list[frozenset[str]],
) -> bool:
    """Treat near-identical headlines as syndicated copies of one story."""
    if len(tokens) < _SYNDICATION_MIN_TOKENS:
        return False
    for seen in seen_tokens:
        if len(seen) < _SYNDICATION_MIN_TOKENS:
            continue
        union = tokens | seen
        if not union:
            continue
        if len(tokens & seen) / len(union) >= _SYNDICATION_SIMILARITY:
            return True
    return False


def _is_valid_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_iso_z(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(raw_value: str) -> datetime | None:
    if not raw_value or not raw_value.strip():
        return None
    text = raw_value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _build_queries(stock: StockRecord) -> list[str]:
    if stock.market == "TW":
        candidates = [
            f"{stock.symbol} 台股",
            f"{stock.symbol} {stock.name_zh_tw or stock.name}",
            f"{stock.symbol} {stock.name}",
        ]
        if stock.name_en:
            candidates.append(f"{stock.symbol} {stock.name_en}")
        return candidates

    candidates = [f"{stock.symbol} stock", f"{stock.symbol} {stock.name}"]
    if stock.name_en:
        candidates.append(f"{stock.symbol} {stock.name_en}")
    return candidates


def _fetch_google_news(
    query: str,
    profile: dict[str, str],
    max_items: int,
    *,
    stock_id: str,
    window_days: int = 1,
    enforce_source_language: bool = True,
) -> list[NewsItem]:
    started_at = perf_counter()
    params = {"q": f"{query} when:{window_days}d", **profile}
    request = Request(f"{_GOOGLE_NEWS_RSS_URL}?{urlencode(params)}")
    request.add_header("User-Agent", "Mozilla/5.0")
    request.add_header(
        "Accept",
        "application/rss+xml,application/xml,text/xml,*/*",
    )

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            xml_payload = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError) as exc:
        logger.warning(
            "Google News request failed",
            extra={
                "event": "news.fetch.failed",
                "provider": "google_news",
                "stock_id": stock_id,
                "query": query,
                "window_days": window_days,
                "duration_ms": elapsed_ms(started_at),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return []

    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError as exc:
        logger.warning(
            "Google News RSS parse failed",
            extra={
                "event": "news.fetch.parse_failed",
                "provider": "google_news",
                "stock_id": stock_id,
                "query": query,
                "window_days": window_days,
                "duration_ms": elapsed_ms(started_at),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return []

    now = datetime.now(UTC)
    if window_days <= 1:
        cutoff = now - timedelta(hours=_WINDOW_HOURS)
    else:
        cutoff = now - timedelta(days=window_days)
    news: list[NewsItem] = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "Google News").strip()
        published_raw = (item.findtext("pubDate") or "").strip()
        if not title or not url or not published_raw:
            continue

        published = _parse_pub_date(published_raw)
        if published is None or published < cutoff:
            continue
        if enforce_source_language and not _is_source_language_compatible(
            source,
            profile["ceid"],
        ):
            continue

        news.append(
            NewsItem(
                title=title,
                url=url,
                source=source,
                published_at=published.isoformat(),
            )
        )
        if len(news) >= max_items:
            break

    logger.info(
        "Google News fetch completed",
        extra={
            "event": "news.fetch.completed",
            "provider": "google_news",
            "stock_id": stock_id,
            "query": query,
            "window_days": window_days,
            "duration_ms": elapsed_ms(started_at),
            "item_count": len(news),
        },
    )
    return news


def _parse_pub_date(raw_value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _market_news_profile(stock: StockRecord) -> dict[str, str]:
    if stock.market == "TW":
        return {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
    return {"hl": "en-US", "gl": "US", "ceid": "US:en"}


def _is_source_language_compatible(source: str, ceid: str) -> bool:
    if ceid.endswith("zh-Hant"):
        return _contains_cjk(source)
    return not _contains_cjk(source)


def _contains_cjk(text: str) -> bool:
    start, end = _ZHTW_CHAR_RANGE
    return any(start <= char <= end for char in text)
