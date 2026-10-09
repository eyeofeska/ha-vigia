"""Fixtures for Vigia tests."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

HOME = (41.7675, -8.5831)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def feeds():
    """Patch every network source with canned data; tests may edit the dict."""
    now = datetime.now(timezone.utc)
    data = {
        "risk": {"today": {"level": 4, "name": "very high", "date": "2026-10-09"},
                 "tomorrow": {"level": 3, "name": "high", "date": "2026-10-10"}, "dico": "1607"},
        "warnings": [{"type": "Tempo Quente", "level": "yellow", "start": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                      "end": (now + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S"), "text": ""}],
        "fogos": [],
        "firms": [],
        "effis": [],
        "wind": [{"lat": HOME[0], "lon": HOME[1], "speed": 12.0, "dir": 225.0, "gust": 20.0}],
    }

    async def risk(*a, **k):
        return data["risk"]

    async def warnings(*a, **k):
        return data["warnings"]

    async def fogos(*a, **k):
        return data["fogos"]

    async def firms(*a, **k):
        return data["firms"]

    async def effis(*a, **k):
        return data["effis"]

    async def wind(*a, **k):
        return data["wind"]

    async def check(*a, **k):
        if a[1] == "bad":
            from custom_components.vigia.sources import InvalidKey
            raise InvalidKey("nope")

    p = "custom_components.vigia.sources."
    with patch(p + "ipma_risk", risk), patch(p + "ipma_warnings", warnings), patch(p + "fogos", fogos), \
            patch(p + "firms", firms), patch(p + "effis", effis), patch(p + "wind_grid", wind), patch(p + "firms_check", check):
        yield data


@pytest.fixture(autouse=True)
async def fake_frontend(hass):
    """The real frontend needs the hass_frontend package; Vigia only needs it to add its card URL."""
    from homeassistant.setup import async_setup_component

    hass.config.components.add("frontend")
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, "lovelace", {})
    with patch("custom_components.vigia.add_extra_js_url") as js:
        yield js
