"""Sensors for RCEm."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .calc import gross, month_key
from .const import CURRENCY, DOMAIN, NAME, PRICE_UNIT, URL
from .coordinator import RcemConfigEntry, RcemCoordinator, RcemData
from .statistics import MonthEarnings


@dataclass(frozen=True, kw_only=True)
class RcemSensorDescription(SensorEntityDescription):
    """Describes an RCEm sensor."""

    value_fn: Callable[[RcemCoordinator, RcemData], float | None]
    attrs_fn: Callable[[RcemCoordinator, RcemData], dict[str, Any]] | None = None
    needs_export: bool = False


def _price(c: RcemCoordinator, d: RcemData, *, with_vat: bool) -> float | None:
    if d.current is None:
        return None
    value = gross(d.current.net_kwh, c.vat) if with_vat else d.current.net_kwh
    return round(value, 5)


def _price_attrs(c: RcemCoordinator, d: RcemData, *, with_vat: bool) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if d.current is not None:
        src = d.prices[d.current.source_month]
        attrs.update(
            month=d.current.month,
            source_month=d.current.source_month,
            estimated=d.current.estimated,
            published=src.published(c.use_corrected),
            corrected=c.use_corrected and src.corrected is not None,
            pln_per_mwh=src.value(c.use_corrected),
        )
    attrs["vat_percent"] = c.vat if with_vat else 0
    attrs["prices"] = {
        k: round(gross(p.value(c.use_corrected) / 1000, c.vat if with_vat else 0), 5)
        for k, p in d.prices.items()
    }
    attrs["fetched"] = d.fetched
    return attrs


def _month_offset(offset: int) -> str:
    local = dt_util.now()
    year, month = local.year, local.month + offset
    while month < 1:
        year, month = year - 1, month + 12
    return month_key(year, month)


def _month(d: RcemData, offset: int) -> MonthEarnings | None:
    return d.earnings.months.get(_month_offset(offset))


def _month_value(d: RcemData, offset: int, *, with_vat: bool) -> float | None:
    if (m := _month(d, offset)) is None:
        return 0.0 if offset == 0 else None
    return round(m.gross if with_vat else m.net, 2)


def _month_attrs(c: RcemCoordinator, d: RcemData, offset: int) -> dict[str, Any]:
    key = _month_offset(offset)
    m = _month(d, offset)
    attrs: dict[str, Any] = {"month": key, "export_entity": c.export_entity}
    if m is not None:
        attrs["exported_kwh"] = round(m.kwh, 3)
        if m.price is not None:
            attrs["price_net"] = round(m.price.net_kwh, 5)
            attrs["estimated"] = m.price.estimated
            attrs["source_month"] = m.price.source_month
    return attrs


def _total_attrs(c: RcemCoordinator, d: RcemData) -> dict[str, Any]:
    return {
        "export_entity": c.export_entity,
        "months": {
            k: {
                "kwh": round(m.kwh, 3),
                "price_net": round(m.price.net_kwh, 5) if m.price else None,
                "net": round(m.net, 2),
                "gross": round(m.gross, 2),
                "estimated": m.price.estimated if m.price else None,
            }
            for k, m in d.earnings.months.items()
        },
    }


SENSORS: tuple[RcemSensorDescription, ...] = (
    RcemSensorDescription(
        key="price_net",
        translation_key="price_net",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        icon="mdi:cash",
        value_fn=lambda c, d: _price(c, d, with_vat=False),
        attrs_fn=lambda c, d: _price_attrs(c, d, with_vat=False),
    ),
    RcemSensorDescription(
        key="price_gross",
        translation_key="price_gross",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        icon="mdi:cash-plus",
        value_fn=lambda c, d: _price(c, d, with_vat=True),
        attrs_fn=lambda c, d: _price_attrs(c, d, with_vat=True),
    ),
    RcemSensorDescription(
        key="earnings_month_net",
        translation_key="earnings_month_net",
        value_fn=lambda c, d: _month_value(d, 0, with_vat=False),
        attrs_fn=lambda c, d: _month_attrs(c, d, 0),
        needs_export=True,
    ),
    RcemSensorDescription(
        key="earnings_month_gross",
        translation_key="earnings_month_gross",
        value_fn=lambda c, d: _month_value(d, 0, with_vat=True),
        attrs_fn=lambda c, d: _month_attrs(c, d, 0),
        needs_export=True,
    ),
    RcemSensorDescription(
        key="earnings_previous_month_net",
        translation_key="earnings_previous_month_net",
        value_fn=lambda c, d: _month_value(d, -1, with_vat=False),
        attrs_fn=lambda c, d: _month_attrs(c, d, -1),
        needs_export=True,
    ),
    RcemSensorDescription(
        key="earnings_previous_month_gross",
        translation_key="earnings_previous_month_gross",
        value_fn=lambda c, d: _month_value(d, -1, with_vat=True),
        attrs_fn=lambda c, d: _month_attrs(c, d, -1),
        needs_export=True,
    ),
    RcemSensorDescription(
        key="earnings_total_net",
        translation_key="earnings_total_net",
        value_fn=lambda c, d: round(d.earnings.total_net, 2),
        attrs_fn=_total_attrs,
        needs_export=True,
    ),
    RcemSensorDescription(
        key="earnings_total_gross",
        translation_key="earnings_total_gross",
        value_fn=lambda c, d: round(d.earnings.total_gross, 2),
        attrs_fn=_total_attrs,
        needs_export=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RcemConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up RCEm sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        RcemSensor(coordinator, description)
        for description in SENSORS
        if coordinator.export_entity or not description.needs_export
    )


class RcemSensor(CoordinatorEntity[RcemCoordinator], SensorEntity):
    """An RCEm sensor."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"prices", "months", "fetched"})
    entity_description: RcemSensorDescription

    def __init__(self, coordinator: RcemCoordinator, description: RcemSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        if description.key.startswith("earnings"):
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = CURRENCY
            self._attr_suggested_display_precision = 2
            self._attr_icon = "mdi:cash-multiple"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Polskie Sieci Elektroenergetyczne",
            model="RCEm",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=URL,
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator, self.coordinator.data)
