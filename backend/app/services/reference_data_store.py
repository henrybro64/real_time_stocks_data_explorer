"""Postgres snapshots for stock catalogs and the Taiwan holiday calendar (#264)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import logging

from app.models.stock import StockRecord
from app.repositories.reference_snapshot_repository import reference_snapshot_repository

logger = logging.getLogger(__name__)

CATALOG_TW_KEY = "catalog:TW"
CATALOG_US_KEY = "catalog:US"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def load_catalog_snapshot(
    cache_key: str,
) -> tuple[dict[str, StockRecord], bool, datetime] | None:
    record = _read_record(cache_key)
    if record is None:
        return None
    payload, updated_at = record
    stocks: dict[str, StockRecord] = {}
    for item in payload.get("stocks") or []:
        stock = _stock_from_item(item)
        if stock is not None:
            stocks[stock.id] = stock
    if not stocks:
        return None
    return stocks, bool(payload.get("degraded")), _aware(updated_at)


def save_catalog_snapshot(
    cache_key: str,
    stocks: dict[str, StockRecord],
    *,
    degraded: bool,
) -> None:
    _write_record(
        cache_key,
        {
            "degraded": degraded,
            "stocks": [asdict(stock) for stock in stocks.values()],
        },
    )


def load_holiday_snapshot(year: int) -> tuple[frozenset[date], datetime] | None:
    record = _read_record(f"holiday:TW:{year}")
    if record is None:
        return None
    payload, updated_at = record
    holidays: set[date] = set()
    for raw in payload.get("dates") or []:
        try:
            holidays.add(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            continue
    return frozenset(holidays), _aware(updated_at)


def save_holiday_snapshot(year: int, holidays: frozenset[date]) -> None:
    _write_record(
        f"holiday:TW:{year}",
        {"dates": sorted(day.isoformat() for day in holidays)},
    )


def _stock_from_item(item: object) -> StockRecord | None:
    if not isinstance(item, dict):
        return None
    try:
        return StockRecord(
            id=str(item["id"]),
            market=str(item["market"]),
            symbol=str(item["symbol"]),
            name=str(item["name"]),
            exchange=str(item["exchange"]),
            currency=str(item["currency"]),
            timezone=str(item["timezone"]),
            name_en=item.get("name_en"),
            name_zh_tw=item.get("name_zh_tw"),
        )
    except (KeyError, TypeError):
        return None


def _read_record(cache_key: str) -> tuple[dict, datetime] | None:
    if not reference_snapshot_repository.is_available():
        return None
    try:
        return reference_snapshot_repository.get_record(cache_key)
    except Exception as exc:  # noqa: BLE001 - missing table or a down database
        logger.debug(
            "Reference snapshot read failed",
            extra={
                "event": "reference_snapshot.read_failed",
                "cache_key": cache_key,
                "error_type": type(exc).__name__,
            },
        )
        return None


def _write_record(cache_key: str, payload: dict) -> None:
    if not reference_snapshot_repository.is_available():
        return
    try:
        reference_snapshot_repository.put_payload(cache_key, payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Reference snapshot write failed",
            extra={
                "event": "reference_snapshot.write_failed",
                "cache_key": cache_key,
                "error_type": type(exc).__name__,
            },
        )
