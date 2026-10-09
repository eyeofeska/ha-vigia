"""Fetchers for the public data sources. Each returns plain, JSON-ready data or raises."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    FIRMS_SOURCES,
    FOGOS_ACTIVE,
    RISK_NAMES,
    URL_EFFIS,
    URL_FIRMS,
    URL_FIRMS_STATUS,
    URL_FOGOS,
    URL_FOGOS_FIRE,
    URL_IPMA_RISK,
    URL_IPMA_WARNINGS,
    URL_OPEN_METEO,
    VERSION,
    WARNING_RANK,
)
from .geo import bbox, distance_km

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"User-Agent": f"Vigia/{VERSION.rsplit('.', 1)[0]} (https://github.com/eyeofeska/ha-vigia)"}


class SourceError(Exception):
    """A source could not be read."""


class InvalidKey(SourceError):
    """An API key was refused."""


class RateLimited(SourceError):
    """The source asked us to slow down (HTTP 429)."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("rate limited (HTTP 429)")
        self.retry_after = retry_after


async def _get(session: aiohttp.ClientSession, url: str, headers: dict | None = None, **params: Any) -> str:
    try:
        async with session.get(url, params=params or None, timeout=TIMEOUT, headers={**HEADERS, **(headers or {})}) as r:
            text = await r.text()
            if r.status == 429:
                try:
                    retry = float(r.headers.get("Retry-After", ""))
                except ValueError:
                    retry = None
                raise RateLimited(retry)
            if r.status in (401, 403) and headers:
                raise InvalidKey(f"HTTP {r.status}")
            if r.status != 200:
                raise SourceError(f"HTTP {r.status}")
            return text
    except (aiohttp.ClientError, TimeoutError) as err:
        raise SourceError(type(err).__name__) from err


async def _json(session: aiohttp.ClientSession, url: str, headers: dict | None = None, **params: Any) -> Any:
    text = await _get(session, url, headers, **params)
    try:
        return json.loads(text)
    except ValueError as err:
        raise SourceError("not JSON") from err


# ---------- IPMA ----------

async def ipma_risk(session, lat: float, lon: float, dico: str | None) -> dict:
    """Fire risk (RCM) for today and tomorrow at the concelho nearest home."""
    out: dict[str, Any] = {}
    for day in (0, 1):
        data = await _json(session, URL_IPMA_RISK.format(day=day))
        local = data.get("local") or {}
        if not local:
            raise SourceError("no concelhos in IPMA risk file")
        if not dico or dico not in local:
            dico = min(local, key=lambda k: distance_km(lat, lon, local[k]["latitude"], local[k]["longitude"]))
        level = int((local[dico].get("data") or {}).get("rcm") or 0) or None
        out["today" if day == 0 else "tomorrow"] = {
            "level": level, "name": RISK_NAMES.get(level), "date": data.get("dataPrev"),
        }
    out["dico"] = dico
    return out


