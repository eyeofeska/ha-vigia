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
    WARNING_RANK,
)
from .geo import bbox, distance_km

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"User-Agent": "ha-vigia (+https://github.com/eyeofeska/ha-vigia)"}


class SourceError(Exception):
    """A source could not be read."""


class InvalidKey(SourceError):
    """The FIRMS map key was refused."""


async def _get(session: aiohttp.ClientSession, url: str, **params: Any) -> str:
    try:
        async with session.get(url, params=params or None, timeout=TIMEOUT, headers=HEADERS) as r:
            text = await r.text()
            if r.status != 200:
                raise SourceError(f"HTTP {r.status}")
            return text
    except (aiohttp.ClientError, TimeoutError) as err:
        raise SourceError(type(err).__name__) from err


async def _json(session: aiohttp.ClientSession, url: str, **params: Any) -> Any:
    text = await _get(session, url, **params)
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

async def fogos(session, lat: float, lon: float, radius: float) -> list[dict]:
    """Civil protection incidents within the radius."""
    data = await _json(session, URL_FOGOS)
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

def _round_coords(c: Any) -> Any:
    if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
        return [round(c[0], 4), round(c[1], 4)]
    return [_round_coords(x) for x in c]


async def effis(session, lat: float, lon: float, radius: float) -> list[dict]:
    """This year's burnt areas in the map square, as GeoJSON features."""
    w, s, e, n = bbox(lat, lon, radius)
    data, last = None, SourceError("no response")
    for fmt in ("application/json", "geojson", "GEOJSON"):
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
        out.append({
            "type": "Feature",
            "geometry": {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"])},
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
