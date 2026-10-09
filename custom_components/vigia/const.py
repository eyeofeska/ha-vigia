"""Constants for Vigia."""
from __future__ import annotations

DOMAIN = "vigia"
VERSION = "0.1.9"

EVENT_ALERT = "vigia_alert"
STATIC_URL = "/vigia_static"
CARD_FILE = "vigia-card.js"

# config entry options
CONF_MAP_KEY = "map_key"
CONF_WEATHER = "weather_entity"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS = "radius_km"
CONF_FOGOS = "use_fogos"
CONF_FOGOS_KEY = "fogos_key"
CONF_EFFIS = "use_effis"
CONF_CONCELHO = "ipma_concelho"

DEFAULT_RADIUS = 30

# alert settings, adjustable from the tile (stored by the integration, exposed as number entities)
SETTINGS = {
    # key: (default, min, max, step, unit, name, icon)
    "close_km": (2.0, 0.5, 10.0, 0.5, "km", "Close radius", "mdi:fire-alert"),
    "upwind_km": (5.0, 1.0, 20.0, 0.5, "km", "Upwind radius", "mdi:weather-windy"),
    "watch_km": (15.0, 2.0, 30.0, 1.0, "km", "Watch radius", "mdi:binoculars"),
    "wind_angle": (45.0, 10.0, 90.0, 5.0, "°", "Wind angle", "mdi:angle-acute"),
    "min_wind": (3.0, 0.0, 30.0, 1.0, "km/h", "Upwind minimum wind", "mdi:windsock"),
    "watch_wind": (20.0, 0.0, 60.0, 1.0, "km/h", "Watch minimum wind", "mdi:weather-windy-variant"),
}

TIERS = ["none", "watch", "upwind", "close"]
TIER_RANK = {t: i for i, t in enumerate(TIERS)}

# satellite detections older than this never raise a new alert (they still show on the map)
ALERT_MAX_AGE_H = 12
# detections kept for the map; they fade out by this age
HOTSPOT_MAX_AGE_H = 48
# detections or incidents this close to an earlier alert count as the same fire
SAME_FIRE_KM = 2.0
# a repeat alert for the same fire needs it this much closer (or a higher tier)
CLOSER_BY_KM = 1.0

RISK_NAMES = {1: "low", 2: "moderate", 3: "high", 4: "very high", 5: "maximum"}

# IPMA district (first two digits of the concelho code) -> weather warning area
WARNING_AREAS = {
    "01": "AVR", "02": "BJA", "03": "BRG", "04": "BGC", "05": "CBO", "06": "CBR",
    "07": "EVR", "08": "FAR", "09": "GDA", "10": "LRA", "11": "LSB", "12": "PTG",
    "13": "PTO", "14": "STM", "15": "STB", "16": "VCT", "17": "VRL", "18": "VIS",
}
WARNING_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}

# fogos.pt status codes that count as an active fire
FOGOS_ACTIVE = {3, 4, 5, 6, 7}

FIRMS_SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT"]

# refresh periods in minutes
REFRESH = {
    "ipma_risk": 60,
    "ipma_warnings": 15,
    "fogos": 10,  # 15 without a fogos.pt key
    "firms": 10,
    "effis": 360,
    "wind": 30,
}

URL_IPMA_RISK = "https://api.ipma.pt/open-data/forecast/meteorology/rcm/rcm-d{day}.json"
URL_IPMA_WARNINGS = "https://api.ipma.pt/open-data/forecast/warnings/warnings_www.json"
URL_FOGOS = "https://api.fogos.pt/new/fires"
URL_FIRMS = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/2"
URL_FIRMS_STATUS = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={key}"
# burnt areas: this year plus the two fire seasons before it
BURNT_SEASONS = 3

URL_EFFIS = "https://maps.effis.emergency.copernicus.eu/effis"
URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
URL_FOGOS_FIRE = "https://fogos.pt/fogo/{id}"
URL_FOGOS_MAP = "https://fogos.pt/"
