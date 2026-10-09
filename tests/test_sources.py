"""Parsing of the real feed formats."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.vigia import sources
from custom_components.vigia.const import URL_FOGOS, URL_IPMA_WARNINGS

HOME = (41.7675, -8.5831)


def _firms_url(key, src):
    from custom_components.vigia.const import URL_FIRMS
    from custom_components.vigia.geo import bbox
    w, s, e, n = bbox(*HOME, 30)
    return URL_FIRMS.format(key=key, source=src, bbox=f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}")


async def test_ipma_risk_nearest_concelho(hass, aioclient_mock):
    for day, lvl in ((0, 2), (1, 4)):
        aioclient_mock.get(f"https://api.ipma.pt/open-data/forecast/meteorology/rcm/rcm-d{day}.json", text=json.dumps({
            "dataPrev": f"2026-10-0{9 + day}", "local": {
                "0101": {"data": {"rcm": 5}, "dico": "0101", "latitude": 40.58, "longitude": -8.44},
                "1607": {"data": {"rcm": lvl}, "dico": "1607", "latitude": 41.7652, "longitude": -8.5786},
            }}))
    r = await sources.ipma_risk(async_get_clientsession(hass), *HOME, None)
    assert r["dico"] == "1607"
    assert r["today"] == {"level": 2, "name": "moderate", "date": "2026-10-09"}
    assert r["tomorrow"]["name"] == "very high"


async def test_ipma_warnings(hass, aioclient_mock):
    now = datetime.now(timezone.utc)
    f = lambda d: (now + d).strftime("%Y-%m-%dT%H:%M:%S")
    aioclient_mock.get(URL_IPMA_WARNINGS, text=json.dumps([
        {"text": "", "awarenessTypeName": "Vento", "idAreaAviso": "VCT", "startTime": f(timedelta(hours=-2)), "awarenessLevelID": "yellow", "endTime": f(timedelta(hours=5))},
        {"text": "x", "awarenessTypeName": "Tempo Quente", "idAreaAviso": "VCT", "startTime": f(timedelta(hours=2)), "awarenessLevelID": "orange", "endTime": f(timedelta(hours=9))},
        {"text": "", "awarenessTypeName": "Nevoeiro", "idAreaAviso": "VCT", "startTime": f(timedelta(hours=-2)), "awarenessLevelID": "green", "endTime": f(timedelta(hours=5))},
        {"text": "", "awarenessTypeName": "Vento", "idAreaAviso": "BRG", "startTime": f(timedelta(hours=-2)), "awarenessLevelID": "red", "endTime": f(timedelta(hours=5))},
        {"text": "", "awarenessTypeName": "Vento", "idAreaAviso": "VCT", "startTime": f(timedelta(hours=-9)), "awarenessLevelID": "red", "endTime": f(timedelta(hours=-3))},
    ]))
    w = await sources.ipma_warnings(async_get_clientsession(hass), "VCT")
    assert [x["level"] for x in w] == ["orange", "yellow"]


async def test_fogos(hass, aioclient_mock):
    aioclient_mock.get(URL_FOGOS, text=json.dumps({"success": True, "data": [
        {"id": "20261", "lat": "41.78", "lng": "-8.60", "statusCode": 5, "status": "Em Curso", "concelho": "Ponte de Lima",
         "freguesia": "Arcozelo", "localidade": "Lugar", "man": 20, "terrain": 6, "aerial": 1, "natureza": "Mato",
         "dateTime": {"sec": 1791500000}, "updated": {"sec": 1791503600}},
        {"id": "20262", "lat": 37.48, "lng": -8.72, "statusCode": 9, "status": "Vigilância"},
    ]}))
    out = await sources.fogos(async_get_clientsession(hass), *HOME, 30)
    assert len(out) == 1
    f = out[0]
    assert f["active"] and f["people"] == 20 and f["url"] == "https://fogos.pt/fogo/20261"
    assert f["place"] == "Lugar, Arcozelo, Ponte de Lima"


async def test_firms_csv(hass, aioclient_mock):
    csv = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
           "41.78,-8.61,340.1,0.4,0.4,2026-10-09,142,N20,VIIRS,n,2.0NRT,290.0,8.31,D\n"
           "41.79,-8.62,300.1,0.4,0.4,2026-10-09,1342,N20,VIIRS,l,2.0NRT,290.0,1.0,D\n")
    aioclient_mock.get(_firms_url("KEY", "VIIRS_NOAA20_NRT"), text=csv)
    aioclient_mock.get(_firms_url("KEY", "VIIRS_NOAA21_NRT"), text=csv.split("\n")[0] + "\n")
    aioclient_mock.get(_firms_url("KEY", "VIIRS_SNPP_NRT"), status=500)
    out = await sources.firms(async_get_clientsession(hass), "KEY", *HOME, 30)
    assert len(out) == 1  # the low-confidence row is dropped
    h = out[0]
    assert h["sat"] == "NOAA-20" and h["frp"] == 8.3
    assert h["time"].startswith("2026-10-09T01:42")


async def test_firms_bad_key(hass, aioclient_mock):
    aioclient_mock.get(_firms_url("BAD", "VIIRS_NOAA20_NRT"), text="Invalid MAP_KEY.")
    with pytest.raises(sources.InvalidKey):
        await sources.firms(async_get_clientsession(hass), "BAD", *HOME, 30)


async def test_effis_thins_and_filters_year(hass, aioclient_mock):
    from datetime import datetime
    from custom_components.vigia.const import URL_EFFIS
    y = datetime.now().year
    ring = [[-8.6 + i * 0.00001, 41.7] for i in range(200)] + [[-8.59, 41.71], [-8.6, 41.71], [-8.6, 41.7]]
    feats = [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"FIREDATE": f"{y}-08-12 00:00:00", "AREA_HA": 140}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"FIREDATE": f"{y - 1}-08-12 00:00:00", "AREA_HA": 99}},
    ]
    aioclient_mock.get(URL_EFFIS, json={"type": "FeatureCollection", "features": feats})
    out = await sources.effis(async_get_clientsession(hass), *HOME, 30)
    assert len(out) == 1 and out[0]["properties"]["area_ha"] == 140
    assert len(out[0]["geometry"]["coordinates"][0]) < 20
