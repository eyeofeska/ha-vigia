"""Test mode: a pretend fire for trying the tile, map and alerts."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VigiaEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([TestModeSwitch(entry.runtime_data, "test_mode", "Test mode")])


class TestModeSwitch(VigiaEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fire-off"

    @property
    def is_on(self) -> bool:
        return self.coordinator.test_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_test_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_test_mode(False)
