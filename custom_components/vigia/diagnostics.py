"""Diagnostics: source health and the current picture, with the map key and home position redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_FOGOS_KEY, CONF_LATITUDE, CONF_LONGITUDE, CONF_MAP_KEY

REDACT = {CONF_MAP_KEY, CONF_FOGOS_KEY, CONF_LATITUDE, CONF_LONGITUDE, "home", "lat", "lon"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    c = entry.runtime_data
    d = c.data or {}
    return {
        "options": async_redact_data(dict(entry.options), REDACT),
        "sources": d.get("sources"),
        "settings": d.get("settings"),
        "wind": d.get("wind"),
        "risk": d.get("risk"),
        "warning_level": d.get("warning_level"),
        "alert": async_redact_data(d.get("alert") or {}, REDACT),
        "counts": d.get("counts"),
        "burnt_areas": len(d.get("burnt") or []),
        "burnt_dates": sorted({f["properties"].get("date", "")[:7] for f in d.get("burnt") or []}),
        "burnt_vertices": sum(len(str(f["geometry"]["coordinates"])) // 20 for f in d.get("burnt") or []),
        "wind_grid_points": len(d.get("wind_grid") or []),
        "alerted_fires": len(c.alerted),
        "test_mode": c.test_mode,
    }
