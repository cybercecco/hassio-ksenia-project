"""Buttons for executing Ksenia Lares 4.0 scenarios.

Every scenario configured on the panel is exposed as a button so users
can wire them into Home Assistant automations and dashboards. Scenarios
that belong to the core alarm workflow (DISARM/ARM/PARTIAL) are still
exposed as individual buttons in addition to the main alarm panel.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import LaresAuthError, LaresError
from .const import DOMAIN
from .entity import LaresEntity, unique_id
from .gateway import LaresGateway


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    gateway: LaresGateway = hass.data[DOMAIN][entry.entry_id]
    entities: list[LaresScenarioButton] = []
    for scenario in gateway.list_scenarios():
        if scenario.get("ID") is None:
            continue
        if _is_hidden(scenario):
            continue
        entities.append(LaresScenarioButton(gateway, scenario))
    async_add_entities(entities)


def _is_hidden(scenario: dict[str, Any]) -> bool:
    if scenario.get("HID") in (True, "true", "1"):
        return True
    # CAT=="H" is used by the panel for hidden entries.
    return str(scenario.get("CAT", "")).upper() == "H"


class LaresScenarioButton(LaresEntity, ButtonEntity):
    """Trigger a single panel scenario on press."""

    _attr_translation_key = "scenario"

    def __init__(self, gateway: LaresGateway, scenario: dict[str, Any]) -> None:
        super().__init__(gateway)
        self._scenario_id = str(scenario["ID"])
        self._attr_unique_id = unique_id(gateway.base_id, "scenario", self._scenario_id)
        self._attr_name = (
            scenario.get("DES")
            or scenario.get("LBL")
            or scenario.get("NM")
            or f"Scenario {self._scenario_id}"
        )
        self._category = (scenario.get("CAT") or "").upper() or None
        self._attr_extra_state_attributes = {
            "id": self._scenario_id,
            "category": self._category,
        }

    async def async_press(self) -> None:
        try:
            success = await self.gateway.client.execute_scenario(self._scenario_id)
        except LaresAuthError as err:
            raise HomeAssistantError("PIN rejected by the panel") from err
        except LaresError as err:
            raise HomeAssistantError(str(err)) from err
        if not success:
            raise HomeAssistantError(
                f"Panel rejected scenario {self._scenario_id}"
            )
