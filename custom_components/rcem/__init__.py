"""RCEm (PSE) integration: monthly market electricity price for prosumers."""

from __future__ import annotations

from homeassistant.components.recorder import get_instance
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import ALL_STATS
from .coordinator import RcemConfigEntry, RcemCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: RcemConfigEntry) -> bool:
    """Set up RCEm from a config entry."""
    coordinator = RcemCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: RcemConfigEntry) -> None:
    """Options changed: drop old statistics and rebuild them from scratch."""
    get_instance(hass).async_clear_statistics(ALL_STATS)
    await entry.runtime_data.stats.async_reset()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RcemConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: RcemConfigEntry) -> None:
    """Remove the statistics written by this entry."""
    get_instance(hass).async_clear_statistics(ALL_STATS)
