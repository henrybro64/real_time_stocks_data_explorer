"""Focused TDCC shareholding CSV parse tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.tdcc_shareholding import parse_tdcc_shareholding_csv

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tw_ownership"


def test_parse_skips_total_levels_16_17_and_999() -> None:
    text = (_FIXTURES / "tdcc_malformed_rows.csv").read_text(encoding="utf-8")
    points = parse_tdcc_shareholding_csv(text, symbol="2330")
    assert len(points) == 1
    # Levels 12-15 only; level 999 total excluded from the sum.
    assert points[0].holders_400_lots_or_more_percent == pytest.approx(50.0)


def test_parse_filters_symbol_and_normalizes_percent() -> None:
    text = (_FIXTURES / "tdcc_2330_multi_date.csv").read_text(encoding="utf-8")
    only_2330 = parse_tdcc_shareholding_csv(text, symbol="2330")
    only_2317 = parse_tdcc_shareholding_csv(text, symbol="2317")
    assert all(point.symbol == "2330" for point in only_2330)
    assert len(only_2317) == 1
    assert only_2317[0].holders_400_lots_or_more_percent == pytest.approx(40.0)
