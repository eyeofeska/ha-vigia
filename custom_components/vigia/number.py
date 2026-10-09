"""Alert settings as number entities, so the tile and automations share them."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SETTINGS
from .entity import VigiaEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add(SettingNumber(entry.runtime_data, k) for k in SETTINGS)


class SettingNumber(VigiaEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, key: str) -> None:
        default, lo, hi, step, unit, name, icon = SETTINGS[key]
        super().__init__(coordinator, key, name)
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = lo, hi, step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return self.coordinator.settings[self.key]

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_setting(self.key, value)
