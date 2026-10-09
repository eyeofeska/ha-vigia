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
from .water import Waterways

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
HERE = Path(__file__).parent


CARD_URL = f"{STATIC_URL}/{CARD_FILE}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card, load it into the dashboards, and register the websocket commands once."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(HERE / "frontend"), False)]
    )
    if not await _register_resource(hass):
        # dashboards kept in YAML: fall back to loading the card with every page
        add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_water)
    return True


def _resources(hass: HomeAssistant):
    """The dashboard resource collection, when it is UI-managed (storage mode)."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        data = hass.data.get(LOVELACE_DATA)
        res = data.resources if data else None
    except (ImportError, AttributeError):
        return None
    return res if res is not None and hasattr(res, "async_create_item") else None


async def _register_resource(hass: HomeAssistant) -> bool:
    """Add the card as a dashboard resource, or move the existing one to this version.

    The app fetches resources on every load, so a new version reaches phones and
    tablets without anyone clearing their cache. Returns False when resources are
    managed in YAML and can't be changed from here.
    """
    res = _resources(hass)
    if res is None:
        return False
    try:
        if not res.loaded:
            await res.async_load()
            res.loaded = True
        url = f"{CARD_URL}?v={VERSION}"
        mine = [r for r in res.async_items() if str(r.get("url", "")).split("?")[0] == CARD_URL]
        if not mine:
            await res.async_create_item({"res_type": "module", "url": url})
        elif mine[0].get("url") != url:
            await res.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
        for extra in mine[1:]:
            await res.async_delete_item(extra["id"])
    except Exception:  # noqa: BLE001  never let the dashboard resource stop Vigia loading
        _LOGGER.warning("Vigia could not register its card as a dashboard resource; loading it with every page instead", exc_info=True)
        return False
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Take the card's dashboard resource away when Vigia is removed."""
    res = _resources(hass)
    if res is None:
        return
    try:
        for r in list(res.async_items()):
            if str(r.get("url", "")).split("?")[0] == CARD_URL:
                await res.async_delete_item(r["id"])
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Vigia: could not remove its dashboard resource", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = VigiaCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    hass.data[DOMAIN] = coordinator
    coordinator.start_listeners()
    coordinator.water = Waterways(hass, entry.entry_id)
    await coordinator.water.async_load()
    coordinator.water.maybe_refresh(*coordinator.home, coordinator.radius)
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


@websocket_api.websocket_command({vol.Required("type"): "vigia/water"})
@websocket_api.async_response
async def ws_water(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Rivers, creeks and lakes for the map, asked for once when the map opens."""
    coordinator: VigiaCoordinator | None = hass.data.get(DOMAIN)
    water = getattr(coordinator, "water", None)
    if water is None:
        connection.send_error(msg["id"], "not_loaded", "Vigia is not set up")
        return
    water.maybe_refresh(*coordinator.home, coordinator.radius)
    connection.send_result(msg["id"], {"data": water.data, "info": water.info()})
