import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.ingest.espn import parse_injuries, parse_scoreboard
from app.ingest.nflverse_stats import TeamStatRecord
from app.ingest.open_meteo import WeatherRecord
from app.ingest.seed import seed_teams
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, Injury, Team, TeamGameStat, Venue, Weather
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


async def _loaded(tmp_path):
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    records = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
    den = next(r for r in records if r.espn_id == "401872995")
    playoff = replace(
        den, espn_id="900001", season=2025, season_type="POST", week=1, status="final",
        start_time=datetime(2026, 1, 18, 21, tzinfo=timezone.utc), home_score=24, away_score=21,
    )
    await GamesRepository(factory).upsert_games("nfl", "espn", [*records, playoff], NOW)
    return factory, ContextRepository(factory)


def _stat(team="DEN", opponent="SEA", week=6, season_type="REG", season=2026, **stats):
    return TeamStatRecord(season, season_type, week, team, opponent, stats or {"attempts": 30.0})


async def _count(factory, model):
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_stats_attach_to_the_game_for_either_team_and_resave_updates(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        records = [_stat(), _stat("SEA", "DEN", attempts=22.0)]
        result = await repo.save_team_stats("nfl", records, NOW)
        assert (result.saved, result.unmatched) == (2, [])
        assert await _count(factory, TeamGameStat) == 2
        await repo.save_team_stats("nfl", [_stat(attempts=99.0)], NOW + timedelta(days=1))
        async with factory() as session:
            den = await session.scalar(
                select(TeamGameStat).join(Team, Team.id == TeamGameStat.team_id)
                .where(Team.abbr == "DEN")
            )
        assert await _count(factory, TeamGameStat) == 2  # updated in place, not duplicated
        assert den.stats == {"attempts": 99.0}
        assert den.fetched_at.replace(tzinfo=timezone.utc) == NOW + timedelta(days=1)

    asyncio.run(scenario())


def test_postseason_stats_match_by_teams_not_by_nflverse_week_number(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        record = _stat(season=2025, season_type="POST", week=19)  # our game is POST week 1
        result = await repo.save_team_stats("nfl", [record], NOW)
        assert (result.saved, result.unmatched) == (1, [])

    asyncio.run(scenario())


def test_unknown_team_or_unloaded_game_is_reported_not_guessed(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        records = [_stat("XXX", "SEA"), _stat("DEN", "KC"), _stat(week=9)]
        result = await repo.save_team_stats("nfl", records, NOW)
        assert result.saved == 0
        assert result.unmatched == [
            "2026 REG wk6 XXX-SEA", "2026 REG wk6 DEN-KC", "2026 REG wk9 DEN-SEA",
        ]
        assert await _count(factory, TeamGameStat) == 0

    asyncio.run(scenario())


def test_each_injury_snapshot_is_appended_and_unknown_teams_are_reported(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        records = parse_injuries(load_json("espn/injuries_league.json"))
        first = await repo.save_injuries("nfl", records, NOW)
        assert first.saved == len(records) and first.unknown_teams == []
        again = await repo.save_injuries("nfl", records[:2], NOW + timedelta(days=1))
        assert again.saved == 2
        assert await _count(factory, Injury) == len(records) + 2  # snapshots, never overwritten
        stray = replace(records[0], team_espn_id="9999")
        result = await repo.save_injuries("nfl", [stray], NOW + timedelta(days=2))
        assert (result.saved, result.unknown_teams) == (0, ["9999"])

    asyncio.run(scenario())


def test_weather_targets_are_upcoming_scheduled_outdoor_games_with_coordinates(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        [target] = await repo.weather_targets("nfl", NOW, timedelta(days=7))
        assert (target.lat, target.lon) == (39.7439, -105.0201)  # DEN; the others are not eligible
        assert await repo.weather_targets("nfl", NOW, timedelta(days=2)) == []  # too far out
        async with factory() as session, session.begin():
            venue = await session.scalar(select(Venue).where(Venue.espn_id == "3937"))
            venue.roof = "dome"
        assert await repo.weather_targets("nfl", NOW, timedelta(days=7)) == []

    asyncio.run(scenario())


def test_saving_weather_twice_keeps_one_row_with_the_latest_forecast(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        async with factory() as session:
            game_id = await session.scalar(select(Game.id).where(Game.espn_id == "401872995"))
        await repo.save_weather(game_id, WeatherRecord(55.4, 11.8, 60, "Overcast"), NOW)
        await repo.save_weather(
            game_id, WeatherRecord(48.0, 20.0, 10, "Clear"), NOW + timedelta(hours=3)
        )
        assert await _count(factory, Weather) == 1
        async with factory() as session:
            row = await session.scalar(select(Weather))
        assert (row.temp_f, row.wind_mph, row.conditions) == (48.0, 20.0, "Clear")

    asyncio.run(scenario())


def test_a_venue_without_coordinates_gets_no_weather_lookup(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        async with factory() as session, session.begin():
            venue = await session.scalar(select(Venue).where(Venue.espn_id == "3937"))
            venue.lat = venue.lon = None  # like an international or neutral-site venue
        assert await repo.weather_targets("nfl", NOW, timedelta(days=7)) == []

    asyncio.run(scenario())


def test_a_rematch_later_in_the_season_gets_its_own_stats(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _loaded(tmp_path)
        den = next(
            r for r in parse_scoreboard(load_json("espn/scoreboard_week6.json"))
            if r.espn_id == "401872995"
        )
        rematch = replace(den, espn_id="900002", week=12, start_time=den.start_time
                          + timedelta(weeks=6))
        await GamesRepository(factory).upsert_games("nfl", "espn", [rematch], NOW)
        result = await repo.save_team_stats("nfl", [_stat(week=12, attempts=41.0)], NOW)
        assert result.saved == 1
        async with factory() as session:
            row = await session.scalar(select(TeamGameStat))
            game = await session.get(Game, row.game_id)
        assert (game.espn_id, game.week, row.stats) == ("900002", 12, {"attempts": 41.0})

    asyncio.run(scenario())
