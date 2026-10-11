"""A provider quote batch that stopped before every symbol was requested."""

from __future__ import annotations

from app.models.quote import QuoteRecord
from app.services.http_client import HttpFetchError


class QuoteBatchInterrupted(Exception):
    """Batch stopped early (#268, #271).

    ``quotes`` are the rows already parsed and must be kept. ``deferred_ids``
    must not fall through to a per-symbol fetch in the same attempt.
    """

    def __init__(
        self,
        *,
        quotes: dict[str, QuoteRecord],
        deferred_ids: list[str],
        error: HttpFetchError | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__("quote batch interrupted")
        self.quotes = quotes
        self.deferred_ids = list(deferred_ids)
        self.error = error
        self.timed_out = timed_out
