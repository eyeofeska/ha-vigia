"""Send a test alert through whatever automations listen for vigia_alert."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VigiaEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([TestAlertButton(entry.runtime_data, "test_alert", "Send test alert")])


class TestAlertButton(VigiaEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:bell-ring-outline"

    async def async_press(self) -> None:
        self.coordinator.fire_test_alert()
