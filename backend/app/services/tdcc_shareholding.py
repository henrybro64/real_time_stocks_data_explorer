"""Parse TDCC 集保戶股權分散表 CSV / HTML / row payloads."""

from __future__ import annotations

import csv
import html as html_lib
import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from app.services.taiwan_market_data import normalize_taiwan_date, parse_taiwan_number

# Standard TDCC tiers: level 12 starts at 400,001 shares (400 張以上).
_MIN_LEVEL_400_LOTS = 12
_MAX_TIER_LEVEL = 15
_TOTAL_LEVEL_MARKERS = {16, 17, 999}
_PERCENT_HEADERS = (
    "占集保庫存數比例%",
    "佔集保庫存數比例%",
    "占集保庫存數比例",
    "佔集保庫存數比例",
)
_DATE_HEADERS = ("資料日期", "日期")
_SYMBOL_HEADERS = ("證券代號", "股票代號")
_LEVEL_HEADERS = ("持股分級", "分級")
_SHARES_HEADERS = ("股數", "持有股數")


@dataclass(frozen=True)
class TdccShareholdingTierRow:
    date: str
    symbol: str
    level: int
    holders: float | None
    shares: float | None
    percent: float | None


@dataclass(frozen=True)
class TdccConcentrationPoint:
    date: str
    symbol: str
    holders_400_lots_or_more_percent: float | None


def parse_tdcc_shareholding_csv(text: str, *, symbol: str | None = None) -> list[TdccConcentrationPoint]:
    """Parse open-data CSV text into per-date concentration points.

    Malformed rows are skipped. When ``symbol`` is set, only that ticker is kept.
    """
    if not isinstance(text, str) or not text.strip():
        return []

    cleaned = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(cleaned))
    if reader.fieldnames is None:
        return []

    field_map = {_normalize_header(name): name for name in reader.fieldnames if name}
    date_key = _first_mapped(field_map, _DATE_HEADERS)
    symbol_key = _first_mapped(field_map, _SYMBOL_HEADERS)
    level_key = _first_mapped(field_map, _LEVEL_HEADERS)
    shares_key = _first_mapped(field_map, _SHARES_HEADERS)
    percent_key = _first_mapped(field_map, _PERCENT_HEADERS)
    if date_key is None or symbol_key is None or level_key is None:
        return []

    wanted = _normalize_symbol(symbol) if symbol else None
    tiers: list[TdccShareholdingTierRow] = []
    for raw in reader:
        try:
            tier = _parse_tier_row(
                raw,
                date_key=date_key,
                symbol_key=symbol_key,
                level_key=level_key,
                shares_key=shares_key,
                percent_key=percent_key,
            )
        except (TypeError, ValueError, KeyError):
            continue
        if tier is None:
            continue
        if wanted is not None and tier.symbol != wanted:
            continue
        tiers.append(tier)

    return concentrate_tiers(tiers)


def parse_tdcc_shareholding_rows(
    rows: list[dict[str, object]] | list[list[object]],
    *,
    fields: list[str] | None = None,
    symbol: str | None = None,
) -> list[TdccConcentrationPoint]:
    """Parse list-of-dict or list-of-list rows into concentration points."""
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return _parse_tdcc_dict_rows([row for row in rows if isinstance(row, dict)], symbol=symbol)
    if fields is None:
        return []
    return _parse_tdcc_list_rows(rows, fields=fields, symbol=symbol)


def _parse_tdcc_dict_rows(
    dict_rows: list[dict[str, object]],
    *,
    symbol: str | None,
) -> list[TdccConcentrationPoint]:
    if not dict_rows:
        return []
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(dict_rows[0].keys()))
    writer.writeheader()
    for row in dict_rows:
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return parse_tdcc_shareholding_csv(buffer.getvalue(), symbol=symbol)


def _parse_tdcc_list_rows(
    rows: list[dict[str, object]] | list[list[object]],
    *,
    fields: list[str],
    symbol: str | None,
) -> list[TdccConcentrationPoint]:
    field_map = {_normalize_header(name): index for index, name in enumerate(fields)}
    date_idx = _first_index(field_map, _DATE_HEADERS)
    symbol_idx = _first_index(field_map, _SYMBOL_HEADERS)
    level_idx = _first_index(field_map, _LEVEL_HEADERS)
    shares_idx = _first_index(field_map, _SHARES_HEADERS)
    percent_idx = _first_index(field_map, _PERCENT_HEADERS)
    if date_idx is None or symbol_idx is None or level_idx is None:
        return []

    wanted = _normalize_symbol(symbol) if symbol else None
    tiers: list[TdccShareholdingTierRow] = []
    for raw in rows:
        if not isinstance(raw, (list, tuple)):
            continue
        tier = _tier_from_list_row(
            raw,
            date_idx=date_idx,
            symbol_idx=symbol_idx,
            level_idx=level_idx,
            shares_idx=shares_idx,
            percent_idx=percent_idx,
        )
        if tier is None:
            continue
        if wanted is not None and tier.symbol != wanted:
            continue
        tiers.append(tier)
    return concentrate_tiers(tiers)


