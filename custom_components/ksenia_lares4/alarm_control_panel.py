"""Alarm control panel entity for the Ksenia Lares 4.0 gateway.

A single entity represents the full panel; arming/disarming is driven
by the panel's own scenarios tagged with ``CAT == DISARM/ARM/PARTIAL``.
State is derived live from ``STATUS_SYSTEM`` and ``STATUS_PARTITIONS``
real-time broadcasts.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import LaresAuthError, LaresError
from .const import AlarmFlag, ArmCode, DOMAIN
from .entity import LaresEntity, unique_id
from .gateway import LaresGateway

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: LaresGateway = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LaresAlarmPanel(gateway)])


class LaresAlarmPanel(LaresEntity, AlarmControlPanelEntity):
    """Single alarm panel entity for the whole Ksenia system."""

    _attr_translation_key = "alarm_control_panel"
    _attr_name = "Alarm"
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True

    def __init__(self, gateway: LaresGateway) -> None:
        super().__init__(gateway)
        self._attr_unique_id = unique_id(gateway.base_id, "alarm")
        self._state: AlarmControlPanelState | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._listen("STATUS_SYSTEM")
        self._listen("STATUS_PARTITIONS")
        self._compute_state()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        return self._state

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        mapping = self.gateway.scenario_by_category()
        feats = AlarmControlPanelEntityFeature(0)
        if "ARM" in mapping:
            feats |= AlarmControlPanelEntityFeature.ARM_AWAY
        if "PARTIAL" in mapping:
            feats |= AlarmControlPanelEntityFeature.ARM_HOME
        return feats

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        partitions: list[dict[str, Any]] = []
        for p in self.gateway.list_partitions():
            pid = p.get("ID")
            status = self.gateway.partition_status(pid) if pid is not None else None
            partitions.append(
                {
                    "id": pid,
                    "name": p.get("DES") or p.get("LBL") or p.get("NM"),
                    "arm": (status or {}).get("ARM"),
                    "alarm": (status or {}).get("AST"),
                    "tamper": (status or {}).get("TST"),
                }
            )
        system = self.gateway.system_status()
        arm_info = system.get("ARM") if isinstance(system, dict) else None
        return {
            "panel_arm_state": arm_info.get("S") if isinstance(arm_info, dict) else None,
            "partitions": partitions,
        }

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._run_scenario("DISARM", code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._run_scenario("ARM", code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._run_scenario("PARTIAL", code)

    async def _run_scenario(self, category: str, code: str | None) -> None:
        if not code:
            raise HomeAssistantError("A PIN is required to operate the alarm panel")
        scenarios = self.gateway.scenario_by_category()
        scenario_id = scenarios.get(category)
        if scenario_id is None:
            raise HomeAssistantError(
                f"No scenario with CAT={category!r} configured on the panel"
            )
        try:
            success = await self.gateway.client.execute_scenario(scenario_id, pin=code)
        except LaresAuthError as err:
            raise HomeAssistantError("PIN rejected by the panel") from err
        except LaresError as err:
            raise HomeAssistantError(str(err)) from err
        if not success:
            raise HomeAssistantError(
                f"Panel rejected scenario {scenario_id} ({category})"
            )

    # ------------------------------------------------------------------
    # State computation
    # ------------------------------------------------------------------

    def _handle_realtime_update(self, *_args: Any) -> None:
        self._compute_state()
        self.async_write_ha_state()

    def _compute_state(self) -> None:
        system = self.gateway.system_status()
        arm_data = system.get("ARM") if isinstance(system, dict) else None
        arm_code = arm_data.get("S") if isinstance(arm_data, dict) else None

        if self._any_partition_in_alarm():
            self._state = AlarmControlPanelState.TRIGGERED
            return
        if arm_code == ArmCode.DISARMED.value:
            self._state = AlarmControlPanelState.DISARMED
            return
        if arm_code in (
            ArmCode.FULLY_ARMED_ENTRY_DELAY.value,
            ArmCode.PARTIALLY_ARMED_ENTRY_DELAY.value,
        ):
            self._state = AlarmControlPanelState.PENDING
            return
        if arm_code in (
            ArmCode.FULLY_ARMED_EXIT_DELAY.value,
            ArmCode.PARTIALLY_ARMED_EXIT_DELAY.value,
        ):
            self._state = AlarmControlPanelState.ARMING
            return
        if arm_code == ArmCode.FULLY_ARMED.value:
            self._state = AlarmControlPanelState.ARMED_AWAY
            return
        if arm_code == ArmCode.PARTIALLY_ARMED.value:
            self._state = AlarmControlPanelState.ARMED_HOME
            return
        # Fall back to aggregating partitions if STATUS_SYSTEM is sparse.
        self._state = self._derive_state_from_partitions()

    def _any_partition_in_alarm(self) -> bool:
        for p in self.gateway.client.get_snapshot("STATUS_PARTITIONS"):
            if p.get("AST") == AlarmFlag.ONGOING.value:
                return True
        return False

    def _derive_state_from_partitions(self) -> AlarmControlPanelState | None:
        partitions = self.gateway.client.get_snapshot("STATUS_PARTITIONS")
        if not partitions:
            return None
        states = {p.get("ARM") for p in partitions}
        if states == {ArmCode.DISARMED.value}:
            return AlarmControlPanelState.DISARMED
        if {ArmCode.IMMEDIATE_ARMING.value, ArmCode.DELAYED_ARMING.value} & states:
            return AlarmControlPanelState.ARMED_AWAY
        if {ArmCode.EXIT_DELAY_ACTIVE.value} & states:
            return AlarmControlPanelState.ARMING
        if {ArmCode.ENTRY_DELAY_ACTIVE.value} & states:
            return AlarmControlPanelState.PENDING
        return None
