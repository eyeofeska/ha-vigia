"""Waterways: parsing, packing and serving."""
from unittest.mock import patch

from custom_components.vigia import water

HOME = (41.8267, -8.3446)


def _way(kind, pts, **tags):
    t = {"waterway": kind} if kind else {}
    t.update(tags)
    return {"type": "way", "tags": t, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


def unpack(f, origin):
    a, b, out = round(origin[0] * 1e4), round(origin[1] * 1e4), []
    for i in range(0, len(f), 2):
        a += f[i]; b += f[i + 1]
        out.append([a / 1e4, b / 1e4])
    return out


def test_parse_lines_areas_and_relation_rings():
    river = [(41.80 + i * 0.0001, -8.40 + i * 0.0001) for i in range(100)]  # dense: thinned
    creek = [(41.83 + i * 0.0002, -8.34) for i in range(50)]
    lake = [(41.81, -8.36), (41.81, -8.35), (41.815, -8.35), (41.815, -8.36), (41.81, -8.36)]
    pond = [(41.82, -8.33), (41.82, -8.3299), (41.8201, -8.3299), (41.82, -8.33)]  # ~10 m: dropped
    # a river bank split into two open member ways, one reversed, plus an island
    bank = {"type": "relation", "tags": {"natural": "water", "water": "river"}, "members": [
        {"type": "way", "role": "outer", "geometry": [{"lat": 41.77, "lon": -8.45}, {"lat": 41.77, "lon": -8.40}, {"lat": 41.775, "lon": -8.40}]},
        {"type": "way", "role": "outer", "geometry": [{"lat": 41.77, "lon": -8.45}, {"lat": 41.775, "lon": -8.45}, {"lat": 41.775, "lon": -8.40}]},
        {"type": "way", "role": "inner", "geometry": [{"lat": 41.772, "lon": -8.43}, {"lat": 41.772, "lon": -8.42}, {"lat": 41.773, "lon": -8.42}, {"lat": 41.772, "lon": -8.43}]},
    ]}
    data = {"elements": [_way("river", river, name="Rio Vez"), _way("stream", creek), _way("canal", creek),
                         _way(None, lake, natural="water", water="reservoir"), _way(None, pond, natural="water", water="pond"), bank]}
    out = water.parse(data, HOME)
    assert out["o"] == [41.8267, -8.3446]
    assert len(out["r"]) == 1 and out["r"][0]["n"] == "Rio Vez"
    r = unpack(out["r"][0]["c"], HOME)
    assert r[0] == [41.8, -8.4] and r[-1] == [41.8099, -8.3901]
    assert len(r) <= 51  # points ~19 m apart: every other one kept
    s = unpack(out["s"][0], HOME)
    assert s[0] == [41.83, -8.34] and len(s) < len(creek) // 2  # creeks thinned harder
    assert len(out["k"]) == 1
    assert len(out["a"]) == 2  # lake + bank; the pond is too small
    bank_poly = out["a"][1]
    assert len(bank_poly) == 2  # outer ring + island hole
    ring = unpack(bank_poly[0], HOME)
    assert ring[0] == ring[-1] and len(ring) >= 5


def test_query_skips_tanks_and_pools():
    q = water.query(*HOME, 30)
    assert 'waterway"~"^(river|stream|canal)$' in q and "wastewater" in q and "out geom" in q


async def test_ws_water_serves_cached_data(hass, hass_ws_client, feeds):
    from .test_vigia import _setup
    started = []
    with patch.object(water.Waterways, "maybe_refresh", lambda self, *a: started.append(a)):
        entry = await _setup(hass)
        entry.runtime_data.water.data = {"o": [41.7675, -8.5831], "r": [], "s": [[1, 2, 3, 4]], "k": [], "a": []}
        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 1, "type": "vigia/water"})
        msg = await ws.receive_json()
    assert msg["success"] and msg["result"]["data"]["s"] == [[1, 2, 3, 4]]
    assert msg["result"]["info"]["streams"] == 1
    assert started  # setup and the request both check freshness
