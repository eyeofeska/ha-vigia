# Vigia

*Vigia* is Portuguese for a fire lookout. This Home Assistant integration watches for wildfires around your home, shows them on a clean terrain map, and alerts your phones when a fire is close or the wind is blowing it toward you.

It was built for an off-grid smallholding in the Minho, northern Portugal, and is shared for anyone else living with fire season. Fire risk, warnings and incidents are Portugal-only today; satellite hotspots, burnt areas and wind work anywhere.

## What you get

**A fire tile** (`custom:vigia-card`): today's and tomorrow's fire risk on a five-step meter, any weather warning, a count of fires nearby, plain advice at high risk, and an alert line when something is close. A settings panel adjusts the alert distances and wind limits, runs a pretend fire, and sends a test alert. Tap the tile to open the map.

**A fire map** (in the tile, or on its own as `custom:vigia-map-card`): 30 km around home with alert rings, satellite heat detections merged into smooth shapes that are bright when new and fade out by 48 hours, civil protection incidents with their status, this year's burnt areas, wind arrows across the area, and the upwind sector shaded.

**Alerts** that follow three tiers, all adjustable:

| Tier | Rule (defaults) | Sent as |
|---|---|---|
| Close | any fire within 2 km, any wind | urgent: breaks Do Not Disturb, flashes a light |
| Upwind | within 5 km, wind blowing from the fire toward home (within 45°, at least 3 km/h) | urgent: breaks Do Not Disturb, flashes a light |
| Watch | within 15 km upwind, wind 20 km/h or more | normal notification |

Each fire alerts once. It alerts again only if it moves up a tier or comes more than 1 km closer. Satellite detections older than 12 hours never raise a new alert. Satellite alerts say "heat detected" and point you to fogos.pt to check, because a hotspot can also be a sunlit roof or a farmer's burn.

## Data sources

| Source | Gives | Refresh |
|---|---|---|
| [IPMA](https://www.ipma.pt) | Fire risk today and tomorrow for your concelho, weather warnings for your district | 60 min, 15 min |
| [fogos.pt](https://fogos.pt) | Active incidents reported by civil protection (ANEPC), often faster than satellites | 5 min with a key, 15 min without |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov) | VIIRS satellite hotspots (NOAA-20, NOAA-21, S-NPP), up to about 3 h behind | 10 min |
| [EFFIS](https://forest-fire.emergency.copernicus.eu) | This season's burnt areas, © Copernicus | 6 h |
| [Open-Meteo](https://open-meteo.com) | Wind across the map | 30 min |
| Your weather entity | Wind at home, for the alert rules | live |

Everything is fetched by Home Assistant and kept, so the tile, map and alerts carry on from the last data if the internet drops.

## Install

1. In HACS, open the menu › **Custom repositories**, add `https://github.com/eyeofeska/ha-vigia` as an **Integration**, then download **Vigia**.
2. Restart Home Assistant.
3. **Settings › Devices & services › Add integration › Vigia.**
   - **fogos.pt API key** (optional, free): fogos.pt asks each user to [request their own key](https://fogos.pt/en/api). Without one Vigia polls every 15 minutes and backs off when asked; shared connections (Starlink, mobile data) may still be rate limited.
   - **NASA FIRMS map key** (optional, free): request one at [firms.modaps.eosdis.nasa.gov/api/map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key). Without it you get IPMA and fogos.pt only. The key stays in Home Assistant.
   - **Weather entity**: the one used for wind at home (e.g. `weather.forecast_home`).
   - Home comes from Home Assistant unless you set a latitude and longitude.
   - **IPMA concelho code** (optional): fire risk is read for the concelho whose centre is nearest home. Near a border that can be the neighbour, so set your concelho's 4-digit DICO code (e.g. 1601 Arcos de Valdevez, 1606 Ponte da Barca, 1607 Ponte de Lima).
4. Add the tile to a dashboard: `type: custom:vigia-card`. Vigia adds its card as a dashboard resource and moves it to the new version on each update, so phones and tablets pick up changes on their next load. (With dashboards kept in YAML it loads the card with every page instead; hard-refresh after updates.)
5. Set up alerts: **Settings › Automations › Blueprints › Vigia fire alert › Create automation.** Choose the phones and a light to flash. The blueprint is copied into your config the first time Vigia starts.

### Card options

```yaml
type: custom:vigia-card
map: true          # tap opens the fire map (default)
navigate: "#fire"  # or tap goes to this path instead, e.g. a Bubble Card pop-up
```

```yaml
type: custom:vigia-map-card
height: 420
```

## Entities

| Entity | |
|---|---|
| `sensor.vigia_fire_risk`, `sensor.vigia_fire_risk_tomorrow` | IPMA class 1 (low) to 5 (maximum); the word is in the `risk` attribute |
| `sensor.vigia_weather_warning` | green, yellow, orange or red, with the warnings as an attribute |
| `sensor.vigia_alert_level` | none, watch, upwind or close, with the alert text as attributes |
| `sensor.vigia_nearest_fire` | km to the nearest active incident or 24 h hotspot |
| `sensor.vigia_fires_nearby` | active incidents plus 24 h hotspots within the map radius |
| `number.vigia_*` | the alert distances, wind angle and wind limits |
| `switch.vigia_test_mode` | a pretend fire upwind of home, for trying the tile, map and alerts |
| `button.vigia_send_test_alert` | sends a test alert through your automations |

Alerts are fired as the `vigia_alert` event, with `tier`, `title`, `message`, `url`, `distance_km`, `direction`, `upwind`, `wind_speed`, `kind` (`incident` or `satellite`), `repeat` and `test`. Use it in your own automations if the blueprint doesn't fit.

## Notes

- Urgent alerts use the `alarm_stream` channel on Android and critical alerts on iOS. On iOS, allow critical alerts for the Home Assistant app the first time.
- fogos.pt is a volunteer project with its own [terms](https://fogos.pt/en/api-termos): use your own key, keep the polling gentle, show "Source: Fogos.pt" with its data, and never rely on it alone for alerts. Vigia identifies itself, honours rate limits, and pairs fogos.pt with satellite data.
- Vigia is a helper, not a warning system. Follow official advice from ANEPC and your local civil protection.

## Development

```sh
pip install pytest-homeassistant-custom-component
pytest
```

MIT licence.
