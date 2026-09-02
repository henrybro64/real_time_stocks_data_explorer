from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException

T = TypeVar("T")


def run_domain_action(
    action: Callable[[], T],
    *,
    not_found: type[Exception] | None = None,
    validation: type[Exception] | None = None,
) -> T:
    try:
        return action()
    except Exception as exc:
        if not_found is not None and isinstance(exc, not_found):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if validation is not None and isinstance(exc, validation):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
