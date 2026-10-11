"""Classify database constraint failures that callers can turn into HTTP 400s."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def is_user_stock_unique_violation(
    exc: BaseException,
    *,
    constraint: str,
    table: str,
) -> bool:
    """True only for the per-user stock unique key, not foreign keys or nulls."""
    if not isinstance(exc, IntegrityError):
        return False
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint_name == constraint:
        return True
    message = " ".join(part for part in (str(orig or ""), str(exc)) if part).lower()
    if constraint.lower() in message:
        return True
    return "unique" in message and table.lower() in message and "stock_id" in message
