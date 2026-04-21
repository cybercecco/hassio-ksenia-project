"""Diagnostic sensors for the Ksenia Lares 4.0 gateway.

Currently exposed:

* One sensor per partition reporting the live ARM state
  (``disarmed``, ``armed_away``, ``armed_home`` …).
* A single sensor for the overall panel ARM state.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, AlarmFlag, ArmCode
from .entity import LaresEntity, unique_id
from .gateway import LaresGateway

ARM_STATE_LABELS: dict[str, str] = {
    ArmCode.DISARMED.value: "disarmed",
    ArmCode.FULLY_ARMED.value: "armed_away",
    ArmCode.PARTIALLY_ARMED.value: "armed_home",
    ArmCode.FULLY_ARMED_ENTRY_DELAY.value: "entry_delay",
    ArmCode.PARTIALLY_ARMED_ENTRY_DELAY.value: "entry_delay",
    ArmCode.FULLY_ARMED_EXIT_DELAY.value: "exit_delay",
    ArmCode.PARTIALLY_ARMED_EXIT_DELAY.value: "exit_delay",
    ArmCode.IMMEDIATE_ARMING.value: "arming",
    ArmCode.DELAYED_ARMING.value: "arming",
    ArmCode.ENTRY_DELAY_ACTIVE.value: "entry_delay",
    ArmCode.EXIT_DELAY_ACTIVE.value: "exit_delay",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: LaresGateway = hass.data[DOMAIN][entry.entry_id]
    entities: list[LaresEntity] = [LaresSystemArmSensor(gateway)]
    for partition in gateway.list_partitions():
        if partition.get("ID") is None:
            continue
        entities.append(LaresPartitionSensor(gateway, partition))
    async_add_entities(entities)


class LaresPartitionSensor(LaresEntity, SensorEntity):
    """Reports the arm/alarm state of a single partition."""

    _attr_translation_key = "partition"

    def __init__(self, gateway: LaresGateway, partition: dict[str, Any]) -> None:
        super().__init__(gateway)
        self._partition_id = str(partition["ID"])
        self._attr_unique_id = unique_id(gateway.base_id, "partition", self._partition_id)
        self._attr_name = (
            partition.get("DES")
            or partition.get("LBL")
            or partition.get("NM")
            or f"Partition {self._partition_id}"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._listen("STATUS_PARTITIONS")

    @property
    def native_value(self) -> str | None:
        status = self.gateway.partition_status(self._partition_id)
        if not status:
            return None
        if status.get("AST") == AlarmFlag.ONGOING.value:
            return "triggered"
        arm = status.get("ARM")
        return ARM_STATE_LABELS.get(arm, arm)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.gateway.partition_status(self._partition_id) or {}
        return {
            "id": self._partition_id,
            "arm": status.get("ARM"),
            "alarm_status": status.get("AST"),
            "tamper_status": status.get("TST"),
        }


class LaresSystemArmSensor(LaresEntity, SensorEntity):
    """Global panel arm-state sensor derived from STATUS_SYSTEM."""

    _attr_translation_key = "system_arm"
    _attr_name = "Panel arm state"

    def __init__(self, gateway: LaresGateway) -> None:
        super().__init__(gateway)
        self._attr_unique_id = unique_id(gateway.base_id, "system_arm")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._listen("STATUS_SYSTEM")
        self._listen("STATUS_PARTITIONS")

    @property
    def native_value(self) -> str | None:
        system = self.gateway.system_status()
        arm_info = system.get("ARM") if isinstance(system, dict) else None
        arm_code = arm_info.get("S") if isinstance(arm_info, dict) else None
        if arm_code is None:
            return None
        return ARM_STATE_LABELS.get(arm_code, arm_code)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        system = self.gateway.system_status()
        if not isinstance(system, dict):
            return {}
        return {
            "arm": system.get("ARM"),
            "info_flags": system.get("INFO"),
        }
