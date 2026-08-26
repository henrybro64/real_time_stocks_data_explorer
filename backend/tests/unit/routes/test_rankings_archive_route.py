"""Unit tests for rankings archive capture route (#167)."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException
import pytest

from app.api.routes import rankings as rankings_route


def test_archive_capture_requires_configured_token(monkeypatch) -> None:
    monkeypatch.setattr(rankings_route.settings, "rankings_archive_capture_token", "")
    with pytest.raises(HTTPException) as exc:
        rankings_route.capture_rankings_archive()
    assert exc.value.status_code == 503


def test_archive_capture_rejects_bad_token(monkeypatch) -> None:
    monkeypatch.setattr(rankings_route.settings, "rankings_archive_capture_token", "secret")
    with pytest.raises(HTTPException) as exc:
        rankings_route.capture_rankings_archive(
            payload=None,
            x_rankings_archive_capture_token="wrong",
        )
    assert exc.value.status_code == 403


def test_archive_capture_delegates_to_service(monkeypatch) -> None:
    monkeypatch.setattr(rankings_route.settings, "rankings_archive_capture_token", "secret")
    captured: dict[str, object] = {}

    def fake_capture(**kwargs):
        captured.update(kwargs)
        return {"captured": 2, "skipped": 1, "failed": 0}

    monkeypatch.setattr(
        rankings_route.rankings_archive_service,
        "capture_eod_archives",
        fake_capture,
    )
    result = rankings_route.capture_rankings_archive(
        payload=rankings_route.RankingsArchiveCaptureRequest(
            market="TW",
            tradingDate=date(2026, 8, 18),
            force=True,
        ),
        x_rankings_archive_capture_token="secret",
    )
    assert result == {"captured": 2, "skipped": 1, "failed": 0}
    assert captured["market"] == "TW"
    assert captured["trading_date"] == date(2026, 8, 18)
    assert captured["force"] is True
    assert captured["trigger"] == "archive_capture_endpoint"
