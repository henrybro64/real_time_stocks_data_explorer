from datetime import datetime, timezone
from time import monotonic, sleep

import pytest

from app.schemas.rankings import RankingSourceType, RankingType
from app.services.rankings_service import RankingsService
from app.services.tw_price_limit_rankings import (
    TW_PRICE_LIMIT_PARTIAL_WARNING,
    TW_PRICE_LIMIT_SOURCE,
    TW_PRICE_LIMIT_UNAVAILABLE_WARNING,
    US_PRICE_LIMIT_UNSUPPORTED_REASON,
    TwPriceLimitRankingsSource,
)
from app.services.tw_price_limits import limit_down_price, limit_up_price
from decimal import Decimal


def _row(
    symbol: str,
    *,
    exchange: str = "TWSE",
    last: str,
    reference: str,
    limit_up: str | None,
    limit_down: str | None,
    volume: str = "1000",
    name: str | None = None,
) -> dict[str, str]:
    return {
        "c": symbol,
        "n": name or f"名稱{symbol}",
        "ex": "otc" if exchange == "TPEx" else "tse",
        "z": last,
        "y": reference,
        "u": limit_up if limit_up is not None else "-",
        "w": limit_down if limit_down is not None else "-",
        "v": volume,
        "d": "20260924",
        "t": "13:30:00",
    }


def _source(rows: list[dict[str, str]], *, error: Exception | None = None) -> tuple[
    TwPriceLimitRankingsSource,
    dict[str, int],
]:
    calls = {"mis": 0, "symbols": 0}

    def symbol_loader():
        calls["symbols"] += 1
        return {
            "TWSE": ("2330", "1101", "0050", "1709"),
            "TPEx": ("6488", "8299", "8084"),
        }

    def mis_fetcher(_ex_chs: list[str]) -> list[dict[str, str]]:
        calls["mis"] += 1
        if error is not None:
            raise error
        return rows

    source = TwPriceLimitRankingsSource(
        symbol_loader=symbol_loader,
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    return source, calls


def test_rankings_use_limit_prices_not_rounded_percent() -> None:
    rows = [
        _row("2330", last="110.00", reference="100", limit_up="110.50", limit_down="90"),
        _row("1101", last="10.05", reference="9.15", limit_up="10.05", limit_down="8.24"),
        _row("6488", last="90", reference="100", limit_up="110", limit_down="90", exchange="TPEx"),
        _row("8299", last="-", reference="50", limit_up="55", limit_down="45", exchange="TPEx"),
    ]
    source, _calls = _source(rows)
    now = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)

    limit_up = source.fetch(RankingType.limit_up, "all", now)
    limit_down = source.fetch(RankingType.limit_down, "TPEx", now)

    assert [quote.symbol for quote in limit_up.quotes] == ["1101"]
    assert limit_up.quotes[0].status == "limit_up"
    assert limit_up.quotes[0].name == "名稱1101"
    assert [quote.symbol for quote in limit_down.quotes] == ["6488"]
    assert limit_down.quotes[0].exchange == "TPEx"
    assert limit_up.unavailable is False


def test_live_rows_with_blank_z_use_the_latest_trade() -> None:
    """Live MIS snapshots leave ``z`` as "-" between trades; ``trade`` holds the last one (#258)."""
    at_limit = _row("1101", last="-", reference="9.15", limit_up="10.05", limit_down="8.24")
    at_limit["t"] = "10:46:45"
    at_limit["trade"] = {"t": "10:46:18", "v": 3, "z": "10.0500", "ft": 20}
    no_trade = _row("2330", last="-", reference="100", limit_up="110", limit_down="90")
    reference_only = _row("6488", last="-", reference="110", limit_up="121", limit_down="99", exchange="TPEx")
    reference_only["pz"] = "110.00"
    source, _calls = _source([at_limit, no_trade, reference_only])

    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc))

    assert [quote.symbol for quote in result.quotes] == ["1101"]
    assert result.quotes[0].last_price == Decimal("10.05")
    assert result.quotes[0].timestamp.startswith("2026-09-24T10:46:18")


