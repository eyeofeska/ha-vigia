"""Config and options flow for Vigia."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import sources
from .const import (
    CONF_CONCELHO,
    CONF_EFFIS,
    CONF_FOGOS,
    CONF_FOGOS_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAP_KEY,
    CONF_RADIUS,
    CONF_WEATHER,
    DEFAULT_RADIUS,
    DOMAIN,
)


def _schema(hass: HomeAssistant, d: dict[str, Any]) -> vol.Schema:
    weather = d.get(CONF_WEATHER)
    if weather is None:
        ids = sorted(hass.states.async_entity_ids("weather"))
        weather = "weather.forecast_home" if "weather.forecast_home" in ids else (ids[0] if ids else None)
    fields: dict = {
        vol.Optional(CONF_MAP_KEY, description={"suggested_value": d.get(CONF_MAP_KEY)}): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_WEATHER, description={"suggested_value": weather}): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        vol.Required(CONF_RADIUS, default=d.get(CONF_RADIUS, DEFAULT_RADIUS)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=60, step=5, unit_of_measurement="km", mode=selector.NumberSelectorMode.SLIDER)
        ),
        vol.Required(CONF_FOGOS, default=d.get(CONF_FOGOS, True)): selector.BooleanSelector(),
        vol.Optional(CONF_FOGOS_KEY, description={"suggested_value": d.get(CONF_FOGOS_KEY)}): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_EFFIS, default=d.get(CONF_EFFIS, True)): selector.BooleanSelector(),
        vol.Optional(CONF_LATITUDE, description={"suggested_value": d.get(CONF_LATITUDE)}): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-90, max=90, step="any", mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Optional(CONF_LONGITUDE, description={"suggested_value": d.get(CONF_LONGITUDE)}): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-180, max=180, step="any", mode=selector.NumberSelectorMode.BOX)
        ),
        vol.Optional(CONF_CONCELHO, description={"suggested_value": d.get(CONF_CONCELHO)}): selector.TextSelector(),
    }
    return vol.Schema(fields)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    key = (data.get(CONF_MAP_KEY) or "").strip()
    if key:
        try:
            await sources.firms_check(async_get_clientsession(hass), key)
        except sources.InvalidKey:
            errors[CONF_MAP_KEY] = "invalid_key"
        except sources.SourceError:
            pass  # offline right now (Starlink asleep, say): accept and try later
    dico = (data.get(CONF_CONCELHO) or "").strip()
    if dico and not (dico.isdigit() and len(dico) == 4):
        errors[CONF_CONCELHO] = "bad_concelho"
    if (data.get(CONF_LATITUDE) is None) != (data.get(CONF_LONGITUDE) is None):
        errors["base"] = "both_coords"
    return errors


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out[CONF_MAP_KEY] = (out.get(CONF_MAP_KEY) or "").strip()
    out[CONF_FOGOS_KEY] = (out.get(CONF_FOGOS_KEY) or "").strip()
    out[CONF_CONCELHO] = (out.get(CONF_CONCELHO) or "").strip()
    out[CONF_RADIUS] = int(out.get(CONF_RADIUS) or DEFAULT_RADIUS)
    return out


class VigiaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="Vigia", data={}, options=_clean(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema(self.hass, user_input or {}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VigiaOptionsFlow()


class VigiaOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=_clean(user_input))
        current = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, current), errors=errors)
