"""Open-Meteo hourly forecast (free, no key) for the kickoff hour of outdoor games.

Verified 2026-10-12: `timezone=UTC` makes `hourly.time` UTC strings like "2026-10-16T00:00";
`forecast_days` accepts 0-16; imperial units are requested explicitly.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 8
WMO_CONDITIONS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


@dataclass(frozen=True)
class WeatherRecord:
    temp_f: Optional[float]
    wind_mph: Optional[float]
    precip_pct: Optional[float]
    conditions: Optional[str]


def kickoff_hour(kickoff: datetime) -> str:
    """The forecast hour nearest kickoff, as Open-Meteo prints it (UTC, no offset)."""
    utc = kickoff.astimezone(timezone.utc)
    rounded = utc.replace(minute=0, second=0, microsecond=0)
    if utc.minute >= 30:
        rounded += timedelta(hours=1)
    return rounded.strftime("%Y-%m-%dT%H:%M")


def parse_forecast(payload: Mapping[str, Any], kickoff: datetime) -> Optional[WeatherRecord]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    wanted = kickoff_hour(kickoff)
    if wanted not in times:
        return None  # kickoff is outside the forecast window
    index = times.index(wanted)

    def at(name: str) -> Any:
        values = hourly.get(name) or []
        return values[index] if index < len(values) else None

    code = at("weather_code")
    return WeatherRecord(
        temp_f=at("temperature_2m"),
        wind_mph=at("wind_speed_10m"),
        precip_pct=at("precipitation_probability"),
        conditions=WMO_CONDITIONS.get(code) if code is not None else None,
    )


async def fetch_forecast(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    response = await http.get(
        FORECAST_URL,
        params={
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": "temperature_2m,wind_speed_10m,precipitation_probability,weather_code",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
            "forecast_days": str(FORECAST_DAYS),
        },
    )
    response.raise_for_status()
    return response.json()
