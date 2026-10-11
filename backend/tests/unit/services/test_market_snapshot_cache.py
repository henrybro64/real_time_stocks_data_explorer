from app.services.market_snapshot_cache import (
    _refresh_gates,
    clear_market_snapshot_cache_for_tests,
    run_snapshot_refresh_singleflight,
)


def test_completed_singleflight_gate_is_removed() -> None:
    clear_market_snapshot_cache_for_tests()
    assert run_snapshot_refresh_singleflight("rankings:test", lambda: 7) == 7
    assert "rankings:test" not in _refresh_gates
