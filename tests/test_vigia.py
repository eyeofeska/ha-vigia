"""Vigia end-to-end tests with canned feeds."""
from datetime import datetime, timedelta, timezone

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.vigia.const import DOMAIN, EVENT_ALERT
from custom_components.vigia.geo import offset

from .conftest import HOME


async def _setup(hass: HomeAssistant, **opts) -> MockConfigEntry:
    hass.config.latitude, hass.config.longitude = HOME
    entry = MockConfigEntry(domain=DOMAIN, title="Vigia", data={},
                            options={"map_key": "k", "radius_km": 30, "use_fogos": True, "use_effis": True, **opts})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _hotspot(dist, brg, hours_ago=1, i=0):
    lat, lon = offset(*HOME, dist, brg)
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"id": f"h{i}-{dist}-{brg}", "lat": lat, "lon": lon, "time": t.isoformat(), "sat": "NOAA-20", "frp": 9.0}


async def test_config_flow(hass, feeds):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    r2 = await hass.config_entries.flow.async_configure(r["flow_id"], {"map_key": "bad", "radius_km": 30, "use_fogos": True, "use_effis": True})
    assert r2["errors"] == {"map_key": "invalid_key"}
    r3 = await hass.config_entries.flow.async_configure(r["flow_id"], {"map_key": " good ", "radius_km": 30, "use_fogos": True, "use_effis": True})
    assert r3["type"] is FlowResultType.CREATE_ENTRY
    assert r3["options"]["map_key"] == "good"


async def test_quiet_day(hass, feeds):
    await _setup(hass)
    assert hass.states.get("sensor.vigia_fire_risk").state == "4"
    assert hass.states.get("sensor.vigia_fire_risk").attributes["risk"] == "very high"
    assert hass.states.get("sensor.vigia_fire_risk_tomorrow").state == "3"
    assert hass.states.get("sensor.vigia_weather_warning").state == "yellow"
    assert hass.states.get("sensor.vigia_alert_level").state == "none"
    assert hass.states.get("sensor.vigia_fires_nearby").state == "0"
    assert hass.states.get("number.vigia_close_radius").state == "2.0"
    assert hass.states.get("switch.vigia_test_mode").state == "off"


async def test_tiers_and_dedupe(hass, feeds):
    events = async_capture_events(hass, EVENT_ALERT)
    # wind from SW (225) at 12 km/h: a fire SW at 4 km is upwind; one NE at 4 km is not
    feeds["firms"] = [_hotspot(4, 225, i=1), _hotspot(4.1, 228, i=2), _hotspot(4, 45, i=3)]
    entry = await _setup(hass)
    c = entry.runtime_data
    assert hass.states.get("sensor.vigia_alert_level").state == "upwind"
    assert len(events) == 1, [e.data["title"] for e in events]  # two detections, one fire
    assert events[0].data["tier"] == "upwind" and events[0].data["direction"] == "SW"

    # nothing new: no repeat
    await c.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1

    # the fire spreads toward home: detections along its edge are the same fire
    async def add(d, i):
        c.cache["firms"]["data"].append(_hotspot(d, 225, i=i))
        c.async_set_updated_data(c._compute(evaluate=True))
        await hass.async_block_till_done()

    await add(3.4, 10)  # only 0.6 km closer: quiet
    assert len(events) == 1
    await add(2.6, 11)  # over 1 km closer: repeat, same tier
    assert len(events) == 2 and events[1].data["tier"] == "upwind" and events[1].data["repeat"]
    await add(1.8, 12)  # inside the close ring: repeat, higher tier
    assert len(events) == 3 and events[2].data["tier"] == "close" and events[2].data["repeat"]
    await add(1.7, 13)  # same tier, barely closer: quiet
    assert len(events) == 3

    # old detections never alert
    c.cache["firms"]["data"] = [_hotspot(3, 90, hours_ago=20, i=5)]
    c.alerted.clear()
    c.async_set_updated_data(c._compute(evaluate=True))
    await hass.async_block_till_done()
    assert len(events) == 3
    assert hass.states.get("sensor.vigia_fires_nearby").state == "1"


async def test_watch_needs_strong_wind(hass, feeds):
    events = async_capture_events(hass, EVENT_ALERT)
    feeds["firms"] = [_hotspot(12, 225)]
    entry = await _setup(hass)
    assert not events  # 12 km/h is under the 20 km/h watch wind
    await hass.services.async_call("number", "set_value", {"entity_id": "number.vigia_watch_minimum_wind", "value": 10}, blocking=True)
    await hass.async_block_till_done()
    assert len(events) == 1 and events[0].data["tier"] == "watch"
    assert entry.runtime_data.settings["watch_wind"] == 10


