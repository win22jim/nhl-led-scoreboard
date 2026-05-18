"""Open-Meteo current-weather worker.

Drop-in replacement for owmWxWorker that uses api.open-meteo.com — no API
key, no signup, no rate limit for non-commercial use, global coverage.
Picked specifically because OWM moved their One Call endpoint to a paid v3
subscription tier, leaving free OWM keys unable to fetch weather.

Populates the same `data.wx_*` fields as the existing workers so the weather
board renders unchanged. Icons map WMO weather codes (Open-Meteo uses these)
to the existing OWMCode column in ecIcons_utf8.csv.
"""

import json
import logging
from datetime import datetime, timedelta

import httpx

from api.weather.wx_utils import degrees_to_direction, dew_point, get_csv, wind_chill
from utils import sb_cache

debug = logging.getLogger("scoreboard")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Map WMO codes (Open-Meteo) -> the OWMCode buckets the icon CSV is keyed on.
# Source: https://open-meteo.com/en/docs#weathervariables
_WMO_TO_OWM = {
    0: 800,                                              # Clear sky
    1: 801, 2: 802, 3: 804,                              # Mostly clear / partly cloudy / overcast
    45: 741, 48: 741,                                    # Fog / depositing rime fog
    51: 300, 53: 300, 55: 300,                           # Drizzle
    56: 300, 57: 300,                                    # Freezing drizzle
    61: 500, 63: 500, 65: 500,                           # Rain
    66: 500, 67: 500,                                    # Freezing rain
    71: 600, 73: 600, 75: 600,                           # Snowfall
    77: 600,                                             # Snow grains
    80: 500, 81: 500, 82: 500,                           # Rain showers
    85: 600, 86: 600,                                    # Snow showers
    95: 200, 96: 200, 99: 200,                           # Thunderstorm
}


def _wmo_summary(code):
    return {
        0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
        45: "Fog", 48: "Freezing Fog",
        51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
        56: "Freezing Drizzle", 57: "Heavy Freezing Drizzle",
        61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
        66: "Freezing Rain", 67: "Heavy Freezing Rain",
        71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 77: "Snow Grains",
        80: "Rain Showers", 81: "Rain Showers", 82: "Heavy Rain Showers",
        85: "Snow Showers", 86: "Heavy Snow Showers",
        95: "Thunderstorm", 96: "Thunderstorm w/ Hail", 99: "Severe Thunderstorm",
    }.get(int(code), "Unknown")


