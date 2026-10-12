import asyncio
from datetime import datetime, timezone

import httpx
from app.ingest.espn import parse_injuries, parse_scoreboard
from app.ingest.seed import seed_teams
from app.jobs.__main__ import JOBS
from app.jobs.context import (
    InjuriesJob,
    TeamStatsHistoryJob,
    TeamStatsJob,
    WeatherJob,
    nfl_season_year,
)
from app.jobs.runner import JobContext
from app.jobs.slots import CENTRAL, Slot, hourly_slots
from app.storage.games_repository import GamesRepository
from app.storage.models import Injury, TeamGameStat, Weather
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
SLOT = Slot("test", NOW, NOW)
STATS_HEADER = "season,week,team,season_type,game_id,opponent_team,attempts,passing_epa,carries"


def _stats_csv() -> str:
    return (
        f"{STATS_HEADER}\n"
        "2026,6,DEN,REG,2026_06_SEA_DEN,SEA,30,4.0,20\n"
        "2026,6,SEA,REG,2026_06_SEA_DEN,DEN,25,-1.0,22\n"
    )


def _transport(calls, injuries=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        path = request.url.path
        if path.endswith("stats_team_week_2026.csv"):
            return httpx.Response(200, text=_stats_csv())
        if path.endswith("/injuries"):
            return httpx.Response(200, json=injuries or load_json("espn/injuries_league.json"))
        if "open-meteo" in request.url.host:
            return httpx.Response(200, json=load_json("open_meteo/denver_forecast.json"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _ctx(tmp_path, calls, monkeypatch, now=NOW, injuries=None):
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    records = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
    await GamesRepository(factory).upsert_games("nfl", "espn", records, NOW)
    monkeypatch.setattr("app.jobs.context._now", lambda: now)
    http = httpx.AsyncClient(transport=_transport(calls, injuries))
    return JobContext(factory, http, tmp_path / "raw")


async def _count(ctx, model):
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_season_rolls_over_in_march_not_january() -> None:
    assert nfl_season_year(datetime(2027, 1, 20, tzinfo=timezone.utc)) == 2026
    assert nfl_season_year(datetime(2027, 3, 1, tzinfo=timezone.utc)) == 2027
    assert nfl_season_year(NOW) == 2026


def test_team_stats_job_saves_rows_and_keeps_a_raw_copy(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        assert await TeamStatsJob().run(ctx, SLOT) == "done"
        assert await _count(ctx, TeamGameStat) == 2
        assert (tmp_path / "raw" / "nflverse" / "stats_team_week_2026.csv").exists()

    asyncio.run(scenario())


def test_team_stats_job_is_empty_when_the_new_season_file_is_not_out_yet(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        later = datetime(2027, 6, 1, tzinfo=timezone.utc)  # 2027 file does not exist: 404
        ctx = await _ctx(tmp_path, [], monkeypatch, now=later)
        assert await TeamStatsJob().run(ctx, SLOT) == "empty"
        assert await _count(ctx, TeamGameStat) == 0

    asyncio.run(scenario())


def test_history_job_walks_every_season_from_2015(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        assert await TeamStatsHistoryJob().run(ctx, SLOT) == "done"
        files = [str(url).rsplit("/", 1)[-1] for url in calls]
        assert files[0] == "stats_team_week_2015.csv" and files[-1] == "stats_team_week_2026.csv"
        assert len(files) == 12
        assert await _count(ctx, TeamGameStat) == 2

    asyncio.run(scenario())


def test_injuries_job_stores_one_snapshot_and_skips_an_empty_league(tmp_path, monkeypatch) -> None:
    (tmp_path / "empty").mkdir()
    expected = len(parse_injuries(load_json("espn/injuries_league.json")))

    async def scenario() -> None:
        ctx = await _ctx(tmp_path, [], monkeypatch)
        assert await InjuriesJob().run(ctx, SLOT) == "done"
        assert await _count(ctx, Injury) == expected
        empty = await _ctx(tmp_path / "empty", [], monkeypatch, injuries={"injuries": []})
        assert await InjuriesJob().run(empty, SLOT) == "empty"
        assert await _count(empty, Injury) == 0

    asyncio.run(scenario())


def test_weather_job_fetches_each_outdoor_game_at_its_venue(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        assert await WeatherJob().run(ctx, SLOT) == "done"
        [call] = calls
        assert (call.params["latitude"], call.params["longitude"]) == ("39.7439", "-105.0201")
        async with ctx.session_factory() as session:
            row = await session.scalar(select(Weather))
        assert (row.temp_f, row.wind_mph, row.precip_pct, row.conditions) == (
            55.4, 11.8, 60.0, "Overcast",
        )

    asyncio.run(scenario())


def test_weather_job_is_empty_with_no_upcoming_outdoor_games(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        far_off = datetime(2026, 12, 1, tzinfo=timezone.utc)
        ctx = await _ctx(tmp_path, calls, monkeypatch, now=far_off)
        assert await WeatherJob().run(ctx, SLOT) == "empty"
        assert calls == []

    asyncio.run(scenario())


def test_weather_slots_are_three_hour_windows_in_central_time() -> None:
    now = datetime(2026, 10, 12, 18, 30, tzinfo=timezone.utc)  # 1:30pm CDT
    slots = hourly_slots("weather", now, last_hour=22, step=3)
    assert [s.key for s in slots] == ["weather:2026-10-12T08", "weather:2026-10-12T11"]
    assert slots[0].window_end - slots[0].window_start == (
        slots[1].window_start - slots[0].window_start
    )
    assert slots[1].window_start.astimezone(CENTRAL).hour == 11


def test_new_jobs_are_registered_with_the_cli() -> None:
    names = {job.name for job in JOBS}
    assert {"team_stats", "team_stats_history", "injuries", "weather", "espn_scoreboard"} <= names
