"""Long-term statistics: RCEm price per hour and earnings from exported energy.

Earnings are written as external statistics (rcem:earnings_net / _gross) so
that every exported kWh is valued at the RCEm of the month it was exported
in, even though PSE publishes that price only around the 11th of the next
month. When a price is published or corrected, the affected history is
rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calc import DEPOSIT_FACTOR_FROM, ResolvedPrice, gross, month_key, resolve_price
from .const import (
    CURRENCY,
    DOMAIN,
    ESTIMATE_NONE,
    ESTIMATE_RCE,
    PRICE_ONLY_MONTHS_BACK,
    PRICE_UNIT,
    STAT_EARNINGS_GROSS,
    STAT_EARNINGS_NET,
    STAT_PRICE_GROSS,
    STAT_PRICE_NET,
)
from .parser import MonthPrice

_LOGGER = logging.getLogger(__name__)

HOUR = timedelta(hours=1)


@dataclass(slots=True)
class MonthEarnings:
    """Exported energy and its value for one month."""

    month: str
    kwh: float
    price: ResolvedPrice | None
    net: float
    gross: float


@dataclass(slots=True)
class SyncResult:
    """Outcome of a statistics sync."""

    months: dict[str, MonthEarnings] = field(default_factory=dict)
    total_net: float = 0.0
    total_gross: float = 0.0


def _meta(statistic_id: str, name: str, unit: str, *, has_sum: bool) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC,
        has_sum=has_sum,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=None,
        unit_of_measurement=unit,
    )


class StatisticsSync:
    """Writes price and earnings statistics for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        export_entity: str | None,
        vat: float,
        use_corrected: bool,
        estimate_method: str,
    ) -> None:
        self.hass = hass
        self.export_entity = export_entity
        self.vat = vat
        self.use_corrected = use_corrected
        self.estimate_method = estimate_method
        self._rce_estimates: dict[str, float] = {}
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}")

    async def async_reset(self) -> None:
        """Forget what was written, so the next sync rebuilds everything."""
        await self._store.async_remove()

    def _resolve(self, prices: dict[str, MonthPrice], key: str) -> ResolvedPrice | None:
        return resolve_price(
            prices,
            key,
            use_corrected=self.use_corrected,
            estimate=self.estimate_method != ESTIMATE_NONE,
            rce_estimates=self._rce_estimates,
        )

    def _signature(self, prices: dict[str, MonthPrice], origin: datetime) -> str:
        payload = {
            "prices": {k: p.value(self.use_corrected) for k, p in prices.items()},
            "vat": self.vat,
            "deposit_factor_from": DEPOSIT_FACTOR_FROM,
            "estimate": self.estimate_method,
            "rce": {k: round(v, 2) for k, v in self._rce_estimates.items()},
            "export": self.export_entity,
            "origin": origin.isoformat(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def _db(self, func, *args):
        return await get_instance(self.hass).async_add_executor_job(func, *args)

    async def async_sync(
        self, prices: dict[str, MonthPrice], rce_estimates: dict[str, float]
    ) -> SyncResult:
        """Bring statistics up to date and return the monthly breakdown."""
        self._rce_estimates = (
            rce_estimates if self.estimate_method == ESTIMATE_RCE else {}
        )
        tz = dt_util.get_default_time_zone()
        now_hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        result = SyncResult()

        monthly_rows: list[dict] = []
        if self.export_entity:
            monthly = await self._db(
                statistics_during_period,
                self.hass,
                datetime(2000, 1, 1, tzinfo=dt_util.UTC),
                None,
                {self.export_entity},
                "month",
                {"energy": "kWh"},
                {"change"},
            )
            monthly_rows = monthly.get(self.export_entity, [])
            if not monthly_rows:
                _LOGGER.warning(
                    "No long-term statistics for %s yet; earnings not computed",
                    self.export_entity,
                )
                return result
            origin = dt_util.utc_from_timestamp(monthly_rows[0]["start"])
        else:
            local = dt_util.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            year, month = local.year, local.month - PRICE_ONLY_MONTHS_BACK
            while month < 1:
                year, month = year - 1, month + 12
            origin = dt_util.as_utc(local.replace(year=year, month=month))

        # Monthly breakdown for the sensors.
        for row in monthly_rows:
            start = datetime.fromtimestamp(row["start"], tz)
            key = month_key(start.year, start.month)
            kwh = row.get("change") or 0.0
            price = self._resolve(prices, key)
            net = kwh * price.net_kwh if price else 0.0
            result.months[key] = MonthEarnings(key, kwh, price, net, gross(net, self.vat, key))
            result.total_net += net
            result.total_gross += gross(net, self.vat, key)

        signature = self._signature(prices, origin)
        stored = await self._store.async_load() or {}
        start = origin
        base_net = base_gross = 0.0

        anchor_stat = STAT_EARNINGS_NET if self.export_entity else STAT_PRICE_NET
        if stored.get("signature") == signature:
            last = await self._db(
                get_last_statistics, self.hass, 1, anchor_stat, False, {"sum"}
            )
            if rows := last.get(anchor_stat):
                start = dt_util.utc_from_timestamp(rows[0]["start"]) + HOUR
                if self.export_entity:
                    base_net = rows[0].get("sum") or 0.0
                    last_g = await self._db(
                        get_last_statistics, self.hass, 1, STAT_EARNINGS_GROSS, False, {"sum"}
                    )
                    if g_rows := last_g.get(STAT_EARNINGS_GROSS):
                        base_gross = g_rows[0].get("sum") or 0.0
                    else:
                        start, base_net = origin, 0.0
        else:
            _LOGGER.info("RCEm prices or options changed: rebuilding statistics")

        if start >= now_hour:
            return result

        price_cache: dict[str, ResolvedPrice | None] = {}

        def price_at(ts: datetime) -> ResolvedPrice | None:
            local = ts.astimezone(tz)
            key = month_key(local.year, local.month)
            if key not in price_cache:
                price_cache[key] = self._resolve(prices, key)
            return price_cache[key]

        price_net_rows: list[StatisticData] = []
        price_gross_rows: list[StatisticData] = []
        hour = start
        while hour < now_hour:
            if (p := price_at(hour)) is not None:
                g = gross(p.net_kwh, self.vat, p.month)
                price_net_rows.append(
                    StatisticData(start=hour, mean=p.net_kwh, min=p.net_kwh, max=p.net_kwh)
                )
                price_gross_rows.append(StatisticData(start=hour, mean=g, min=g, max=g))
            hour += HOUR

        earn_net_rows: list[StatisticData] = []
        earn_gross_rows: list[StatisticData] = []
        if self.export_entity:
            hourly = await self._db(
                statistics_during_period,
                self.hass,
                start,
                now_hour,
                {self.export_entity},
                "hour",
                {"energy": "kWh"},
                {"change"},
            )
            sum_net, sum_gross = base_net, base_gross
            for row in hourly.get(self.export_entity, []):
                ts = dt_util.utc_from_timestamp(row["start"])
                p = price_at(ts)
                value = (row.get("change") or 0.0) * p.net_kwh if p else 0.0
                sum_net += value
                sum_gross += gross(value, self.vat, p.month if p else None)
                earn_net_rows.append(StatisticData(start=ts, state=sum_net, sum=sum_net))
                earn_gross_rows.append(
                    StatisticData(start=ts, state=sum_gross, sum=sum_gross)
                )

        if price_net_rows:
            async_add_external_statistics(
                self.hass,
                _meta(STAT_PRICE_NET, "RCEm cena netto", PRICE_UNIT, has_sum=False),
                price_net_rows,
            )
            async_add_external_statistics(
                self.hass,
                _meta(STAT_PRICE_GROSS, "RCEm cena brutto", PRICE_UNIT, has_sum=False),
                price_gross_rows,
            )
        if earn_net_rows:
            async_add_external_statistics(
                self.hass,
                _meta(STAT_EARNINGS_NET, "RCEm zarobek netto", CURRENCY, has_sum=True),
                earn_net_rows,
            )
            async_add_external_statistics(
                self.hass,
                _meta(STAT_EARNINGS_GROSS, "RCEm zarobek brutto", CURRENCY, has_sum=True),
                earn_gross_rows,
            )

        _LOGGER.debug(
            "RCEm statistics: %d price hours, %d earnings hours from %s",
            len(price_net_rows),
            len(earn_net_rows),
            start,
        )
        await self._store.async_save({"signature": signature})
        return result