async def ipma_warnings(session, area: str) -> list[dict]:
    """Weather warnings above green for one warning area, ending in the future, soonest first."""
    data = await _json(session, URL_IPMA_WARNINGS)
    now = datetime.now(timezone.utc)
    out = []
    for w in data if isinstance(data, list) else []:
        if w.get("idAreaAviso") != area or w.get("awarenessLevelID") not in ("yellow", "orange", "red"):
            continue
        try:
            # IPMA gives local (Lisbon) times without a zone; close enough to compare to UTC within an hour
            end = datetime.fromisoformat(w["endTime"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if end < now:
            continue
        out.append({
            "type": w.get("awarenessTypeName"), "level": w.get("awarenessLevelID"),
            "start": w.get("startTime"), "end": w.get("endTime"), "text": (w.get("text") or "").strip(),
        })
    out.sort(key=lambda w: (-WARNING_RANK[w["level"]], w["start"] or ""))
    return out


# ---------- fogos.pt (ANEPC incidents) ----------

async def fogos(session, lat: float, lon: float, radius: float, key: str | None = None) -> list[dict]:
    """Civil protection incidents within the radius. fogos.pt asks for an individual key (X-API-Key)."""
    data = await _json(session, URL_FOGOS, {"X-API-Key": key} if key else None)
    items = data.get("data") if isinstance(data, dict) else data
    out = []
    for f in items or []:
        try:
            flat, flon = float(f["lat"]), float(f["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if distance_km(lat, lon, flat, flon) > radius * 1.42:  # corners of the map square
            continue
        code = int(f.get("statusCode") or 0)
        when = (f.get("dateTime") or {}).get("sec") if isinstance(f.get("dateTime"), dict) else None
        upd = (f.get("updated") or {}).get("sec") if isinstance(f.get("updated"), dict) else None
        out.append({
            "id": str(f.get("id")), "lat": flat, "lon": flon,
            "status": f.get("status"), "status_code": code, "active": code in FOGOS_ACTIVE,
            "place": ", ".join(x for x in (f.get("localidade"), f.get("freguesia"), f.get("concelho")) if x),
            "concelho": f.get("concelho"), "kind": f.get("natureza"),
            "people": f.get("man"), "vehicles": f.get("terrain"), "aircraft": f.get("aerial"),
            "time": datetime.fromtimestamp(when, timezone.utc).isoformat() if when else None,
            "updated": datetime.fromtimestamp(upd, timezone.utc).isoformat() if upd else None,
            "url": URL_FOGOS_FIRE.format(id=f.get("id")),
        })
    return out


# ---------- NASA FIRMS ----------

async def firms_check(session, key: str) -> None:
    """Raise InvalidKey if FIRMS refuses the key, SourceError if it can't be reached."""
    text = await _get(session, URL_FIRMS_STATUS.format(key=key))
    try:
        data = json.loads(text)
    except ValueError as err:
        raise InvalidKey(text[:80]) from err
    if not isinstance(data, dict) or "transaction_limit" not in data:
        raise InvalidKey(str(data)[:80])


async def firms(session, key: str, lat: float, lon: float, radius: float) -> list[dict]:
    """VIIRS hotspots in the map square over the last 2 days, from all three satellites."""
    w, s, e, n = bbox(lat, lon, radius)
    box = f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"
    out, errors = [], []
    for src in FIRMS_SOURCES:
        try:
            text = await _get(session, URL_FIRMS.format(key=key, source=src, bbox=box))
        except SourceError as err:
            errors.append(f"{src}: {err}")
            continue
        if "invalid" in text[:200].lower() and "map_key" in text[:200].lower():
            raise InvalidKey("FIRMS refused the map key")
        for row in csv.DictReader(io.StringIO(text)):
            try:
                hlat, hlon = float(row["latitude"]), float(row["longitude"])
                t = str(row.get("acq_time", "0")).zfill(4)
                when = datetime.strptime(f"{row['acq_date']} {t}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if row.get("confidence", "").lower() in ("l", "low"):
                continue
            out.append({
                "id": f"{src[6:-4]}-{when:%Y%m%d%H%M}-{hlat:.4f}-{hlon:.4f}",
                "lat": hlat, "lon": hlon, "time": when.isoformat(),
                "sat": {"VIIRS_NOAA20_NRT": "NOAA-20", "VIIRS_NOAA21_NRT": "NOAA-21", "VIIRS_SNPP_NRT": "S-NPP"}[src],
                "frp": _num(row.get("frp")), "confidence": row.get("confidence"), "daynight": row.get("daynight"),
            })
    if errors and len(errors) == len(FIRMS_SOURCES):
        raise SourceError("; ".join(errors))
    return out


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


# ---------- EFFIS burnt areas ----------

def _thin_ring(ring: list, swap: bool = False) -> list:
    """Round to ~10 m and drop points within ~30 m of the last kept one; burnt areas only need their outline."""
    out = []
    for a, b, *_ in ring:
        x, y = (b, a) if swap else (a, b)
        p = [round(x, 4), round(y, 4)]
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= 0.0004:
            out.append(p)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else []


def _first_point(c: Any) -> list:
    while isinstance(c, list) and c and isinstance(c[0], list):
        c = c[0]
    return c


def _thin(geom: dict, swap: bool = False) -> dict | None:
    if geom["type"] == "Polygon":
        rings = [r for r in (_thin_ring(r, swap) for r in geom["coordinates"]) if r]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    polys = [[r for r in (_thin_ring(r, swap) for r in poly) if r] for poly in geom["coordinates"]]
    polys = [p for p in polys if p]
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


async def effis(session, lat: float, lon: float, radius: float) -> list[dict]:
    """This year's burnt areas in the map square, as GeoJSON features."""
    w, s, e, n = bbox(lat, lon, radius)
    data, last = None, SourceError("no response")
    for fmt in ("geojson", "application/json"):
        try:
            data = await _json(
                session, URL_EFFIS, service="WFS", request="GetFeature", version="1.0.0",
                typename="ms:modis.ba.poly", srsname="EPSG:4326",
                bbox=f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", outputformat=fmt,
            )
            break
        except SourceError as err:
            last = err
    if data is None:
        raise last
    year = str(datetime.now().year)
    out = []
    for f in data.get("features") or []:
        props = {k.lower(): v for k, v in (f.get("properties") or {}).items()}
        date = str(props.get("firedate") or props.get("lastupdate") or "")
        if not date.startswith(year) and year not in date[:10]:
            continue
        geom = f.get("geometry")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        # EFFIS answers EPSG:4326 in latitude, longitude order; GeoJSON wants longitude first
        p = _first_point(geom["coordinates"])
        swap = len(p) >= 2 and abs(p[0] - lat) + abs(p[1] - lon) < abs(p[1] - lat) + abs(p[0] - lon)
        thin = _thin(geom, swap)
        if not thin:
            continue
        out.append({
            "type": "Feature",
            "geometry": thin,
            "properties": {"date": date[:10], "area_ha": _num(props.get("area_ha")), "place": props.get("commune") or props.get("province")},
        })
    return out


# ---------- wind ----------

async def wind_grid(session, lat: float, lon: float, radius: float, n: int = 5) -> list[dict]:
    """Current 10 m wind on an n x n grid across the map square (Open-Meteo)."""
    w, s, e, nn = bbox(lat, lon, radius)
    lats, lons = [], []
    for i in range(n):
        for j in range(n):
            lats.append(round(s + (nn - s) * (i + 0.5) / n, 3))
            lons.append(round(w + (e - w) * (j + 0.5) / n, 3))
    data = await _json(
        session, URL_OPEN_METEO, latitude=",".join(map(str, lats)), longitude=",".join(map(str, lons)),
        current="wind_speed_10m,wind_direction_10m,wind_gusts_10m", wind_speed_unit="kmh",
    )
    rows = data if isinstance(data, list) else [data]
    out = []
    for la, lo, r in zip(lats, lons, rows):
        c = r.get("current") or {}
        if c.get("wind_speed_10m") is None:
            continue
        out.append({"lat": la, "lon": lo, "speed": c["wind_speed_10m"], "dir": c.get("wind_direction_10m"), "gust": c.get("wind_gusts_10m")})
    if not out:
        raise SourceError("no wind data")
    return out
