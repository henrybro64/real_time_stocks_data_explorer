"""Shared TW-research unit fixtures."""

from __future__ import annotations

import pytest

from app.services.tw_calendar_preload import reset_for_tests


@pytest.fixture(autouse=True)
def _default_tw_calendars_known(monkeypatch: pytest.MonkeyPatch):
    """Avoid network holiday fetches; assume calendars known unless a test overrides."""
    reset_for_tests()
    monkeypatch.setattr(
        "app.services.tw_research.report_evidence.ensure_tw_calendars_bounded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_calendars_bounded",
        lambda years: True,
    )