async def test_incidents(hass, feeds):
    events = async_capture_events(hass, EVENT_ALERT)
    lat, lon = offset(*HOME, 1.2, 10)
    feeds["fogos"] = [
        {"id": "1", "lat": lat, "lon": lon, "status": "Em Curso", "status_code": 5, "active": True, "place": "x", "concelho": "Ponte de Lima",
         "url": "https://fogos.pt/fogo/1", "time": None},
        {"id": "2", "lat": lat + 0.001, "lon": lon, "status": "Vigilância", "status_code": 9, "active": False, "place": "y", "concelho": "z",
         "url": "https://fogos.pt/fogo/2", "time": None},
    ]
    await _setup(hass)
    assert len(events) == 1
    assert events[0].data["tier"] == "close" and events[0].data["kind"] == "incident"
    assert events[0].data["url"] == "https://fogos.pt/fogo/1"


async def test_test_mode_and_button(hass, feeds):
    events = async_capture_events(hass, EVENT_ALERT)
    await _setup(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.vigia_test_mode"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.vigia_alert_level").state == "upwind"
    assert len(events) == 1 and events[0].data["test"] and events[0].data["title"].startswith("TEST")
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.vigia_test_mode"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.vigia_alert_level").state == "none"
    await hass.services.async_call("button", "press", {"entity_id": "button.vigia_send_test_alert"}, blocking=True)
    await hass.async_block_till_done()
    assert len(events) == 2 and events[1].data["key"] == "test:button"


async def test_offline_keeps_last_data(hass, feeds):
    entry = await _setup(hass)
    c = entry.runtime_data
    from custom_components.vigia import sources

    async def boom(*a, **k):
        raise sources.SourceError("ClientConnectorError")

    from unittest.mock import patch
    for c_ in c.cache.values():
        c_["fetched"] = 0
    with patch("custom_components.vigia.sources.ipma_risk", boom), patch("custom_components.vigia.sources.fogos", boom):
        await c.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get("sensor.vigia_fire_risk").state == "4"
    assert c.data["sources"]["fogos"]["ok"] is False and c.data["sources"]["fogos"]["updated"]


async def test_websocket(hass, hass_ws_client, feeds):
    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "vigia/subscribe"})
    msg = await ws.receive_json()
    assert msg["success"]
    msg = await ws.receive_json()
    ev = msg["event"]
    assert ev["risk"]["today"]["level"] == 4
    assert ev["entities"]["close_km"] == "number.vigia_close_radius"
    assert ev["entities"]["test_mode"] == "switch.vigia_test_mode"


async def test_no_key_disables_firms(hass, feeds):
    entry = await _setup(hass, map_key="")
    assert entry.runtime_data.data["sources"]["firms"]["enabled"] is False


async def test_rate_limit_backs_off(hass, feeds):
    from unittest.mock import patch
    from custom_components.vigia import sources

    entry = await _setup(hass)
    c = entry.runtime_data
    calls = []

    async def limited(*a, **k):
        calls.append(1)
        raise sources.RateLimited(None)

    c.cache["fogos"]["fetched"] = 0
    with patch("custom_components.vigia.sources.fogos", limited):
        await c.async_refresh()
        await c.async_refresh()  # still inside the back-off window: not called again
    assert len(calls) == 1
    assert c.data["sources"]["fogos"]["error"].startswith("rate limited")
    assert c.cache["fogos"]["backoff_until"] > 0


async def test_fogos_key_is_sent(hass, aioclient_mock):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from custom_components.vigia import sources
    from custom_components.vigia.const import URL_FOGOS

    aioclient_mock.get(URL_FOGOS, json={"success": True, "data": []})
    await sources.fogos(async_get_clientsession(hass), *HOME, 30, "abc")
    assert aioclient_mock.mock_calls[0][3]["X-API-Key"] == "abc"


async def test_card_resource_registered_and_versioned(hass, feeds, fake_frontend):
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from custom_components.vigia import _register_resource
    from custom_components.vigia.const import VERSION

    await _setup(hass)
    res = hass.data[LOVELACE_DATA].resources
    urls = [r["url"] for r in res.async_items()]
    assert urls == [f"/vigia_static/vigia-card.js?v={VERSION}"]
    fake_frontend.assert_not_called()  # no per-page script once it's a resource

    # an older version (and a stray duplicate) are folded into one current entry
    item = res.async_items()[0]
    await res.async_update_item(item["id"], {"res_type": "module", "url": "/vigia_static/vigia-card.js?v=0.0.1"})
    await res.async_create_item({"res_type": "module", "url": "/vigia_static/vigia-card.js?v=0.0.2"})
    assert await _register_resource(hass)
    assert [r["url"] for r in res.async_items()] == [f"/vigia_static/vigia-card.js?v={VERSION}"]
