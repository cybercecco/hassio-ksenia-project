"""Shared entity helpers for the Ksenia Lares 4.0 gateway integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .gateway import LaresGateway


class LaresEntity(Entity):
    """Base entity whose availability follows the gateway WebSocket."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, gateway: LaresGateway) -> None:
        self.gateway = gateway

    @property
    def available(self) -> bool:
        return self.gateway.client.connected

    @property
    def device_info(self) -> DeviceInfo:
        sysinfo: dict[str, Any] = self.gateway.system_info
        sw_version = None
        ver_lite = sysinfo.get("VER_LITE")
        if isinstance(ver_lite, dict):
            sw_version = ver_lite.get("FW")
        connections = set()
        if self.gateway.mac:
            connections.add((CONNECTION_NETWORK_MAC, self.gateway.mac))
        return DeviceInfo(
            identifiers={(DOMAIN, self.gateway.base_id)},
            connections=connections or None,
            manufacturer=sysinfo.get("BRAND") or MANUFACTURER,
            model=sysinfo.get("MODEL") or MODEL,
            name=f"Ksenia Lares 4.0 ({self.gateway.host})",
            sw_version=sw_version,
            configuration_url=self.gateway.client.base_url,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.gateway.signal_update("CONNECTION"),
                self._handle_connection_update,
            )
        )

    @callback
    def _handle_connection_update(self, _connected: bool) -> None:
        self.async_write_ha_state()

    def _listen(self, key: str) -> None:
        """Subscribe the entity to dispatcher updates for *key*."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.gateway.signal_update(key),
                self._handle_realtime_update,
            )
        )

    @callback
    def _handle_realtime_update(self, *_args: Any) -> None:
        self.async_write_ha_state()


def unique_id(base: str, *parts: str | int) -> str:
    """Build a consistent unique_id for an entity."""
    return "_".join([base, *(str(p) for p in parts)])
