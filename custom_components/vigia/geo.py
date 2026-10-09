"""Small geometry helpers."""
from __future__ import annotations

import math

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def ang_diff(a: float, b: float) -> float:
    """Smallest difference between two bearings."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def compass(bearing: float) -> str:
    """Eight-point compass name."""
    return COMPASS[round(bearing / 45) % 8]


def offset(lat: float, lon: float, km: float, bearing: float) -> tuple[float, float]:
    """Point km away from lat/lon along bearing (flat-earth approximation, fine for tens of km)."""
    b = math.radians(bearing)
    dlat = km * math.cos(b) / 111.32
    dlon = km * math.sin(b) / (111.32 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def bbox(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    """West, south, east, north of a square km from the centre."""
    dlat = km / 111.32
    dlon = km / (111.32 * math.cos(math.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat
