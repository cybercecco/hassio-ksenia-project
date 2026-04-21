"""Gateway object bridging :class:`LaresClient` with Home Assistant.

The gateway owns the WebSocket client for a single Lares panel and
exposes convenience accessors used by the entity platforms. Configuration
data (zones, partitions, scenarios) is cached in memory; real-time
updates refresh the cache and fire a dispatcher signal per key so that
entities refresh themselves without polling.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import LaresClient
from .const import DOMAIN, SCENARIO_CATEGORIES, SIGNAL_UPDATE

_LOGGER = logging.getLogger(__name__)


class LaresGateway:
    """Thin wrapper around :class:`LaresClient` with HA helpers."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: LaresClient) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self._unsub_connection = client.add_connection_listener(self._on_connection_change)
        for key in ("STATUS_ZONES", "STATUS_PARTITIONS", "STATUS_SYSTEM"):
            client.add_realtime_listener(key, self._on_realtime_update)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        await self.client.start()

    async def async_stop(self) -> None:
        if self._unsub_connection:
            self._unsub_connection()
            self._unsub_connection = None
        await self.client.stop()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    @callback
    def _on_realtime_update(self, key: str, _value: list[dict[str, Any]]) -> None:
        async_dispatcher_send(self.hass, self._signal(key))

    @callback
    def _on_connection_change(self, is_connected: bool) -> None:
        _LOGGER.debug("Lares gateway connection state: %s", is_connected)
        async_dispatcher_send(self.hass, self._signal("CONNECTION"), is_connected)

    def _signal(self, key: str) -> str:
        return f"{SIGNAL_UPDATE}_{self.entry_id}_{key}"

    def signal_update(self, key: str) -> str:
        """Return the dispatcher signal used for *key* updates."""
        return self._signal(key)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        return self.client.host

    @property
    def system_info(self) -> dict[str, Any]:
        return self.client.system_info

    @property
    def mac(self) -> str | None:
        mac = self.client.system_info.get("MAC")
        return mac if isinstance(mac, str) and mac else None

    @property
    def base_id(self) -> str:
        return self.mac or f"{self.client.host}:{self.client.port}"

    def list_zones(self) -> list[dict[str, Any]]:
        return self.client.get_snapshot("ZONES")

    def list_partitions(self) -> list[dict[str, Any]]:
        return self.client.get_snapshot("PARTITIONS")

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self.client.get_snapshot("SCENARIOS")

    def zone_status(self, zone_id: str | int) -> dict[str, Any] | None:
        zid = str(zone_id)
        for z in self.client.get_snapshot("STATUS_ZONES"):
            if str(z.get("ID")) == zid:
                return z
        return None

    def partition_status(self, partition_id: str | int) -> dict[str, Any] | None:
        pid = str(partition_id)
        for p in self.client.get_snapshot("STATUS_PARTITIONS"):
            if str(p.get("ID")) == pid:
                return p
        return None

    def system_status(self) -> dict[str, Any]:
        snapshot = self.client.get_snapshot("STATUS_SYSTEM")
        return snapshot[0] if snapshot else {}

    # ------------------------------------------------------------------
    # Scenario mapping used by the alarm control panel
    # ------------------------------------------------------------------

    def scenario_by_category(self) -> dict[str, str]:
        """Return ``{CAT: scenario_id}`` for scenarios with known CATs.

        Ksenia scenarios carry a ``CAT`` attribute (``DISARM``, ``ARM``,
        ``PARTIAL``) used by the panel UI to drive alarm state. We mirror
        the same mapping for the HA alarm panel entity.
        """
        mapping: dict[str, str] = {}
        for scenario in self.list_scenarios():
            cat = (scenario.get("CAT") or "").upper()
            sid = scenario.get("ID")
            if cat in SCENARIO_CATEGORIES and sid is not None:
                mapping[cat] = str(sid)
        return mapping


def get_gateway(hass: HomeAssistant, entry_id: str) -> LaresGateway:
    """Return the gateway registered for *entry_id*."""
    return hass.data[DOMAIN][entry_id]