def test_missing_limits_use_tick_calculation_and_skip_no_trade_rows() -> None:
    reference = Decimal("9.15")
    up = limit_up_price(reference, kind="equity")
    down = limit_down_price(reference, kind="equity")
    rows = [
        _row("1101", last=format(up, "f"), reference="9.15", limit_up=None, limit_down=None),
        _row("2330", last="10.06", reference="9.15", limit_up=None, limit_down=None),
        _row("0050", last="-", reference="112.40", limit_up=None, limit_down=None),
    ]
    source, _calls = _source(rows)

    result = source.fetch(RankingType.limit_up, "TWSE", datetime(2026, 9, 26, tzinfo=timezone.utc))

    assert [quote.symbol for quote in result.quotes] == ["1101"]
    assert result.quotes[0].limit_up_price == up
    assert result.quotes[0].limit_down_price == down
    assert result.quotes[0].reference_price == reference


def test_board_filter_sort_and_separate_cache_keys() -> None:
    rows = [
        _row("2330", last="110", reference="100", limit_up="110", limit_down="90", volume="100"),
        _row("1101", last="55", reference="50", limit_up="55", limit_down="45", volume="5000"),
        _row(
            "6488",
            last="110",
            reference="100",
            limit_up="110",
            limit_down="90",
            volume="9000",
            exchange="TPEx",
            name="環球晶",
        ),
    ]
    source, calls = _source(rows)
    now = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)

    twse = source.fetch(RankingType.limit_up, "TWSE", now)
    again = source.fetch(RankingType.limit_up, "TWSE", now)
    both = source.fetch(RankingType.limit_up, "all", now)
    down = source.fetch(RankingType.limit_down, "all", now)

    assert [quote.symbol for quote in twse.quotes] == ["1101", "2330"]
    assert [quote.symbol for quote in again.quotes] == ["1101", "2330"]
    assert [quote.symbol for quote in both.quotes] == ["6488", "1101", "2330"]
    assert down.quotes == []
    assert calls["mis"] == 1
    assert any(key.endswith("limit_up:TWSE") for key in source._memory)
    assert any(key.endswith("limit_up:all") for key in source._memory)
    assert any(key.endswith("limit_down:all") for key in source._memory)


def test_cached_limit_down_keeps_negative_change_percent() -> None:
    rows = [
        _row(
            "1709",
            last="43.50",
            reference="48.30",
            limit_up="53.10",
            limit_down="43.50",
            volume="48516",
        ),
        _row(
            "8084",
            last="33.10",
            reference="36.75",
            limit_up="40.40",
            limit_down="33.10",
            volume="100",
            exchange="TPEx",
        ),
    ]
    source, calls = _source(rows)
    now = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)

    first = source.fetch(RankingType.limit_down, "all", now)
    second = source.fetch(RankingType.limit_down, "all", now)

    assert [quote.symbol for quote in first.quotes] == ["1709", "8084"]
    assert [quote.change_percent for quote in second.quotes] == [
        quote.change_percent for quote in first.quotes
    ]
    assert second.quotes[0].change_percent == Decimal("-9.94")
    assert second.quotes[1].change_percent == Decimal("-9.93")
    assert calls["mis"] == 1


def test_empty_mis_body_is_unavailable_not_a_clean_empty_ranking() -> None:
    source, _calls = _source([])
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.quotes == []
    assert result.unavailable is True
    assert result.partial is False


def test_classified_non_limit_quotes_are_a_real_empty_ranking() -> None:
    rows = [
        _row("2330", last="100", reference="100", limit_up="110", limit_down="90"),
        _row("1101", last="10", reference="10", limit_up="11", limit_down="9"),
    ]
    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {"TWSE": ("2330", "1101"), "TPEx": ()},
        mis_fetcher=lambda _ex_chs: rows,
        cache_ttl_seconds=600,
    )
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.quotes == []
    assert result.unavailable is False
    assert result.partial is False


def test_unrequested_symbols_are_ignored() -> None:
    rows = [
        _row("2330", last="110", reference="100", limit_up="110", limit_down="90"),
        _row("9999", last="110", reference="100", limit_up="110", limit_down="90"),
    ]
    source, _calls = _source(rows)
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert [quote.symbol for quote in result.quotes] == ["2330"]
    assert result.unavailable is False


def test_empty_successful_batch_marks_the_scan_partial() -> None:
    def mis_fetcher(ex_chs: list[str]) -> list[dict[str, str]]:
        if any(channel.startswith("otc_") for channel in ex_chs):
            return [
                _row(
                    "6488",
                    last="110",
                    reference="100",
                    limit_up="110",
                    limit_down="90",
                    exchange="TPEx",
                )
            ]
        return []

    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {
            "TWSE": tuple(f"{index:04d}" for index in range(40)),
            "TPEx": ("6488",),
        },
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.partial is True
    assert result.unavailable is False
    assert [quote.symbol for quote in result.quotes] == ["6488"]


