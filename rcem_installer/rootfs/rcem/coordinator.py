"""Data update coordinator for RCEm."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .calc import ResolvedPrice, month_key, resolve_price
from .const import (
    CONF_ESTIMATE,
    CONF_ESTIMATE_METHOD,
    CONF_EXPORT_ENTITY,
    CONF_USE_CORRECTED,
    CONF_VAT,
    DEFAULT_ESTIMATE,
    DEFAULT_ESTIMATE_METHOD,
    DEFAULT_USE_CORRECTED,
    DEFAULT_VAT,
    DOMAIN,
    ESTIMATE_NONE,
    ESTIMATE_RCE,
    FETCH_INTERVAL,
    FETCH_TIMEOUT,
    FORECAST_INTERVAL,
    UPDATE_INTERVAL,
    URL,
)
from .forecast import MonthEstimate, RceForecast, estimate_months
from .parser import MonthPrice, parse_rcem
from .statistics import StatisticsSync, SyncResult

_LOGGER = logging.getLogger(__name__)

type RcemConfigEntry = ConfigEntry[RcemCoordinator]


@dataclass(slots=True)
class RcemData:
    """Everything the entities show."""

    prices: dict[str, MonthPrice]
    fetched: datetime
    current: ResolvedPrice | None
    latest: MonthPrice | None
    earnings: SyncResult
    estimates: dict[str, MonthEstimate]


class RcemCoordinator(DataUpdateCoordinator[RcemData]):
    """Fetches RCEm from PSE and keeps the statistics in sync."""

    config_entry: RcemConfigEntry

    def __init__(self, hass: HomeAssistant, entry: RcemConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        opts = {**entry.data, **entry.options}
        self.export_entity: str | None = opts.get(CONF_EXPORT_ENTITY) or None
        self.vat: float = float(opts.get(CONF_VAT, DEFAULT_VAT))
        self.use_corrected: bool = opts.get(CONF_USE_CORRECTED, DEFAULT_USE_CORRECTED)
        # Entries from 0.1.x only have the CONF_ESTIMATE on/off switch.
        legacy = DEFAULT_ESTIMATE_METHOD if opts.get(CONF_ESTIMATE, DEFAULT_ESTIMATE) else ESTIMATE_NONE
        self.estimate_method: str = opts.get(CONF_ESTIMATE_METHOD, legacy)
        self.forecast = RceForecast(hass) if self.estimate_method == ESTIMATE_RCE else None
        self._forecast_updated: datetime | None = None
        self._forecast_running = False
        self.stats = StatisticsSync(
            hass,
            entry.entry_id,
            export_entity=self.export_entity,
            vat=self.vat,
            use_corrected=self.use_corrected,
            estimate_method=self.estimate_method,
        )
        self._prices: dict[str, MonthPrice] | None = None
        self._fetched: datetime | None = None
        self._cache: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.prices")
        self._cache_loaded = False

    async def _load_cache(self) -> None:
        """Load the last fetched prices, so a restart doesn't depend on PSE."""
        self._cache_loaded = True
        if not (data := await self._cache.async_load()):
            return
        self._prices = {k: MonthPrice.from_dict(v) for k, v in data["prices"].items()}
        self._fetched = dt_util.parse_datetime(data["fetched"])

    async def _fetch(self) -> dict[str, MonthPrice]:
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(FETCH_TIMEOUT):
            resp = await session.get(URL, headers={"User-Agent": "HomeAssistant-RCEm"})
            resp.raise_for_status()
            html = await resp.text()
        prices = parse_rcem(html)
        _LOGGER.debug("Fetched RCEm page: %d months, latest %s", len(prices), max(prices, default=None))
        if not prices:
            raise UpdateFailed("No RCEm prices found on the PSE page")
        return prices

    async def _async_update_data(self) -> RcemData:
        now = dt_util.utcnow()
        if not self._cache_loaded:
            await self._load_cache()
        if (
            self._prices is None
            or self._fetched is None
            or now - self._fetched >= FETCH_INTERVAL
        ):
            try:
                self._prices = await self._fetch()
                self._fetched = now
                await self._cache.async_save(
                    {
                        "fetched": now.isoformat(),
                        "prices": {k: p.as_dict() for k, p in self._prices.items()},
                    }
                )
            except (aiohttp.ClientError, TimeoutError, UpdateFailed) as err:
                reason = str(err) or type(err).__name__
                if self._prices is None:
                    raise UpdateFailed(f"Cannot fetch RCEm from PSE: {reason}") from err
                _LOGGER.warning(
                    "Cannot refresh RCEm from PSE, using prices fetched %s: %s",
                    self._fetched,
                    reason,
                )

        prices = self._prices
        estimates: dict[str, MonthEstimate] = {}
        if self.forecast is not None:
            await self.forecast.async_load()
            self._schedule_forecast(prices, now)
            estimates = estimate_months(
                self.forecast.days, prices, use_corrected=self.use_corrected
            )
        rce_estimates = {k: e.value for k, e in estimates.items()}
        local = dt_util.now()
        current = resolve_price(
            prices,
            month_key(local.year, local.month),
            use_corrected=self.use_corrected,
            estimate=self.estimate_method != ESTIMATE_NONE,
            rce_estimates=rce_estimates,
        )
        earnings = await self.stats.async_sync(prices, rce_estimates)
        return RcemData(
            prices=prices,
            fetched=self._fetched or now,
            current=current,
            latest=prices[max(prices)],
            earnings=earnings,
            estimates=estimates,
        )

    def _schedule_forecast(self, prices: dict[str, MonthPrice], now: datetime) -> None:
        """Refresh RCE/PV data in the background; the first fetch takes a while."""
        if self._forecast_running or (
            self._forecast_updated is not None
            and now - self._forecast_updated < FORECAST_INTERVAL
        ):
            return
        self._forecast_running = True
        self.config_entry.async_create_background_task(
            self.hass, self._async_update_forecast(prices), f"{DOMAIN}_forecast"
        )

    async def _async_update_forecast(self, prices: dict[str, MonthPrice]) -> None:
        assert self.forecast is not None
        try:
            await self.forecast.async_update(prices)
            self._forecast_updated = dt_util.utcnow()
        finally:
            self._forecast_running = False
        await self.async_request_refresh()
