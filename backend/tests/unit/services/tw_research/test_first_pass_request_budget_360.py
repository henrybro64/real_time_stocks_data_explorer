"""Offline fixture-measured request budget for First-Pass (#360).

No live provider calls. Asserts TDCC open-data download coalescing and
warm margin store short-circuits.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.services import tw_ownership_concentration_service as own_mod
from app.services.http_client import (
    reset_provider_request_budget,
    snapshot_provider_request_budget,
)
from app.services.tw_margin_short_service import TaiwanMarginShortService
from app.services.tw_research.first_pass import collect_first_pass_batch
from tests.support import FakeQuoteSource, stock_record
from tests.unit.services.test_tw_margin_short_service import FakeMarginShortStore, _margin_stored_row
from tests.unit.services.tw_research.test_first_pass_budget_circuit_348_349 import (
    _candles,
    _good_inst,
    _good_own,
    _key_stats,
)
from tests.unit.services.tw_research.test_first_pass_deadline_hierarchy_360 import (
    _stock_loader,
)


def test_fixture_measured_request_budget_73_and_300(monkeypatch) -> None:
    own_mod.clear_tdcc_open_data_cache()
    reset_provider_request_budget()
    open_data_calls = {"n": 0}

    def fake_fetch_text(*_a, **_k) -> str:
        open_data_calls["n"] += 1
        return "資料日期,證券代號\n2026-07-04,2330\n"

    monkeypatch.setattr(own_mod, "fetch_text", fake_fetch_text)

    # Warm margin store for every symbol in the universe.
    store = FakeMarginShortStore()
    for size in (73, 300):
        open_data_calls["n"] = 0
        own_mod.clear_tdcc_open_data_cache()
        reset_provider_request_budget()
        stock_ids = [f"TW:{2000 + i}" for i in range(size)]
        for sid in stock_ids:
            symbol = sid.split(":")[-1]
            store.rows_by_key[("TWSE", symbol)] = [
                _margin_stored_row(
                    symbol=symbol,
                    trade_date=date(2026, 7, 8),
                )
            ]

        live_margin_calls = {"n": 0}

        def margin_loader(stock_id: str):
            stock = stock_record(
                stock_id,
                market="TW",
                symbol=stock_id.split(":")[-1],
                name=stock_id,
                exchange="TWSE",
                currency="TWD",
                timezone="Asia/Taipei",
            )
            service = TaiwanMarginShortService(
                quote_source=FakeQuoteSource(stock),
                twse_margin_fetcher=lambda _d: live_margin_calls.__setitem__(
                    "n", live_margin_calls["n"] + 1
                )
                or {"stat": "查無資料"},
                twse_borrowed_short_fetcher=lambda _d: {"stat": "查無資料"},
                twse_close_fetcher=lambda _d: {"stat": "查無資料"},
                now_provider=lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
                store=store,
            )
            return service.get_margin_short(stock_id, days=5)

        # Ownership loader that hits open-data once via the service helper.
        def ownership_loader(stock_id: str):
            del stock_id
            own_mod._fetch_tdcc_open_data_csv()
            return _good_own()

        collect_first_pass_batch(
            stock_ids,
            stock_loader=_stock_loader,
            candles_loader=lambda _s: _candles(),
            institutional_loader=lambda _s: _good_inst(),
            margin_loader=margin_loader,
            ownership_loader=ownership_loader,
            key_stats_loader=_key_stats,
            symbol_deadline_seconds=45.0,
            time_budget_seconds=10_000.0,
        )
        assert open_data_calls["n"] == 1, f"size={size} open-data downloads"
        assert live_margin_calls["n"] == 0, f"size={size} warm margin must skip live"
        budget = snapshot_provider_request_budget()
        # Printed for the doc as fixture-measured (no live crawl).
        print(f"fixture-measured size={size} provider_budget={budget}")
        print(
            f"fixture-measured size={size} tdcc_open_data_downloads={open_data_calls['n']} "
            f"margin_live={live_margin_calls['n']}"
        )

    own_mod.clear_tdcc_open_data_cache()
