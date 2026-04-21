"""Ksenia Lares 4.0 Gateway integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC

from .api import LaresAuthError, LaresClient, LaresError
from .const import (
    CONF_PIN,
    CONF_SSL,
    CONF_USERNAME,
    DEFAULT_PORT_PLAIN,
    DEFAULT_PORT_SSL,
    DEFAULT_SSL,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PLATFORMS,
    SETUP_TIMEOUT,
)
from .gateway import LaresGateway

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the domain (config-entry only integration)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Ksenia Lares 4.0 gateway from a config entry."""
    data = entry.data
    host = data[CONF_HOST]
    pin = data[CONF_PIN]
    use_ssl = data.get(CONF_SSL, DEFAULT_SSL)
    port = data.get(CONF_PORT) or (DEFAULT_PORT_SSL if use_ssl else DEFAULT_PORT_PLAIN)
    username = data.get(CONF_USERNAME)

    client = LaresClient(
        host=host,
        pin=pin,
        port=port,
        use_ssl=use_ssl,
        username=username,
    )
    gateway = LaresGateway(hass, entry.entry_id, client)

    try:
        await gateway.async_start()
        if not await client.wait_ready(timeout=SETUP_TIMEOUT):
            await gateway.async_stop()
            raise ConfigEntryNotReady(
                f"Timed out waiting for initial data from {host}"
            )
    except LaresAuthError as err:
        await gateway.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except LaresError as err:
        await gateway.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = gateway

    _register_device(hass, entry, gateway)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    gateway: LaresGateway | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if gateway is not None:
        await gateway.async_stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options or data change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_device(
    hass: HomeAssistant, entry: ConfigEntry, gateway: LaresGateway
) -> None:
    """Register/refresh the Lares panel in the device registry."""
    sysinfo = gateway.system_info
    registry = dr.async_get(hass)
    connections = set()
    if gateway.mac:
        connections.add((CONNECTION_NETWORK_MAC, gateway.mac))
    sw_version = None
    ver_lite = sysinfo.get("VER_LITE")
    if isinstance(ver_lite, dict):
        sw_version = ver_lite.get("FW")

    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, gateway.base_id)},
        connections=connections or None,
        manufacturer=sysinfo.get("BRAND") or MANUFACTURER,
        model=sysinfo.get("MODEL") or MODEL,
        name=entry.title,
        sw_version=sw_version,
        configuration_url=gateway.client.base_url,
    )