def _tier_from_list_row(
    raw: list[object] | tuple[object, ...],
    *,
    date_idx: int,
    symbol_idx: int,
    level_idx: int,
    shares_idx: int | None,
    percent_idx: int | None,
) -> TdccShareholdingTierRow | None:
    try:
        return _parse_tier_values(
            date_value=raw[date_idx] if date_idx < len(raw) else None,
            symbol_value=raw[symbol_idx] if symbol_idx < len(raw) else None,
            level_value=raw[level_idx] if level_idx < len(raw) else None,
            shares_value=raw[shares_idx] if shares_idx is not None and shares_idx < len(raw) else None,
            percent_value=(
                raw[percent_idx] if percent_idx is not None and percent_idx < len(raw) else None
            ),
        )
    except (TypeError, ValueError, IndexError):
        return None


def parse_tdcc_qry_stock_html(
    text: str,
    *,
    symbol: str,
    point_date: str,
) -> TdccConcentrationPoint | None:
    """Parse one official TDCC portal HTML result table into a concentration point.

    The portal table uses human-readable tier labels with an ordinal ``序`` column.
    Levels 12–15 are ``400張以上`` (400,001 shares and above).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    normalized_date = normalize_taiwan_date(point_date)
    normalized_symbol = _normalize_symbol(symbol)
    if normalized_date is None or normalized_symbol is None:
        return None

    table_html = _extract_shareholding_table_html(text)
    if table_html is None:
        return None

    rows = _HtmlTableExtractor().extract(table_html)
    if not rows:
        return None

    tiers: list[TdccShareholdingTierRow] = []
    for row in rows:
        if len(row) < 5:
            continue
        level = _parse_level(row[0])
        if level is None:
            continue
        tiers.append(
            TdccShareholdingTierRow(
                date=normalized_date,
                symbol=normalized_symbol,
                level=level,
                holders=parse_taiwan_number(row[2]),
                shares=parse_taiwan_number(row[3]),
                percent=parse_taiwan_number(row[4]),
            )
        )
    if not tiers:
        return None
    points = concentrate_tiers(tiers)
    return points[0] if points else None


def extract_tdcc_synchronizer_token(text: str) -> str | None:
    """Read SYNCHRONIZER_TOKEN from a TDCC portal HTML page."""
    if not isinstance(text, str):
        return None
    match = re.search(
        r'name=["\']SYNCHRONIZER_TOKEN["\'][^>]*value=["\']([^"\']+)["\']',
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip() or None
    match = re.search(
        r'value=["\']([^"\']+)["\'][^>]*name=["\']SYNCHRONIZER_TOKEN["\']',
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def extract_tdcc_available_dates(text: str) -> list[str]:
    """Return YYYY-MM-DD dates from the portal ``scaDate`` select options."""
    if not isinstance(text, str):
        return []
    raw_values = re.findall(
        r'<option[^>]*value=["\'](\d{8})["\']',
        text,
        flags=re.IGNORECASE,
    )
    dates: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        normalized = normalize_taiwan_date(raw)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        dates.append(normalized)
    return dates


def concentrate_tiers(tiers: list[TdccShareholdingTierRow]) -> list[TdccConcentrationPoint]:
    """Aggregate tier rows into one concentration point per date+symbol."""
    grouped: dict[tuple[str, str], list[TdccShareholdingTierRow]] = {}
    for tier in tiers:
        grouped.setdefault((tier.date, tier.symbol), []).append(tier)

    points: list[TdccConcentrationPoint] = []
    for (point_date, point_symbol), group in grouped.items():
        percent = _holders_400_lots_or_more_percent(group)
        points.append(
            TdccConcentrationPoint(
                date=point_date,
                symbol=point_symbol,
                holders_400_lots_or_more_percent=percent,
            )
        )

    # Points are already unique on (date, symbol); sort ascending for stable output.
    return sorted(points, key=lambda item: (item.date, item.symbol))


def _holders_400_lots_or_more_percent(tiers: list[TdccShareholdingTierRow]) -> float | None:
    # Last row wins per level so duplicated CSV blocks do not double-count.
    by_level: dict[int, TdccShareholdingTierRow] = {tier.level: tier for tier in tiers}
    unique_tiers = list(by_level.values())
    eligible = [
        tier
        for tier in unique_tiers
        if tier.level not in _TOTAL_LEVEL_MARKERS and _MIN_LEVEL_400_LOTS <= tier.level <= _MAX_TIER_LEVEL
    ]
    if not eligible:
        return None

    percents = [tier.percent for tier in eligible if tier.percent is not None]
    percent_sum = sum(percents) if percents else None
    if percent_sum is not None and len(percents) == len(eligible) and percent_sum > 0:
        return round(percent_sum, 2)

    total_shares = _total_shares(unique_tiers)
    eligible_shares = [tier.shares for tier in eligible]
    if (
        total_shares is not None
        and total_shares > 0
        and all(shares is not None for shares in eligible_shares)
    ):
        return round(float(sum(eligible_shares)) / total_shares * 100.0, 2)  # type: ignore[arg-type]

    if percent_sum is not None and len(percents) == len(eligible):
        return round(percent_sum, 2)
    if percent_sum is not None and percents:
        return round(percent_sum, 2)
    return None


def _total_shares(tiers: list[TdccShareholdingTierRow]) -> float | None:
    for tier in tiers:
        if tier.level in _TOTAL_LEVEL_MARKERS and tier.shares is not None and tier.shares > 0:
            return tier.shares
    shares = [tier.shares for tier in tiers if tier.level not in _TOTAL_LEVEL_MARKERS and tier.shares is not None]
    if not shares:
        return None
    return float(sum(shares))


def _parse_tier_row(
    raw: dict[str, object],
    *,
    date_key: str,
    symbol_key: str,
    level_key: str,
    shares_key: str | None,
    percent_key: str | None,
) -> TdccShareholdingTierRow | None:
    return _parse_tier_values(
        date_value=raw.get(date_key),
        symbol_value=raw.get(symbol_key),
        level_value=raw.get(level_key),
        shares_value=raw.get(shares_key) if shares_key else None,
        percent_value=raw.get(percent_key) if percent_key else None,
    )


def _parse_tier_values(
    *,
    date_value: object,
    symbol_value: object,
    level_value: object,
    shares_value: object,
    percent_value: object,
) -> TdccShareholdingTierRow | None:
    point_date = normalize_taiwan_date(date_value)
    symbol = _normalize_symbol(symbol_value)
    level = _parse_level(level_value)
    if point_date is None or symbol is None or level is None:
        return None
    return TdccShareholdingTierRow(
        date=point_date,
        symbol=symbol,
        level=level,
        holders=None,
        shares=parse_taiwan_number(shares_value),
        percent=parse_taiwan_number(percent_value),
    )


def _parse_level(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return int(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def _normalize_symbol(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    if "." in text:
        text = text.split(".", 1)[0].strip()
    return text or None


def _normalize_header(value: object) -> str:
    return str(value).replace("\ufeff", "").strip()


def _first_mapped(field_map: dict[str, str], labels: tuple[str, ...]) -> str | None:
    for label in labels:
        key = field_map.get(_normalize_header(label))
        if key is not None:
            return key
    return None


def _first_index(field_map: dict[str, int], labels: tuple[str, ...]) -> int | None:
    for label in labels:
        index = field_map.get(_normalize_header(label))
        if index is not None:
            return index
    return None


def _extract_shareholding_table_html(text: str) -> str | None:
    """Return the first HTML table that looks like a TDCC shareholding result."""
    lower = text.lower()
    search_from = 0
    while True:
        start = lower.find("<table", search_from)
        if start < 0:
            return None
        end = lower.find("</table>", start)
        if end < 0:
            return None
        end += len("</table>")
        table = text[start:end]
        if "占集保" in table or "佔集保" in table or "持股" in table:
            return table
        search_from = end


class _HtmlTableExtractor(HTMLParser):
    """Minimal table extractor: returns rows of cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_cell = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []

    def extract(self, table_html: str) -> list[list[str]]:
        self.feed(table_html)
        self.close()
        return self.rows

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._current_row = []
        elif lowered in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._in_cell:
            text = html_lib.unescape("".join(self._current_cell))
            text = re.sub(r"\s+", " ", text).strip()
            self._current_row.append(text)
            self._in_cell = False
            self._current_cell = []
        elif lowered == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)
