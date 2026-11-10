"""The Next.js rewrite proxy is sized against this default, not the route maximum."""

import pytest
from pydantic import ValidationError

from app.schemas.tw_research import RunBatchRequest


def test_default_batch_budget_is_two_minutes() -> None:
    request = RunBatchRequest(scanId="scan-1")
    assert request.timeoutSeconds == 120


def test_batch_budget_rejects_more_than_ten_minutes() -> None:
    with pytest.raises(ValidationError):
        RunBatchRequest(scanId="scan-1", timeoutSeconds=601)
