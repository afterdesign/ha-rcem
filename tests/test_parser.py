"""Parser and price-lookup tests."""

from datetime import date
from pathlib import Path

from custom_components.rcem.calc import resolve_price
from custom_components.rcem.parser import parse_rcem

HTML = (Path(__file__).parent / "fixture_pse.html").read_text(encoding="utf-8")


def test_parse_all_months():
    prices = parse_rcem(HTML)
    assert len(prices) == 52
    assert min(prices) == "2022-06"
    assert max(prices) == "2026-09"
    sep = prices["2026-09"]
    assert sep.original == 345.68
    assert sep.corrected is None
    assert sep.original_published == date(2026, 10, 11)


def test_latest_correction_wins():
    prices = parse_rcem(HTML)
    oct25 = prices["2025-10"]
    assert oct25.original == 334.02
    assert oct25.corrected == 330.27
    assert oct25.corrected_published == date(2026, 10, 11)
    assert oct25.value() == 330.27
    assert oct25.value(use_corrected=False) == 334.02


def test_month_name_with_footnote():
    assert parse_rcem(HTML)["2025-03"].corrected == 178.84


def test_resolve_estimate():
    prices = parse_rcem(HTML)
    real = resolve_price(prices, "2026-09")
    assert not real.estimated and abs(real.net_kwh - 0.34568) < 1e-9
    est = resolve_price(prices, "2026-11")
    assert est.estimated and est.source_month == "2026-09"
    assert resolve_price(prices, "2026-11", estimate=False) is None
    assert resolve_price(prices, "2020-01") is None
