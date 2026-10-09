"""Base entity for Vigia."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import VigiaCoordinator


class VigiaEntity(CoordinatorEntity[VigiaCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: VigiaCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Vigia",
            manufacturer="Vigia",
            model="Wildfire watch",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://github.com/eyeofeska/ha-vigia",
        )
