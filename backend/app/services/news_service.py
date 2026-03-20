from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models.stock import StockRecord

_REQUEST_TIMEOUT_SECONDS = 8
_GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
_MAX_ITEMS = 12
_WINDOW_HOURS = 24
_ZHTW_CHAR_RANGE = ("\u4e00", "\u9fff")


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str


class NewsService:
    def get_recent_news(self, stock: StockRecord) -> list[NewsItem]:
        queries = _build_queries(stock)
        profile = _market_news_profile(stock)
        for query in queries:
            items = _fetch_google_news(
                query,
                profile=profile,
                max_items=_MAX_ITEMS,
            )
            if items:
                return items
        return []


news_service = NewsService()


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
) -> list[NewsItem]:
    params = {"q": f"{query} when:1d", **profile}
    request = Request(f"{_GOOGLE_NEWS_RSS_URL}?{urlencode(params)}")
    request.add_header("User-Agent", "Mozilla/5.0")
    request.add_header(
        "Accept",
        "application/rss+xml,application/xml,text/xml,*/*",
    )

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            xml_payload = response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError):
        return []

    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return []

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=_WINDOW_HOURS)
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
        if not _is_source_language_compatible(source, profile["ceid"]):
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
