"""RCEm estimate from RCE + PV, checked against published PSE prices."""

import json
from pathlib import Path

import pytest

from custom_components.rcem.calc import resolve_price
from custom_components.rcem.forecast import (
    DayAggregate,
    aggregate_days,
    calibration_factor,
    estimate_months,
)
from custom_components.rcem.parser import parse_rcem

HERE = Path(__file__).parent
PRICES = parse_rcem((HERE / "fixture_pse.html").read_text(encoding="utf-8"))
DAYS = {
    d: DayAggregate(*v)
    for d, v in json.loads((HERE / "fixture_rce_days.json").read_text()).items()
}


def _known_until(month: str):
    """Prices and RCE days as they were before `month` was published."""
    prices = {k: v for k, v in PRICES.items() if k < month}
    days = {d: a for d, a in DAYS.items() if d[:7] <= month}
    return prices, days


def test_aggregate_days_clamps_negative_prices():
    rce = [
        {"business_date": "2026-08-01", "period": "12:00 - 12:15", "rce_pln": -50.0},
        {"business_date": "2026-08-01", "period": "12:15 - 12:30", "rce_pln": 100.0},
        {"business_date": "2026-08-01", "period": "12:30 - 12:45", "rce_pln": 100.0},
    ]
    pv = [
        {"business_date": "2026-08-01", "period": "12:00 - 12:15", "pv": 10.0},
        {"business_date": "2026-08-01", "period": "12:15 - 12:30", "pv": 30.0},
        {"business_date": "2026-08-01", "period": "12:30 - 12:45", "pv": None},
    ]
    agg = aggregate_days(rce, pv)["2026-08-01"]
    assert agg.num == pytest.approx(30.0 * 100.0)
    assert agg.den == pytest.approx(40.0)
    assert agg.periods == 2
    assert not agg.complete


@pytest.mark.parametrize(
    ("month", "tolerance"), [("2026-07", 0.04), ("2026-08", 0.03), ("2026-09", 0.05)]
)
def test_estimate_matches_published(month, tolerance):
    prices, days = _known_until(month)
    est = estimate_months(days, prices)[month]
    published = PRICES[month].value()
    assert est.value == pytest.approx(published, rel=tolerance)
    assert est.days == (30 if month == "2026-09" else 31)


def test_estimate_beats_last_published():
    """Over Apr 2025 – Sep 2026 the RCE estimate is far closer than reusing
    the last published RCEm."""
    rce_err = last_err = 0.0
    months = sorted(m for m in PRICES if "2025-04" <= m <= "2026-09")
    for month in months:
        prices, days = _known_until(month)
        published = PRICES[month].value()
        rce_err += abs(estimate_months(days, prices)[month].value - published) / published
        last = resolve_price(prices, month)
        last_err += abs(last.net_kwh * 1000 - published) / published
    assert rce_err / len(months) < 0.09
    assert last_err / len(months) > 0.25


def test_october_2026_and_fallback_for_month_without_data():
    est = estimate_months(DAYS, PRICES)
    assert set(est) == {"2026-10"}
    assert est["2026-10"].value == pytest.approx(421.3, abs=1.0)
    assert calibration_factor(DAYS, PRICES) == pytest.approx(0.84, abs=0.02)
    rce = {k: e.value for k, e in est.items()}
    # November has no RCE data yet: October's estimate is used.
    nov = resolve_price(PRICES, "2026-11", rce_estimates=rce)
    assert nov.method == "rce"
    assert nov.source_month == "2026-10"
    assert nov.net_kwh == pytest.approx(rce["2026-10"] / 1000)
    oct_ = resolve_price(PRICES, "2026-10", rce_estimates=rce)
    assert oct_.method == "rce" and oct_.estimated
    assert resolve_price(PRICES, "2026-09", rce_estimates=rce).method == "published"