class openMeteoWxWorker(object):
    def __init__(self, data, scheduler):
        self.data = data
        self.scheduler = scheduler
        self.weather_frequency = data.config.weather_update_freq
        self.time_format = data.config.time_format
        self.icons = get_csv("ecIcons_utf8.csv")
        self.network_issues = False

        scheduler.add_job(self.getWeather, "interval",
                          minutes=self.weather_frequency, jitter=60,
                          id="openMeteoWeather")

        if self.data.config.weather_units.lower() not in ("metric", "imperial"):
            debug.info("Weather units not set correctly, defaulting to imperial")
            self.data.config.weather_units = "imperial"

        # First call is delayed slightly so a crash loop can't hammer the
        # API on every restart. Matches the pattern owmWeather established.
        if sb_cache.get("weather") is None:
            run_date = datetime.now() + timedelta(seconds=10)
            scheduler.add_job(self.getWeather, "date", run_date=run_date, id="openMeteoWeather_startup")
        else:
            self.getWeather()

    def _icon_for_code(self, wmo_code):
        owm_bucket = _WMO_TO_OWM.get(int(wmo_code), 800)
        for row in self.icons:
            try:
                if int(row["OWMCode"]) == owm_bucket:
                    return row["font"]
            except (KeyError, ValueError):
                continue
        return ""

    def getWeather(self):
        if self.data.config.weather_units == "metric":
            self.data.wx_units = ["C", "kph", "mm", "miles", "hPa", "ca"]
            temp_unit = "celsius"
            wind_unit = "kmh"
        else:
            self.data.wx_units = ["F", "mph", "in", "miles", "MB", "us"]
            temp_unit = "fahrenheit"
            wind_unit = "mph"

        lat = self.data.latlng[0]
        lon = self.data.latlng[1]

        wx = None
        try:
            wx_cache, expiration_time = sb_cache.get("weather", expire_time=True)
            if wx_cache is None:
                debug.info("Refreshing Open-Meteo current observations")
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "current": ",".join([
                        "temperature_2m", "apparent_temperature", "weather_code",
                        "wind_speed_10m", "wind_gust_10m", "wind_direction_10m",
                        "relative_humidity_2m", "pressure_msl", "visibility",
                    ]),
                    "temperature_unit": temp_unit,
                    "wind_speed_unit": wind_unit,
                    "timezone": "auto",
                }
                response = httpx.get(OPEN_METEO_URL, params=params, timeout=10.0)
                wx = response.json()
                if response.status_code != 200:
                    raise Exception(f"Open-Meteo HTTP {response.status_code}: {response.text[:200]}")
                if not isinstance(wx, dict) or "current" not in wx:
                    raise Exception(f"Open-Meteo unexpected response: {str(wx)[:200]}")
                self.network_issues = False
                self.data.wx_updated = True
                expiretime = (self.weather_frequency * 60) - 1
                sb_cache.set("weather", json.dumps(wx, indent=4), expire=expiretime)
            else:
                current_time = datetime.now().timestamp()
                remaining = int(max(0, int(expiration_time) - current_time))
                debug.info(f"Loading weather from cache... cache expires in {remaining} seconds")
                wx = json.loads(wx_cache)
                self.network_issues = False
                self.data.wx_updated = True

        except Exception as e:
            debug.error(f"Open-Meteo fetch failed: {e}")
            self.data.wx_updated = False
            self.network_issues = True

        if self.network_issues or not wx:
            return

        try:
            current = wx.get("current") or {}
            code = current.get("weather_code", 0)
            wx_icon = self._icon_for_code(code)
            wx_summary = _wmo_summary(code)

            temp = current.get("temperature_2m")
            app_temp = current.get("apparent_temperature")
            humidity = current.get("relative_humidity_2m", 0)
            wind_speed = current.get("wind_speed_10m", 0) or 0
            wind_gust = current.get("wind_gust_10m", 0) or 0
            wind_deg = current.get("wind_direction_10m", 0) or 0
            pressure = current.get("pressure_msl")
            visibility_m = current.get("visibility", 10000) or 10000

            # Wind-chill: Open-Meteo's apparent_temperature already factors it
            # in, but we still need a fallback when the field is missing.
            if app_temp is None and temp is not None:
                check = 10.0 if self.data.config.weather_units == "metric" else 50.0
                if float(temp) < check:
                    # Open-Meteo gives wind in our requested unit, but
                    # wind_chill expects m/s. Convert.
                    ws_ms = float(wind_speed)
                    if self.data.config.weather_units == "metric":
                        ws_ms = float(wind_speed) / 3.6  # km/h -> m/s
                    else:
                        ws_ms = float(wind_speed) * 0.44704  # mph -> m/s
                    app_temp = round(wind_chill(float(temp), ws_ms, "mps"), 1)
                else:
                    app_temp = temp

            wx_temp = (str(round(float(temp), 1)) + self.data.wx_units[0]) if temp is not None else "--"
            wx_app_temp = (str(round(float(app_temp), 1)) + self.data.wx_units[0]) if app_temp is not None else "--"
            wx_humidity = f"{int(humidity)}%"
            wx_dewpoint = "--"
            if temp is not None and humidity:
                try:
                    wx_dewpoint = str(round(dew_point(float(temp), int(humidity)), 1)) + self.data.wx_units[0]
                except Exception:
                    pass

            wx_windspeed = f"{round(float(wind_speed), 1)}{self.data.wx_units[1]}"
            wx_windgust = f"{round(float(wind_gust), 1)}{self.data.wx_units[1]}"
            winddir = degrees_to_direction(float(wind_deg))

            if self.data.config.weather_units == "metric":
                wx_pressure = (f"{int(pressure)} {self.data.wx_units[4]}") if pressure is not None else "--"
                wx_visibility = f"{round(float(visibility_m) / 1000, 1)} km"
            else:
                wx_pressure = (f"{int(pressure)} {self.data.wx_units[4]}") if pressure is not None else "--"
                wx_visibility = f"{round(float(visibility_m) * 0.000621371, 1)} mi"

            wx_timestamp = datetime.now().strftime(
                "%m/%d %H:%M" if self.time_format == "%H:%M" else "%m/%d %I:%M %p"
            )
            self.data.wx_current = [wx_timestamp, wx_icon, wx_summary, wx_temp, wx_app_temp, wx_humidity, wx_dewpoint]
            self.data.wx_curr_wind = [wx_windspeed, winddir[0], winddir[1], wx_windgust, wx_pressure, "", wx_visibility]
            debug.info(self.data.wx_current)
            debug.info(self.data.wx_curr_wind)
        except Exception as e:
            debug.error(f"Open-Meteo parse failed: {e}", exc_info=True)
            self.data.wx_updated = False
