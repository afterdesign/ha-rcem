"""Estimate RCEm for months PSE has not published yet, from RCE and PV data.

PSE computes RCEm as the RCE of every settlement period weighted by the energy
prosumers fed into the grid (negative RCE counted as 0). That volume is not
public, so national PV generation from the PSE API stands in for it. Because
self-consumption removes the morning and evening shoulders of the PV curve,
the PV-weighted price runs ~15% high; a calibration factor (published RCEm /
PV-weighted RCE, averaged over recent published months) corrects for that.
Backtested on 04.2025–09.2026: mean absolute error ~7%, versus ~30% when the
last published RCEm is reused.
"""

from __future__ import annotations

import asyncio
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    CALIBRATION_MONTHS,
    DOMAIN,
    FETCH_TIMEOUT,
    MIN_PERIODS_PER_DAY,
)
from .parser import MonthPrice

_LOGGER = logging.getLogger(__name__)

# Days near "today" are re-fetched: PV data for a day appears with a delay.
REFRESH_RECENT_DAYS = 3
KEEP_DAYS = 500


@dataclass(frozen=True, slots=True)
class DayAggregate:
    """Sum of PV-weighted RCE for one day."""

    num: float  # Σ pv · max(rce, 0)
    den: float  # Σ pv
    periods: int  # settlement periods with both values

    @property
    def complete(self) -> bool:
        """Return True when the day has (nearly) all periods."""
        return self.periods >= MIN_PERIODS_PER_DAY


@dataclass(frozen=True, slots=True)
class MonthEstimate:
    """Estimated RCEm for one month."""

    month: str
    value: float  # PLN/MWh
    pv_weighted: float  # PLN/MWh, before calibration
    factor: float
    days: int  # days of data the estimate is based on


def aggregate_days(rce_rows: list[dict], pv_rows: list[dict]) -> dict[str, DayAggregate]:
    """Join RCE and PV rows by settlement period and sum them per day."""
    pv = {
        (r["business_date"], r["period"]): r["pv"]
        for r in pv_rows
        if r.get("pv") is not None
    }
    sums: dict[str, list[float]] = {}
    for r in rce_rows:
        key = (r["business_date"], r["period"])
        if (price := r.get("rce_pln")) is None or key not in pv:
            continue
        acc = sums.setdefault(r["business_date"], [0.0, 0.0, 0])
        acc[0] += pv[key] * max(price, 0.0)
        acc[1] += pv[key]
        acc[2] += 1
    return {d: DayAggregate(n, w, int(c)) for d, (n, w, c) in sums.items()}


def month_pv_weighted(days: dict[str, DayAggregate], month: str) -> tuple[float, int] | None:
    """Return (PV-weighted RCE, number of complete days) for a month."""
    selected = [a for d, a in days.items() if d.startswith(month) and a.complete]
    den = sum(a.den for a in selected)
    if not selected or den <= 0:
        return None
    return sum(a.num for a in selected) / den, len(selected)


def _days_in_month(month: str) -> int:
    year, mon = (int(x) for x in month.split("-"))
    return monthrange(year, mon)[1]


def calibration_factor(
    days: dict[str, DayAggregate],
    prices: dict[str, MonthPrice],
    *,
    use_corrected: bool = True,
    months: int = CALIBRATION_MONTHS,
) -> float | None:
    """Average of published RCEm / PV-weighted RCE over recent full months."""
    ratios: list[float] = []
    for key in sorted(prices, reverse=True):
        result = month_pv_weighted(days, key)
        if result is None or result[1] < _days_in_month(key) or result[0] <= 0:
            continue
        ratios.append(prices[key].value(use_corrected) / result[0])
        if len(ratios) >= months:
            break
    return sum(ratios) / len(ratios) if ratios else None


def estimate_months(
    days: dict[str, DayAggregate],
    prices: dict[str, MonthPrice],
    *,
    use_corrected: bool = True,
) -> dict[str, MonthEstimate]:
    """Estimate RCEm for every month with RCE data that PSE hasn't published."""
    factor = calibration_factor(days, prices, use_corrected=use_corrected)
    if factor is None:
        return {}
    result: dict[str, MonthEstimate] = {}
    for month in sorted({d[:7] for d in days} - set(prices)):
        if prices and month < max(prices):
            continue
        if (weighted := month_pv_weighted(days, month)) is None:
            continue
        result[month] = MonthEstimate(
            month, weighted[0] * factor, weighted[0], factor, weighted[1]
        )
    return result


def _month_start(d: date, back: int) -> date:
    year, month = d.year, d.month - back
    while month < 1:
        year, month = year - 1, month + 12
    return date(year, month, 1)


class RceForecast:
    """Keeps per-day PV-weighted RCE sums, fetched from the PSE API."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.rce_days")
        self.days: dict[str, DayAggregate] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load cached day sums."""
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load() or {}
        self.days = {d: DayAggregate(*v) for d, v in data.get("days", {}).items()}

    async def _fetch(self, endpoint: str, start: date, end: date, fields: str) -> list[dict]:
        session = async_get_clientsession(self.hass)
        url: str | None = f"{API_URL}/{endpoint}?" + urlencode(
            {
                "$filter": f"business_date ge '{start.isoformat()}' "
                f"and business_date le '{end.isoformat()}'",
                "$select": fields,
                "$first": "20000",
            }
        )
        rows: list[dict] = []
        while url:
            async with asyncio.timeout(FETCH_TIMEOUT):
                resp = await session.get(url)
                resp.raise_for_status()
                data = await resp.json()
            rows.extend(data.get("value", []))
            url = data.get("nextLink")
        return rows

    def _missing_ranges(self, first: date, last: date) -> list[tuple[date, date]]:
        """Return month-sized date ranges that contain days to (re)fetch."""
        recent = last - timedelta(days=REFRESH_RECENT_DAYS)
        ranges: list[tuple[date, date]] = []
        day = first
        while day <= last:
            month_end = min(
                date(day.year, day.month, monthrange(day.year, day.month)[1]), last
            )
            todo = [
                d
                for d in (day + timedelta(days=i) for i in range((month_end - day).days + 1))
                if d >= recent
                or (agg := self.days.get(d.isoformat())) is None
                or not agg.complete
            ]
            if todo:
                ranges.append((min(todo), max(todo)))
            day = month_end + timedelta(days=1)
        return ranges

    async def async_update(self, prices: dict[str, MonthPrice]) -> None:
        """Fetch missing days: recent published months for calibration, then
        the months PSE has not published yet, up to yesterday."""
        await self.async_load()
        today = dt_util.now().date()
        last = today - timedelta(days=1)
        latest = max(prices) if prices else None
        first = _month_start(today, CALIBRATION_MONTHS + 2)
        if latest is None:
            return
        for start, end in self._missing_ranges(first, last):
            try:
                rce = await self._fetch("rce-pln", start, end, "business_date,period,rce_pln")
                pv = await self._fetch("his-wlk-cal", start, end, "business_date,period,pv")
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.warning(
                    "Cannot fetch RCE/PV from PSE API for %s–%s: %s",
                    start,
                    end,
                    str(err) or type(err).__name__,
                )
                break
            self.days.update(aggregate_days(rce, pv))
            _LOGGER.debug("Fetched RCE/PV for %s–%s", start, end)
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.days = {d: a for d, a in self.days.items() if d >= cutoff}
        await self._store.async_save(
            {"days": {d: [a.num, a.den, a.periods] for d, a in self.days.items()}}
        )
