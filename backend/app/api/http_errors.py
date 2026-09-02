from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException

T = TypeVar("T")


def run_domain_action(
    action: Callable[[], T],
    *,
    not_found: type[Exception] | tuple[type[Exception], ...],
    validation: type[Exception] | tuple[type[Exception], ...] = (),
) -> T:
    not_found_types = not_found if isinstance(not_found, tuple) else (not_found,)
    validation_types = validation if isinstance(validation, tuple) else (validation,)
    try:
        return action()
    except not_found_types as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except validation_types as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
