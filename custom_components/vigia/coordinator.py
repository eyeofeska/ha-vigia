"""Vigia coordinator: fetches every source on its own schedule, keeps the last good data, and raises alerts."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import sources
from .const import (
    ALERT_MAX_AGE_H,
    CLOSER_BY_KM,
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
    EVENT_ALERT,
    HOTSPOT_MAX_AGE_H,
    REFRESH,
    SAME_FIRE_KM,
    SETTINGS,
    TIER_RANK,
    URL_FOGOS_MAP,
    WARNING_AREAS,
    WARNING_RANK,
)
from .geo import ang_diff, bearing_deg, compass, distance_km, offset

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
TIER_TITLE = {"close": "Fire close", "upwind": "Fire upwind", "watch": "Fire watch"}
SPEED_TO_KMH = {"km/h": 1.0, "m/s": 3.6, "mph": 1.609344, "kn": 1.852, "ft/s": 1.09728}


def _age_h(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() / 3600
    except ValueError:
        return None


class VigiaCoordinator(DataUpdateCoordinator[dict]):
    """One coordinator for the whole integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.entry = entry
        self.session = async_get_clientsession(hass)
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.cache: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, float] = {k: v[0] for k, v in SETTINGS.items()}
        self.alerted: list[dict] = []
        self.test_mode = False
        self._fake: dict[str, list] = {"hotspots": [], "incidents": []}
        self._unsub_weather = None
        self._recompute_pending = None

    # ---------- config ----------
    @property
    def opts(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def home(self) -> tuple[float, float]:
        o = self.opts
        if o.get(CONF_LATITUDE) not in (None, "") and o.get(CONF_LONGITUDE) not in (None, ""):
            return float(o[CONF_LATITUDE]), float(o[CONF_LONGITUDE])
        return self.hass.config.latitude, self.hass.config.longitude

    @property
    def radius(self) -> float:
        return float(self.opts.get(CONF_RADIUS) or DEFAULT_RADIUS)

    def _enabled(self, name: str) -> bool:
        o = self.opts
        if name == "firms":
            return bool(o.get(CONF_MAP_KEY))
        if name == "fogos":
            return o.get(CONF_FOGOS, True)
        if name == "effis":
            return o.get(CONF_EFFIS, True)
        return True

    # ---------- storage ----------
    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.cache = stored.get("cache") or {}
        for k, v in (stored.get("settings") or {}).items():
            if k in self.settings:
                self.settings[k] = float(v)
        self.alerted = [a for a in stored.get("alerted") or [] if not a.get("test")]
        # a changed home or radius makes cached positions meaningless for the map square
        if stored.get("home") != [*self.home, self.radius]:
            for name in ("fogos", "firms", "effis", "wind"):
                self.cache.pop(name, None)
        now = time.time()
        for c in self.cache.values():
            if c.get("backoff_until", 0) <= now:
                c["fetched"] = 0  # refetch everything on start, except sources that asked us to wait

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {
            "cache": self.cache, "settings": self.settings,
            "alerted": [a for a in self.alerted if not a.get("test")], "home": [*self.home, self.radius],
        }, 10)

    # ---------- wind at home ----------
    def start_listeners(self) -> None:
        ent = self.opts.get(CONF_WEATHER)
        if ent:
            self._unsub_weather = async_track_state_change_event(self.hass, [ent], self._weather_changed)

    def stop_listeners(self) -> None:
        if self._unsub_weather:
            self._unsub_weather()
            self._unsub_weather = None
        if self._recompute_pending:
            self._recompute_pending()
            self._recompute_pending = None

    @callback
    def _weather_changed(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old and new and old.attributes.get("wind_bearing") == new.attributes.get("wind_bearing") \
                and old.attributes.get("wind_speed") == new.attributes.get("wind_speed"):
            return
        self.schedule_recompute()

    @callback
    def schedule_recompute(self, delay: float = 2) -> None:
        if self._recompute_pending:
            return

        @callback
        def _run(_now) -> None:
            self._recompute_pending = None
            if self.data is not None:
                self.async_set_updated_data(self._compute(evaluate=True))

        self._recompute_pending = async_call_later(self.hass, delay, _run)

    def wind(self) -> dict:
        """Wind at home: speed km/h and the bearing it blows FROM."""
        ent = self.opts.get(CONF_WEATHER)
        st = self.hass.states.get(ent) if ent else None
        if st:
            spd, brg = st.attributes.get("wind_speed"), st.attributes.get("wind_bearing")
            unit = st.attributes.get("wind_speed_unit") or "km/h"
            try:
                spd = float(spd) * SPEED_TO_KMH.get(unit, 1.0)
            except (TypeError, ValueError):
                spd = None
            if isinstance(brg, str):
                names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
                brg = names.index(brg.upper()) * 22.5 if brg.upper() in names else None
            if spd is not None:
                return {"speed": round(spd, 1), "from": float(brg) if brg is not None else None, "source": ent}
        grid = (self.cache.get("wind") or {}).get("data") or []
        if grid:
            lat, lon = self.home
            p = min(grid, key=lambda g: distance_km(lat, lon, g["lat"], g["lon"]))
            return {"speed": p["speed"], "from": p["dir"], "source": "open-meteo"}
        return {"speed": None, "from": None, "source": None}

    # ---------- fetching ----------
    async def _fetch(self, name: str) -> None:
        lat, lon = self.home
        c = self.cache.setdefault(name, {"data": None, "ok": False, "updated": None, "error": None, "fetched": 0})
        c["fetched"] = time.time()
        try:
            if name == "ipma_risk":
                data = await sources.ipma_risk(self.session, lat, lon, (c.get("data") or {}).get("dico"))
            elif name == "ipma_warnings":
                dico = ((self.cache.get("ipma_risk") or {}).get("data") or {}).get("dico")
                area = WARNING_AREAS.get((dico or "")[:2])
                if not area:
                    raise sources.SourceError("waiting for IPMA concelho" if not dico else "no warning area here")
                data = await sources.ipma_warnings(self.session, area)
            elif name == "fogos":
                data = await sources.fogos(self.session, lat, lon, self.radius, self.opts.get(CONF_FOGOS_KEY) or None)
            elif name == "firms":
                data = await sources.firms(self.session, self.opts[CONF_MAP_KEY], lat, lon, self.radius)
            elif name == "effis":
                data = await sources.effis(self.session, lat, lon, self.radius)
            elif name == "wind":
                data = await sources.wind_grid(self.session, lat, lon, self.radius)
            else:
                return
        except sources.RateLimited as err:
            # back off: honour Retry-After, otherwise double the wait each time, up to an hour
            c["strikes"] = min(c.get("strikes", 0) + 1, 6)
            wait = err.retry_after or REFRESH[name] * 60 * 2 ** c["strikes"]
            c["backoff_until"] = time.time() + min(max(wait, 60), 3600)
            c["ok"], c["error"] = False, str(err)
            _LOGGER.debug("Vigia: %s rate limited, waiting %.0f s", name, c["backoff_until"] - time.time())
            return
        except Exception as err:  # noqa: BLE001  one bad source must never stop the others
            c["ok"], c["error"] = False, str(err) or type(err).__name__
            _LOGGER.debug("Vigia: %s failed: %s", name, c["error"])
            return
        c.update(data=data, ok=True, error=None, updated=datetime.now(timezone.utc).isoformat(), strikes=0, backoff_until=0)

    def _due(self, name: str) -> bool:
        if not self._enabled(name):
            return False
        c = self.cache.get(name)
        if not c:
            return True
        if time.time() < c.get("backoff_until", 0):
            return False
        period = REFRESH[name] * 60
        if name == "fogos" and not self.opts.get(CONF_FOGOS_KEY):
            period = 15 * 60  # be gentle without a key
        elif name == "fogos":
            period = 10 * 60  # as agreed in the key request
        if not c.get("ok"):
            period = min(period, 5 * 60)  # retry failed sources every 5 min
        return time.time() - c.get("fetched", 0) >= period - 30

    async def _async_update_data(self) -> dict:
        if self._due("ipma_risk"):
            await self._fetch("ipma_risk")
        due = [n for n in REFRESH if n != "ipma_risk" and self._due(n)]
        if due:
            await asyncio.gather(*(self._fetch(n) for n in due))
        for name in REFRESH:
            if not self._enabled(name) and name in self.cache:
                self.cache.pop(name)
        self._save()
        return self._compute(evaluate=True)

    # ---------- settings and test mode ----------
    @callback
    def set_setting(self, key: str, value: float) -> None:
        self.settings[key] = float(value)
        self._save()
        if self.data is not None:
            self.async_set_updated_data(self._compute(evaluate=True))

    @callback
    def set_test_mode(self, on: bool) -> None:
        self.test_mode = on
        self._fake = self._make_fake() if on else {"hotspots": [], "incidents": []}
        if not on:
            self.alerted = [a for a in self.alerted if not a.get("test")]
        if self.data is not None:
            self.async_set_updated_data(self._compute(evaluate=True))

    def _make_fake(self) -> dict:
        """A pretend fire just upwind (or close, if there's no wind), and an older one further off."""
        lat, lon = self.home
        w = self.wind()
        now = datetime.now(timezone.utc)
        if w["from"] is not None and (w["speed"] or 0) >= self.settings["min_wind"]:
            brg, dist = w["from"], min(3.5, self.settings["upwind_km"] * 0.7)
        else:
            brg, dist = 20.0, self.settings["close_km"] * 0.75
        flat, flon = offset(lat, lon, dist, brg)
        hot = []
        for i, (dx, dy) in enumerate([(0, 0), (0.35, 0.1), (-0.3, 0.2), (0.1, -0.35), (0.45, -0.25), (-0.15, -0.5)]):
            plat, plon = offset(flat, flon, math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)))
            hot.append({"id": f"test-new-{i}", "lat": plat, "lon": plon, "sat": "NOAA-21", "frp": 12.0 + i,
                        "time": (now - timedelta(minutes=50 + 20 * i)).isoformat(), "test": True})
        olat, olon = offset(lat, lon, 9, (brg + 140) % 360)
        for i, (dx, dy) in enumerate([(0, 0), (0.4, 0.15), (0.2, 0.5), (-0.3, 0.3)]):
            plat, plon = offset(olat, olon, math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)))
            hot.append({"id": f"test-old-{i}", "lat": plat, "lon": plon, "sat": "S-NPP", "frp": 5.0,
                        "time": (now - timedelta(hours=20 + 6 * i)).isoformat(), "test": True})
        inc = [{"id": "test", "lat": flat, "lon": flon, "status": "Em Curso", "status_code": 5, "active": True,
                "place": "Test fire (not real)", "concelho": "Test", "kind": "Mato", "people": 24, "vehicles": 7,
                "aircraft": 1, "time": (now - timedelta(minutes=40)).isoformat(), "updated": now.isoformat(),
                "url": URL_FOGOS_MAP, "test": True}]
        return {"hotspots": hot, "incidents": inc}

    # ---------- the picture ----------
    def _tier(self, dist: float, toward: bool, speed: float | None) -> str:
        s = self.settings
        if dist <= s["close_km"]:
            return "close"
        if toward and (speed or 0) >= s["min_wind"] and dist <= s["upwind_km"]:
            return "upwind"
        if toward and (speed or 0) >= s["watch_wind"] and dist <= s["watch_km"]:
            return "watch"
        return "none"

    def _compute(self, evaluate: bool) -> dict:
        now = datetime.now(timezone.utc)
        lat, lon = self.home
        wind = self.wind()
        wfrom, wspd = wind["from"], wind["speed"]

        def place(item: dict, can_alert: bool) -> dict:
            d = distance_km(lat, lon, item["lat"], item["lon"])
            b = bearing_deg(lat, lon, item["lat"], item["lon"])
            toward = wfrom is not None and ang_diff(wfrom, b) <= self.settings["wind_angle"]
            return {**item, "distance": round(d, 2), "bearing": round(b), "dir": compass(b), "upwind": toward,
                    "tier": self._tier(d, toward, wspd) if can_alert else "none"}

        hotspots = []
        for h in ((self.cache.get("firms") or {}).get("data") or []) + self._fake["hotspots"]:
            age = _age_h(h.get("time"), now)
            if age is None or age > HOTSPOT_MAX_AGE_H:
                continue
            p = place(h, age <= ALERT_MAX_AGE_H)
            p["age_h"] = round(age, 2)
            if p["distance"] <= self.radius * 1.42:
                hotspots.append(p)
        incidents = [place(i, i.get("active")) for i in ((self.cache.get("fogos") or {}).get("data") or []) + self._fake["incidents"]]

        threats = [{"kind": "incident", **i} for i in incidents if i["tier"] != "none"] + \
                  [{"kind": "satellite", **h} for h in hotspots if h["tier"] != "none"]
        threats.sort(key=lambda t: (-TIER_RANK[t["tier"]], t["distance"]))
        nearby = [i for i in incidents if i.get("active")] + [h for h in hotspots if h["age_h"] <= 24]
        nearest = min(nearby, key=lambda x: x["distance"]) if nearby else None

        if evaluate:
            self._evaluate(threats, wind, now)

        risk = (self.cache.get("ipma_risk") or {}).get("data") or {}
        warnings = (self.cache.get("ipma_warnings") or {}).get("data") or []
        top = threats[0] if threats else None
        return {
            "home": {"lat": lat, "lon": lon}, "radius_km": self.radius,
            "settings": dict(self.settings), "test_mode": self.test_mode,
            "wind": wind,
            "risk": {"today": risk.get("today"), "tomorrow": risk.get("tomorrow"), "dico": risk.get("dico")},
            "warnings": warnings,
            "warning_level": max((w["level"] for w in warnings if self._warning_now(w, now)), key=WARNING_RANK.get, default="green"),
            "hotspots": sorted(hotspots, key=lambda h: h["distance"]),
            "incidents": sorted(incidents, key=lambda i: i["distance"]),
            "burnt": (self.cache.get("effis") or {}).get("data") or [],
            "wind_grid": (self.cache.get("wind") or {}).get("data") or [],
            "alert": {
                "tier": top["tier"] if top else "none",
                "threat": self._describe(top, wind) if top else None,
            },
            "nearest": {
                "distance": nearest["distance"], "dir": nearest["dir"], "upwind": nearest["upwind"],
                "kind": "incident" if "status" in nearest else "satellite",
            } if nearest else None,
            "counts": {
                "incidents": sum(1 for i in incidents if i.get("active") and i["distance"] <= self.radius),
                "hotspots": sum(1 for h in hotspots if h["age_h"] <= 24 and h["distance"] <= self.radius),
            },
            "sources": {
                n: {"enabled": self._enabled(n), "ok": c.get("ok", False), "updated": c.get("updated"), "error": c.get("error")}
                for n in REFRESH for c in [self.cache.get(n) or {}]
            },
            "computed": now.isoformat(),
        }

    @staticmethod
    def _warning_now(w: dict, now: datetime) -> bool:
        try:
            start = datetime.fromisoformat(w["start"]).replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            return True
        return start <= now + timedelta(hours=1)

    def _describe(self, t: dict, wind: dict) -> dict:
        d = t["distance"]
        dist = f"{d:.1f}" if d < 10 else f"{round(d)}"
        where = f"{dist} km {t['dir']}"
        test = bool(t.get("test"))
        if t["upwind"] and wind["speed"]:
            wtxt = f"Wind {round(wind['speed'])} km/h blowing from it toward the land."
        else:
            wtxt = "Wind not blowing toward the land for now."
        if t["kind"] == "incident":
            body = f"{t.get('status') or 'Fire'} near {t.get('concelho') or t.get('place') or 'here'}, {where}. {wtxt}"
            url = t.get("url") or URL_FOGOS_MAP
        else:
            age = t.get("age_h") or 0
            ago = f"{round(age * 60)} min" if age < 1 else f"{age:.0f} h"
            body = f"Satellite heat detected {where}, {ago} ago. {wtxt} Check fogos.pt."
            url = URL_FOGOS_MAP
        title = f"{TIER_TITLE[t['tier']]}: {where}"
        if test:
            title, body = "TEST " + title, body + " This is a test."
        return {
            "tier": t["tier"], "kind": t["kind"], "title": title, "message": body, "url": url,
            "distance_km": t["distance"], "direction": t["dir"], "bearing": t["bearing"], "upwind": t["upwind"],
            "wind_speed": wind["speed"], "wind_from": wind["from"], "lat": t["lat"], "lon": t["lon"],
            "key": ("fogos:" if t["kind"] == "incident" else "firms:") + str(t["id"]), "test": test,
        }

    def _evaluate(self, threats: list[dict], wind: dict, now: datetime) -> None:
        """Fire one event per fire; repeat only when it is meaningfully closer or a higher tier."""
        cutoff = (now - timedelta(hours=72)).isoformat()
        self.alerted = [a for a in self.alerted if a.get("time", "") >= cutoff]
        changed = False
        for t in threats:
            info = self._describe(t, wind)
            rec = next((a for a in self.alerted if a["key"] == info["key"] or any(
                distance_km(p[0], p[1], t["lat"], t["lon"]) <= SAME_FIRE_KM for p in a.get("points") or [[a["lat"], a["lon"]]])), None)
            if rec:
                # remember where this fire has spread, so later detections along its edge count as the same fire
                pts = rec.setdefault("points", [[rec["lat"], rec["lon"]]])
                if all(distance_km(p[0], p[1], t["lat"], t["lon"]) > 0.3 for p in pts):
                    pts.append([round(t["lat"], 5), round(t["lon"], 5)])
                    del pts[:-60]
                    changed = True
                higher = TIER_RANK[t["tier"]] > TIER_RANK[rec["tier"]]
                closer = t["distance"] < rec["distance"] - CLOSER_BY_KM
                if not (higher or closer):
                    continue
                rec.update(tier=t["tier"] if higher else rec["tier"], distance=min(rec["distance"], t["distance"]), time=now.isoformat())
                info["repeat"] = True
            else:
                self.alerted.append({"key": info["key"], "lat": t["lat"], "lon": t["lon"], "tier": t["tier"],
                                     "points": [[round(t["lat"], 5), round(t["lon"], 5)]],
                                     "distance": t["distance"], "time": now.isoformat(), "test": info["test"]})
                info["repeat"] = False
            changed = True
            _LOGGER.info("Vigia alert: %s", info["title"])
            self.hass.bus.async_fire(EVENT_ALERT, info)
        if changed:
            self._save()

    @callback
    def fire_test_alert(self) -> None:
        """A sample alert for checking phones and the signal light; no fire involved."""
        lat, lon = self.home
        flat, flon = offset(lat, lon, 3.2, 225)
        self.hass.bus.async_fire(EVENT_ALERT, {
            "tier": "upwind", "kind": "incident", "title": "TEST Fire upwind: 3.2 km SW",
            "message": "This is a Vigia test alert. No fire has been detected.", "url": URL_FOGOS_MAP,
            "distance_km": 3.2, "direction": "SW", "bearing": 225, "upwind": True, "wind_speed": 15, "wind_from": 225,
            "lat": flat, "lon": flon, "key": "test:button", "test": True, "repeat": False,
        })
