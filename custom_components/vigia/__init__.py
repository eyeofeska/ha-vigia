"""Vigia: wildfire watch for Home Assistant."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILE, DOMAIN, STATIC_URL, VERSION
from .coordinator import VigiaCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
HERE = Path(__file__).parent


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card and register the websocket commands once."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(HERE / "frontend"), False)]
    )
    add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={VERSION}")
    websocket_api.async_register_command(hass, ws_subscribe)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = VigiaCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    hass.data[DOMAIN] = coordinator
    coordinator.start_listeners()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.async_add_executor_job(_install_blueprint, hass.config.path("blueprints", "automation", DOMAIN))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: VigiaCoordinator = entry.runtime_data
    coordinator.stop_listeners()
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data.pop(DOMAIN, None)
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _install_blueprint(target: str) -> None:
    """Copy the alert blueprint into the config folder the first time (never overwrites edits)."""
    src = HERE / "blueprints" / "fire_alert.yaml"
    dst = Path(target) / "fire_alert.yaml"
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _entities(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Map each entity key (e.g. close_km, test_mode) to its entity_id, whatever the user renamed it to."""
    reg = er.async_get(hass)
    out = {}
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        key = e.unique_id.removeprefix(f"{entry.entry_id}_")
        out[key] = e.entity_id
    return out


@websocket_api.websocket_command({vol.Required("type"): "vigia/subscribe"})
@websocket_api.async_response
async def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Send the card the full picture now and after every update."""
    coordinator: VigiaCoordinator | None = hass.data.get(DOMAIN)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Vigia is not set up")
        return

    @callback
    def push() -> None:
        if coordinator.data is not None:
            connection.send_message(websocket_api.event_message(
                msg["id"], {**coordinator.data, "entities": _entities(hass, coordinator.entry), "version": VERSION},
            ))

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(push)
    connection.send_result(msg["id"])
    push()
