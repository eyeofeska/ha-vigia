"""Waterways for the map: rivers, creeks, canals and lakes from OpenStreetMap (Overpass), thinned and cached.

Fetched once when Vigia starts or home moves, then every 30 days. Served to the map card on request,
so the 30 km of creeks never rides along with the 5-minute fire updates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, VERSION
from .geo import bbox

_LOGGER = logging.getLogger(__name__)

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
HEADERS = {"User-Agent": f"Vigia/{VERSION.rsplit('.', 1)[0]} (https://github.com/eyeofeska/ha-vigia)"}
REFRESH_S = 30 * 86400
RETRY_S = 6 * 3600
LINE_KINDS = ("river", "stream", "canal")
SKIP_WATER = "basin|wastewater|pool|swimming_pool|reflecting_pool|fountain|moat"
MIN_STEP = 0.00025   # ~25 m between kept points on rivers and lake shores
STREAM_STEP = 0.0006  # ~55 m on creeks: there are thousands of them
MIN_AREA_SPAN = 0.0007  # lakes and ponds smaller than ~60 m across are left out


def query(lat: float, lon: float, radius: float) -> str:
    w, s, e, n = bbox(lat, lon, radius)
    bb = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    return (
        f'[out:json][timeout:90];('
        f'way["waterway"~"^(river|stream|canal)$"]({bb});'
        f'way["natural"="water"]["water"!~"^({SKIP_WATER})$"]({bb});'
        f'relation["natural"="water"]["water"!~"^({SKIP_WATER})$"]({bb});'
        f');out geom qt;'
    )


def _thin(points: list, closed: bool = False, step: float = MIN_STEP) -> list:
    """Round to ~10 m and keep a point only every `step` degrees (~25 m); always keep the ends."""
    if not points:
        return []
    k = math.cos(math.radians(points[0][0]))
    out = [[round(points[0][0], 4), round(points[0][1], 4)]]
    for la, lo in points[1:-1]:
        if abs(la - out[-1][0]) + abs(lo - out[-1][1]) * k >= step:
            out.append([round(la, 4), round(lo, 4)])
    last = [round(points[-1][0], 4), round(points[-1][1], 4)]
    if last != out[-1] or len(out) == 1:
        out.append(last)
    if closed and out[0] != out[-1]:
        out.append(out[0])
    return out


def _span(ring: list) -> float:
    la = [p[0] for p in ring]
    lo = [p[1] for p in ring]
    return max(max(la) - min(la), (max(lo) - min(lo)) * math.cos(math.radians(la[0])))


def _join(parts: list[list]) -> list[list]:
    """Stitch relation member ways end to end into closed rings."""
    parts = [p[:] for p in parts if len(p) >= 2]
    rings = []
    while parts:
        ring = parts.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[::-1][1:]
                elif p[-1] == ring[0]:
                    ring = p[:-1] + ring
                elif p[0] == ring[0]:
                    ring = p[::-1][:-1] + ring
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def pack(line: list, origin: tuple[float, float]) -> list[int]:
    """[[lat, lon], ...] -> flat ints in 1e-4 degree steps, first point from `origin`, the rest as differences.

    About a quarter of the size of plain coordinates; the card unpacks it."""
    out: list[int] = []
    pa, pb = round(origin[0] * 1e4), round(origin[1] * 1e4)
    for la, lo in line:
        a, b = round(la * 1e4), round(lo * 1e4)
        out += [a - pa, b - pb]
        pa, pb = a, b
    return out


def parse(data: dict, origin: tuple[float, float]) -> dict:
    """Overpass JSON -> packed {"o": origin, "r": [{n, c}], "s": [c], "k": [c], "a": [[ring, hole...]]}."""
    raw = _parse(data)
    return {
        "o": [round(origin[0], 4), round(origin[1], 4)],
        "r": [{"n": r["n"], "c": pack(r["c"], origin)} for r in raw["rivers"]],
        "s": [pack(c, origin) for c in raw["streams"]],
        "k": [pack(c, origin) for c in raw["canals"]],
        "a": [[pack(r, origin) for r in poly] for poly in raw["areas"]],
    }


def _parse(data: dict) -> dict:
    out: dict[str, list] = {"rivers": [], "streams": [], "canals": [], "areas": []}
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        ww = tags.get("waterway")
        if el["type"] == "way" and ww in LINE_KINDS:
            pts = [(g["lat"], g["lon"]) for g in el.get("geometry") or []]
            line = _thin(pts, step=MIN_STEP if ww == "river" else STREAM_STEP)
            if len(line) < 2:
                continue
            if ww == "river":
                out["rivers"].append({"n": tags.get("name"), "c": line})
            else:
                out["streams" if ww == "stream" else "canals"].append(line)
        elif el["type"] == "way" and tags.get("natural") == "water":
            pts = [(g["lat"], g["lon"]) for g in el.get("geometry") or []]
            if len(pts) < 4 or pts[0] != pts[-1]:
                continue
            ring = _thin(pts, closed=True)
            if len(ring) >= 4 and _span(ring) >= MIN_AREA_SPAN:
                out["areas"].append([ring])
        elif el["type"] == "relation":
            outer, inner = [], []
            for m in el.get("members") or []:
                if m.get("type") != "way" or not m.get("geometry"):
                    continue
                pts = [[g["lat"], g["lon"]] for g in m["geometry"]]
                (inner if m.get("role") == "inner" else outer).append(pts)
            holes = [_thin(r, closed=True) for r in _join(inner)]
            for r in _join(outer):
                ring = _thin(r, closed=True)
                if len(ring) >= 4 and _span(ring) >= MIN_AREA_SPAN:
                    out["areas"].append([ring, *[h for h in holes if len(h) >= 4]])
    return out


class Waterways:
    """Keeps the thinned waterways for one home, refreshed monthly in the background."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.water")
        self.data: dict | None = None
        self.place: list | None = None
        self.fetched = 0.0
        self.error: str | None = None
        self.pending = False
        self._next_try = 0.0

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.data, self.place, self.fetched = stored.get("data"), stored.get("place"), stored.get("fetched", 0)

    def info(self) -> dict:
        d = self.data or {}
        return {
            "rivers": len(d.get("r") or []), "streams": len(d.get("s") or []),
            "canals": len(d.get("k") or []), "areas": len(d.get("a") or []),
            "fetched": datetime.fromtimestamp(self.fetched, timezone.utc).isoformat() if self.fetched else None,
            "error": self.error, "pending": self.pending,
        }

    def maybe_refresh(self, lat: float, lon: float, radius: float) -> None:
        place = [round(lat, 4), round(lon, 4), radius]
        if place != self.place:
            self.data = None  # another place: don't draw the old one's rivers
        stale = place != self.place or time.time() - self.fetched > REFRESH_S
        if stale and not self.pending and time.time() >= self._next_try:
            self.pending = True
            self.hass.async_create_background_task(self._refresh(place), f"{DOMAIN} waterways")

    async def _refresh(self, place: list) -> None:
        session = async_get_clientsession(self.hass)
        q = query(*place)
        try:
            text = None
            for url in OVERPASS:
                try:
                    async with session.post(url, data={"data": q}, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=150)) as r:
                        if r.status == 200:
                            text = await r.text()
                            break
                        self.error = f"HTTP {r.status} from {url.split('/')[2]}"
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    self.error = f"{type(err).__name__} from {url.split('/')[2]}"
            if text is None:
                raise RuntimeError(self.error or "no response")
            origin = (place[0], place[1])
            data = await self.hass.async_add_executor_job(lambda: parse(json.loads(text), origin))
            self.data, self.place, self.fetched, self.error = data, place, time.time(), None
            await self._store.async_save({"data": data, "place": place, "fetched": self.fetched})
            _LOGGER.debug("Vigia: waterways loaded %s", self.info())
        except Exception as err:  # noqa: BLE001  the map works without rivers
            self.error = str(err) or type(err).__name__
            self._next_try = time.time() + RETRY_S
            _LOGGER.debug("Vigia: waterways failed: %s", self.error)
        finally:
            self.pending = False
