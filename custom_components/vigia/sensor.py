"""Vigia sensors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TIERS
from .entity import VigiaEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    add([
        RiskSensor(c, "fire_risk", "Fire risk", "today"),
        RiskSensor(c, "fire_risk_tomorrow", "Fire risk tomorrow", "tomorrow"),
        WarningSensor(c, "weather_warning", "Weather warning"),
        AlertSensor(c, "alert_level", "Alert level"),
        NearestSensor(c, "nearest_fire", "Nearest fire"),
        CountSensor(c, "fires_nearby", "Fires nearby"),
    ])


class RiskSensor(VigiaEntity, SensorEntity):
    """IPMA fire risk class, 1 (low) to 5 (maximum)."""

    _attr_icon = "mdi:fire"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c, key, name, day) -> None:
        super().__init__(c, key, name)
        self.day = day

    @property
    def _r(self) -> dict:
        return (self.coordinator.data.get("risk") or {}).get(self.day) or {}

    @property
    def native_value(self) -> int | None:
        return self._r.get("level")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"risk": self._r.get("name"), "date": self._r.get("date"),
                "concelho_code": (self.coordinator.data.get("risk") or {}).get("dico"), "attribution": "IPMA"}


class WarningSensor(VigiaEntity, SensorEntity):
    _attr_icon = "mdi:alert-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["green", "yellow", "orange", "red"]

    @property
    def native_value(self) -> str:
        return self.coordinator.data.get("warning_level") or "green"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"warnings": self.coordinator.data.get("warnings") or [], "attribution": "IPMA"}


class AlertSensor(VigiaEntity, SensorEntity):
    """The highest alert tier right now: none, watch, upwind or close."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TIERS

    @property
    def native_value(self) -> str:
        return self.coordinator.data["alert"]["tier"]

    @property
    def icon(self) -> str:
        return "mdi:fire-alert" if self.native_value != "none" else "mdi:shield-check-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self.coordinator.data["alert"].get("threat") or {}
        return {k: t.get(k) for k in ("title", "message", "kind", "distance_km", "direction", "upwind", "url", "test")}


class NearestSensor(VigiaEntity, SensorEntity):
    """Distance to the nearest active incident or recent hotspot."""

    _attr_icon = "mdi:map-marker-distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        n = self.coordinator.data.get("nearest")
        return n["distance"] if n else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        n = self.coordinator.data.get("nearest") or {}
        return {"direction": n.get("dir"), "upwind": n.get("upwind"), "source": n.get("kind")}


class CountSensor(VigiaEntity, SensorEntity):
    """Active incidents plus satellite hotspots from the last 24 h within the map radius."""

    _attr_icon = "mdi:fire-circle"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        c = self.coordinator.data.get("counts") or {}
        return (c.get("incidents") or 0) + (c.get("hotspots") or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator.data.get("counts") or {}
        return {"incidents": c.get("incidents"), "hotspots_24h": c.get("hotspots"),
                "radius_km": self.coordinator.data.get("radius_km"), "test_mode": self.coordinator.data.get("test_mode")}