def test_provider_failure_is_empty_and_not_a_false_limit_list() -> None:
    source, _calls = _source([], error=TimeoutError("mis down"))
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.quotes == []
    assert result.unavailable is True
    assert result.partial is False


def test_partial_batch_failure_omits_unclassified_symbols() -> None:
    def mis_fetcher(ex_chs: list[str]) -> list[dict[str, str]]:
        if any(channel.startswith("tse_") for channel in ex_chs):
            raise TimeoutError("listed board failed")
        return [
            _row(
                "6488",
                last="90",
                reference="100",
                limit_up="110",
                limit_down="90",
                exchange="TPEx",
            )
        ]

    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {
            "TWSE": tuple(f"{index:04d}" for index in range(40)),
            "TPEx": ("6488",),
        },
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    result = source.fetch(RankingType.limit_down, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.partial is True
    assert result.unavailable is False
    assert [quote.symbol for quote in result.quotes] == ["6488"]


def test_service_shapes_limit_lists_and_rejects_us() -> None:
    rows = [
        _row("2330", last="110", reference="100", limit_up="110", limit_down="90", name="台積電"),
        _row("6488", last="90", reference="100", limit_up="110", limit_down="90", exchange="TPEx"),
    ]
    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {"TWSE": ("2330",), "TPEx": ("6488",)},
        mis_fetcher=lambda _ex_chs: rows,
        cache_ttl_seconds=600,
    )
    service = RankingsService(price_limit_source=source)
    now = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)

    listed = service.get_rankings("TW", RankingType.limit_up, limit=50, now=now, tw_board="TWSE")
    combined = service.get_rankings("TW", RankingType.limit_down, limit=50, now=now, tw_board="all")
    empty = service.get_rankings("TW", RankingType.limit_down, limit=50, now=now, tw_board="TWSE")
    us = service.get_rankings("US", RankingType.limit_up, limit=50, now=now)

    assert listed.source == TW_PRICE_LIMIT_SOURCE
    assert listed.sourceType == RankingSourceType.free_public
    assert listed.twBoard == "TWSE"
    assert listed.universe == "TWSE"
    assert listed.twRankingsScope is None
    assert "hard-coded" not in listed.source
    assert listed.items[0].stock.symbol == "2330"
    assert listed.items[0].stock.nameZhTw == "台積電"
    assert listed.items[0].priceLimitStatus == "limit_up"
    assert listed.items[0].referencePrice == pytest.approx(100)
    assert listed.items[0].limitUpPrice == pytest.approx(110)
    assert listed.items[0].lastPrice == pytest.approx(110)
    assert listed.items[0].changePercent == pytest.approx(10)
    assert listed.items[0].volume == 1_000_000
    assert listed.items[0].stock.currency == "TWD"

    assert [item.stock.exchange for item in combined.items] == ["TPEx"]
    assert combined.twBoard == "all"
    assert combined.universe is None
    assert combined.items[0].priceLimitStatus == "limit_down"
    assert combined.items[0].limitDownPrice == pytest.approx(90)

    assert empty.items == []
    assert empty.warningMessage is None

    assert us.items == []
    assert us.sourceType == RankingSourceType.unsupported
    assert us.unsupportedReason == US_PRICE_LIMIT_UNSUPPORTED_REASON


def test_service_provider_failure_does_not_invent_limits() -> None:
    source, _calls = _source([], error=TimeoutError("down"))
    service = RankingsService(price_limit_source=source)
    response = service.get_rankings(
        "TW",
        RankingType.limit_up,
        limit=50,
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
        tw_board="all",
    )
    assert response.items == []
    assert response.warningMessage == TW_PRICE_LIMIT_UNAVAILABLE_WARNING
    assert "hard-coded" not in (response.warningMessage or "")
    assert response.twRankingsScope is None


