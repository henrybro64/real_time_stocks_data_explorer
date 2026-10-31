"""Classify database constraint failures that callers can turn into HTTP 400s."""

from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

_SAFE_CONSTRAINT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
_QUOTED_CONSTRAINT_NAME = re.compile(
    r'constraint "([A-Za-z_][A-Za-z0-9_]{0,62})"',
    re.IGNORECASE,
)


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


def is_username_unique_violation(exc: BaseException) -> bool:
    """True when ``users.username`` already exists, not for other constraint failures."""
    if not isinstance(exc, IntegrityError):
        return False
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint_name == "users_username_key":
        return True
    message = " ".join(part for part in (str(orig or ""), str(exc)) if part).lower()
    if "users_username_key" in message:
        return True
    return "unique" in message and "users" in message and "username" in message


def is_oauth_identity_unique_violation(exc: BaseException) -> bool:
    """True when ``(provider, subject)`` already exists, not for FK failures."""
    if not isinstance(exc, IntegrityError):
        return False
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint_name == "uq_oauth_identities_provider_subject":
        return True
    message = " ".join(part for part in (str(orig or ""), str(exc)) if part).lower()
    if "uq_oauth_identities_provider_subject" in message:
        return True
    return (
        "unique" in message
        and "oauth_identities" in message
        and "provider" in message
        and "subject" in message
    )


def safe_constraint_name(exc: BaseException | None) -> str | None:
    """Constraint identifier only. Never the SQL text or the DETAIL line."""
    if exc is None:
        return None
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None) if diag is not None else None
    if isinstance(name, str) and _SAFE_CONSTRAINT_NAME.fullmatch(name):
        return name
    message = " ".join(part for part in (str(orig or ""), str(exc)) if part)
    match = _QUOTED_CONSTRAINT_NAME.search(message)
    if match is None:
        return None
    return match.group(1)
