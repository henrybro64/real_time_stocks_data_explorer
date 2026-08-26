from __future__ import annotations

import app.jobs.runtime as jobs_runtime_module
from app.jobs.runtime import start_enabled_background_jobs, stop_background_jobs


def test_start_enabled_background_jobs_skips_disabled_samplers(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "portfolio_history_sampler_enabled", False)
    monkeypatch.setattr(settings, "alert_evaluator_enabled", False)
    monkeypatch.setattr(settings, "rankings_snapshot_enabled", False)
    monkeypatch.setattr(settings, "taiwan_market_data_refresh_enabled", False)
    monkeypatch.setattr(settings, "quote_refresher_enabled", False)
    monkeypatch.setattr(settings, "rankings_archive_eod_enabled", False)

    started = {
        "portfolio": [],
        "alert": [],
        "rankings": [],
        "taiwan": [],
        "quotes": [],
        "archive": [],
    }
    monkeypatch.setattr(
        jobs_runtime_module.portfolio_history_service,
        "start_background_sampler",
        lambda poll_seconds: started["portfolio"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.alert_service,
        "start_background_evaluator",
        lambda poll_seconds: started["alert"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_snapshot_service,
        "start_background_sampler",
        lambda poll_seconds: started["rankings"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.taiwan_market_data_refresh_service,
        "start_background_sampler",
        lambda poll_seconds, **kwargs: started["taiwan"].append((poll_seconds, kwargs)),
    )
    monkeypatch.setattr(
        jobs_runtime_module.quote_refresher_service,
        "start_background_sampler",
        lambda: started["quotes"].append(True),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_archive_service,
        "start_background_sampler",
        lambda poll_seconds: started["archive"].append(poll_seconds),
    )

    start_enabled_background_jobs()

    assert started == {
        "portfolio": [],
        "alert": [],
        "rankings": [],
        "taiwan": [],
        "quotes": [],
        "archive": [],
    }


def test_start_enabled_background_jobs_starts_enabled_samplers(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "portfolio_history_sampler_enabled", True)
    monkeypatch.setattr(settings, "portfolio_history_poll_seconds", 111)
    monkeypatch.setattr(settings, "alert_evaluator_enabled", True)
    monkeypatch.setattr(settings, "alert_evaluator_poll_seconds", 222)
    monkeypatch.setattr(settings, "rankings_snapshot_enabled", True)
    monkeypatch.setattr(settings, "rankings_refresh_seconds", 333)
    monkeypatch.setattr(settings, "taiwan_market_data_refresh_enabled", True)
    monkeypatch.setattr(settings, "taiwan_market_data_refresh_poll_seconds", 444)
    monkeypatch.setattr(settings, "taiwan_market_data_refresh_hour", 18)
    monkeypatch.setattr(settings, "taiwan_market_data_refresh_recent_days", 7)
    monkeypatch.setattr(settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(settings, "rankings_archive_eod_enabled", True)
    monkeypatch.setattr(settings, "rankings_archive_eod_poll_seconds", 555)

    started = {
        "portfolio": [],
        "alert": [],
        "rankings": [],
        "taiwan": [],
        "quotes": [],
        "archive": [],
    }
    monkeypatch.setattr(
        jobs_runtime_module.portfolio_history_service,
        "start_background_sampler",
        lambda poll_seconds: started["portfolio"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.alert_service,
        "start_background_evaluator",
        lambda poll_seconds: started["alert"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_snapshot_service,
        "start_background_sampler",
        lambda poll_seconds: started["rankings"].append(poll_seconds),
    )
    monkeypatch.setattr(
        jobs_runtime_module.taiwan_market_data_refresh_service,
        "start_background_sampler",
        lambda poll_seconds, **kwargs: started["taiwan"].append((poll_seconds, kwargs)),
    )
    monkeypatch.setattr(
        jobs_runtime_module.quote_refresher_service,
        "start_background_sampler",
        lambda: started["quotes"].append(True),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_archive_service,
        "start_background_sampler",
        lambda poll_seconds: started["archive"].append(poll_seconds),
    )

    start_enabled_background_jobs()

    assert started == {
        "portfolio": [111],
        "alert": [222],
        "rankings": [333],
        "taiwan": [(444, {"refresh_hour": 18, "recent_days": 7})],
        "quotes": [True],
        "archive": [555],
    }


def test_stop_background_jobs_stops_all_samplers(monkeypatch) -> None:
    stopped = {
        "portfolio": 0,
        "alert": 0,
        "rankings": 0,
        "taiwan": 0,
        "quotes": 0,
        "archive": 0,
    }
    monkeypatch.setattr(
        jobs_runtime_module.portfolio_history_service,
        "stop_background_sampler",
        lambda: stopped.__setitem__("portfolio", stopped["portfolio"] + 1),
    )
    monkeypatch.setattr(
        jobs_runtime_module.alert_service,
        "stop_background_evaluator",
        lambda: stopped.__setitem__("alert", stopped["alert"] + 1),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_snapshot_service,
        "stop_background_sampler",
        lambda: stopped.__setitem__("rankings", stopped["rankings"] + 1),
    )
    monkeypatch.setattr(
        jobs_runtime_module.taiwan_market_data_refresh_service,
        "stop_background_sampler",
        lambda: stopped.__setitem__("taiwan", stopped["taiwan"] + 1),
    )
    monkeypatch.setattr(
        jobs_runtime_module.quote_refresher_service,
        "stop_background_sampler",
        lambda: stopped.__setitem__("quotes", stopped["quotes"] + 1),
    )
    monkeypatch.setattr(
        jobs_runtime_module.rankings_archive_service,
        "stop_background_sampler",
        lambda: stopped.__setitem__("archive", stopped["archive"] + 1),
    )

    stop_background_jobs()

    assert stopped == {
        "portfolio": 1,
        "alert": 1,
        "rankings": 1,
        "taiwan": 1,
        "quotes": 1,
        "archive": 1,
    }
