"""Canonical ``MARKET:SYMBOL`` ids stored on user-owned rows (#275)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import text


def canonical_stored_stock_id(stock_id: str) -> str:
    """Fold a stored id the same way quote lookup does.

    The market prefix is uppercased. U.S. symbols are uppercased so ``US:aapl``
    and ``US:AAPL`` are one id. Other markets keep the symbol text.
    """
    raw = stock_id.strip()
    if ":" not in raw:
        return raw
    market, symbol = raw.split(":", 1)
    market = market.strip().upper()
    symbol = symbol.strip()
    if not market or not symbol:
        return raw
    if market == "US":
        symbol = symbol.upper()
    return f"{market}:{symbol}"


def removal_stock_ids(stock_id: str, resolved_id: str | None) -> list[str]:
    """Ids to delete so a raw request removes the canonical row."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        ordered.append(cleaned)

    add(stock_id)
    add(resolved_id)
    add(canonical_stored_stock_id(stock_id))
    return ordered


def collapse_mixed_case_stock_ids(connection: Any) -> None:
    """Rewrite legacy mixed-case ids so each user has one row per symbol."""
    _collapse_watchlist(connection)
    _collapse_positions(connection)


def _collapse_watchlist(connection: Any) -> None:
    rows = connection.execute(
        text("SELECT user_id, stock_id FROM watchlist_items")
    ).all()
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for user_id, stock_id in rows:
        grouped[(str(user_id), canonical_stored_stock_id(str(stock_id)))].append(
            str(stock_id)
        )

    for (user_id, canonical_id), stock_ids in grouped.items():
        distinct = list(dict.fromkeys(stock_ids))
        if distinct == [canonical_id]:
            continue
        if canonical_id not in distinct:
            keeper = distinct[0]
            connection.execute(
                text(
                    "UPDATE watchlist_items SET stock_id = :canonical_id "
                    "WHERE user_id = :user_id AND stock_id = :stock_id"
                ),
                {
                    "canonical_id": canonical_id,
                    "user_id": user_id,
                    "stock_id": keeper,
                },
            )
            extras = distinct[1:]
        else:
            extras = [stock_id for stock_id in distinct if stock_id != canonical_id]
        for extra_id in extras:
            connection.execute(
                text(
                    "DELETE FROM watchlist_items "
                    "WHERE user_id = :user_id AND stock_id = :stock_id"
                ),
                {"user_id": user_id, "stock_id": extra_id},
            )


def _collapse_positions(connection: Any) -> None:
    rows = connection.execute(
        text(
            "SELECT id, user_id, stock_id, quantity, average_cost "
            "FROM portfolio_positions"
        )
    ).all()
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.user_id), canonical_stored_stock_id(str(row.stock_id)))].append(
            row
        )

    for (user_id, canonical_id), positions in grouped.items():
        if len(positions) == 1 and str(positions[0].stock_id) == canonical_id:
            continue
        keeper = next(
            (row for row in positions if str(row.stock_id) == canonical_id),
            positions[0],
        )
        quantity = float(keeper.quantity)
        average_cost = float(keeper.average_cost)
        for other in positions:
            if other.id == keeper.id:
                continue
            other_quantity = float(other.quantity)
            total_quantity = quantity + other_quantity
            if total_quantity != 0:
                average_cost = (
                    quantity * average_cost + other_quantity * float(other.average_cost)
                ) / total_quantity
            quantity = total_quantity
            connection.execute(
                text("DELETE FROM portfolio_positions WHERE id = :position_id"),
                {"position_id": other.id},
            )
        connection.execute(
            text(
                "UPDATE portfolio_positions "
                "SET stock_id = :canonical_id, quantity = :quantity, "
                "average_cost = :average_cost "
                "WHERE id = :position_id AND user_id = :user_id"
            ),
            {
                "canonical_id": canonical_id,
                "quantity": quantity,
                "average_cost": average_cost,
                "position_id": keeper.id,
                "user_id": user_id,
            },
        )
