import asyncio
from datetime import datetime, timezone

import httpx
from app.ingest.open_meteo import WeatherRecord, fetch_forecast, kickoff_hour, parse_forecast

from tests.matchup_helpers import load_json


def test_kickoff_hour_rounds_to_the_nearest_utc_hour() -> None:
    assert kickoff_hour(datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)) == "2026-10-16T00:00"
    assert kickoff_hour(datetime(2026, 10, 16, 0, 45, tzinfo=timezone.utc)) == "2026-10-16T01:00"
    assert kickoff_hour(datetime(2026, 10, 15, 23, 45, tzinfo=timezone.utc)) == "2026-10-16T00:00"


def test_forecast_for_denver_thursday_night() -> None:
    payload = load_json("open_meteo/denver_forecast.json")
    record = parse_forecast(payload, datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc))
    assert record == WeatherRecord(
        temp_f=55.4, wind_mph=11.8, precip_pct=60, conditions="Overcast"
    )


def test_kickoff_outside_the_window_has_no_forecast() -> None:
    payload = load_json("open_meteo/denver_forecast.json")
    assert parse_forecast(payload, datetime(2026, 11, 1, 18, tzinfo=timezone.utc)) is None
    assert parse_forecast({}, datetime(2026, 10, 16, tzinfo=timezone.utc)) is None


def test_unknown_weather_code_leaves_conditions_blank() -> None:
    payload = {"hourly": {"time": ["2026-10-16T00:00"], "temperature_2m": [50.0],
                          "wind_speed_10m": [3.0], "precipitation_probability": [0],
                          "weather_code": [123]}}
    record = parse_forecast(payload, datetime(2026, 10, 16, tzinfo=timezone.utc))
    assert record.conditions is None and record.temp_f == 50.0


def test_fetch_requests_imperial_units_in_utc() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"hourly": {}})

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await fetch_forecast(http, 39.7439, -105.0201)

    asyncio.run(go())
    assert seen["temperature_unit"] == "fahrenheit" and seen["wind_speed_unit"] == "mph"
    assert seen["timezone"] == "UTC" and seen["latitude"] == "39.7439"