def test_service_partial_warning_is_explicit() -> None:
    def mis_fetcher(ex_chs: list[str]) -> list[dict[str, str]]:
        if any(channel.startswith("tse_") for channel in ex_chs):
            raise TimeoutError("listed board failed")
        return [
            _row(
                "6488",
                last="90",
                reference="100",
                limit_up="110",
                limit_down="90",
                exchange="TPEx",
            )
        ]

    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {
            "TWSE": tuple(f"{index:04d}" for index in range(40)),
            "TPEx": ("6488",),
        },
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    service = RankingsService(price_limit_source=source)
    response = service.get_rankings(
        "TW",
        RankingType.limit_down,
        limit=50,
        now=datetime(2026, 9, 26, tzinfo=timezone.utc),
        tw_board="all",
    )
    assert response.warningMessage == TW_PRICE_LIMIT_PARTIAL_WARNING
    assert response.items[0].stock.symbol == "6488"
    assert response.items[0].priceLimitStatus == "limit_down"


def test_six_digit_symbol_uses_published_limits() -> None:
    rows = [_row("006208", last="20", reference="18.20", limit_up="20", limit_down="16.40")]
    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {"TWSE": ("006208", "NOT-A-CODE"), "TPEx": ()},
        mis_fetcher=lambda _ex_chs: rows,
        cache_ttl_seconds=600,
    )
    result = source.fetch(RankingType.limit_up, "TWSE", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert [quote.symbol for quote in result.quotes] == ["006208"]
    assert result.partial is False
    assert result.unavailable is False


def test_unknown_quote_and_missing_symbol_mark_the_scan_partial() -> None:
    rows = [
        _row("2330", last="110", reference="100", limit_up="110", limit_down="90"),
        _row("1101", last="10.05", reference="9.15", limit_up="10.05", limit_down=None),
    ]
    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {"TWSE": ("2330", "1101", "0050"), "TPEx": ()},
        mis_fetcher=lambda _ex_chs: rows,
        cache_ttl_seconds=600,
    )
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert [quote.symbol for quote in result.quotes] == ["2330"]
    assert result.partial is True
    assert result.unavailable is False


def test_force_refresh_bypasses_both_cache_layers() -> None:
    calls = {"mis": 0}

    def mis_fetcher(_ex_chs: list[str]) -> list[dict[str, str]]:
        calls["mis"] += 1
        return [_row("2330", last="110", reference="100", limit_up="110", limit_down="90")]

    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {"TWSE": ("2330",), "TPEx": ()},
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    first = source.fetch(RankingType.limit_up, "TWSE", now)
    second = source.fetch(RankingType.limit_up, "TWSE", now)
    refreshed = source.fetch(RankingType.limit_up, "TWSE", now, force_refresh=True)
    assert [quote.symbol for quote in first.quotes] == ["2330"]
    assert [quote.symbol for quote in second.quotes] == ["2330"]
    assert [quote.symbol for quote in refreshed.quotes] == ["2330"]
    assert calls["mis"] == 2


def test_scan_deadline_returns_unavailable_without_draining_the_queue(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tw_price_limit_rankings._SCAN_DEADLINE_SECONDS", 0)
    calls = {"mis": 0}

    def mis_fetcher(_ex_chs: list[str]) -> list[dict[str, str]]:
        calls["mis"] += 1
        sleep(2)
        return []

    source = TwPriceLimitRankingsSource(
        symbol_loader=lambda: {
            "TWSE": tuple(f"{index:04d}" for index in range(80)),
            "TPEx": (),
        },
        mis_fetcher=mis_fetcher,
        cache_ttl_seconds=600,
    )
    started = monotonic()
    result = source.fetch(RankingType.limit_up, "all", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert result.unavailable is True
    assert result.quotes == []
    assert calls["mis"] == 0
    assert monotonic() - started < 1


def test_open_mis_circuit_skips_the_scan() -> None:
    from app.services.providers.tw_provider import (
        note_mis_transport_failure,
        reset_mis_circuit_for_tests,
    )

    reset_mis_circuit_for_tests()
    calls = {"mis": 0}

    def mis_fetcher(_ex_chs: list[str]) -> list[dict[str, str]]:
        calls["mis"] += 1
        return [_row("2330", last="110", reference="100", limit_up="110", limit_down="90")]

    try:
        for _ in range(3):
            note_mis_transport_failure("test")
        source = TwPriceLimitRankingsSource(
            symbol_loader=lambda: {"TWSE": ("2330",), "TPEx": ()},
            mis_fetcher=mis_fetcher,
            cache_ttl_seconds=600,
        )
        result = source.fetch(RankingType.limit_up, "TWSE", datetime(2026, 9, 26, tzinfo=timezone.utc))
        assert result.unavailable is True
        assert calls["mis"] == 0
    finally:
        reset_mis_circuit_for_tests()
