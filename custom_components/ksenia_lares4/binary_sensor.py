"""Binary sensors representing Ksenia Lares 4.0 zones.

Each configured zone is exposed as a binary_sensor whose state reflects
the live ``STATUS_ZONES`` broadcast. The device class is guessed from
the zone ``CAT`` (category) so door/window zones show up as openings,
motion zones as motion, smoke zones as smoke, etc.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import LaresEntity, unique_id
from .gateway import LaresGateway

ZONE_DEVICE_CLASSES = {
    "DOOR": BinarySensorDeviceClass.DOOR,
    "WINDOW": BinarySensorDeviceClass.WINDOW,
    "IMOV": BinarySensorDeviceClass.MOTION,
    "EMOV": BinarySensorDeviceClass.MOTION,
    "PMC": BinarySensorDeviceClass.MOTION,
    "SMOKE": BinarySensorDeviceClass.SMOKE,
    "SEISM": BinarySensorDeviceClass.VIBRATION,
    "FLOOD": BinarySensorDeviceClass.MOISTURE,
    "GAS": BinarySensorDeviceClass.GAS,
}

OPEN_STATES = {"AL", "OPEN", "ON", "OPENED", "TRIGGERED"}
"""Raw ``STA`` tokens that should map to ``is_on=True``."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: LaresGateway = hass.data[DOMAIN][entry.entry_id]
    entities: list[LaresZoneBinarySensor] = []
    for zone in gateway.list_zones():
        zone_id = zone.get("ID")
        if zone_id is None:
            continue
        entities.append(LaresZoneBinarySensor(gateway, zone))
    async_add_entities(entities)


class LaresZoneBinarySensor(LaresEntity, BinarySensorEntity):
    """Binary sensor for a single zone on the Ksenia panel."""

    def __init__(self, gateway: LaresGateway, zone: dict[str, Any]) -> None:
        super().__init__(gateway)
        self._zone_id = str(zone["ID"])
        self._attr_unique_id = unique_id(gateway.base_id, "zone", self._zone_id)
        self._attr_name = (
            zone.get("DES") or zone.get("LBL") or zone.get("NM") or f"Zone {self._zone_id}"
        )
        cat = (zone.get("CAT") or "").upper()
        self._attr_device_class = ZONE_DEVICE_CLASSES.get(cat)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._listen("STATUS_ZONES")

    @property
    def is_on(self) -> bool | None:
        status = self.gateway.zone_status(self._zone_id)
        if not status:
            return None
        state = str(status.get("STA") or status.get("STATUS") or "").upper()
        if not state:
            return None
        return state in OPEN_STATES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.gateway.zone_status(self._zone_id) or {}
        return {
            "id": self._zone_id,
            "raw_status": status.get("STA"),
            "bypass": status.get("BYP"),
            "tamper": status.get("TST") or status.get("TAM"),
            "trouble": status.get("TRB") or status.get("TROUBLE"),
            "alarm": status.get("AST"),
        }
