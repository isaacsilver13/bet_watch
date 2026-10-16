import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from app.ingest.nba_stats import PlayerStatRecord, fetch_player_stats, parse_player_stats
from app.ingest.records import GameRecord
from app.ingest.seed import SEEDS, seed_teams
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import PlayerSeasonStat, Team
from app.storage.stars import season_stars
from sqlalchemy import select

from tests.matchup_helpers import new_session_factory

T = datetime(2026, 2, 1, 12, tzinfo=timezone.utc)
HEADERS = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "MIN"]


def payload(*rows):
    return {"resultSets": [{"name": "LeagueDashPlayerStats", "headers": HEADERS,
                            "rowSet": [list(row) for row in rows]}]}


def rec(team, player, games, minutes):
    return PlayerStatRecord(team, player, games, minutes)


def test_player_rows_become_records_and_a_row_without_minutes_is_skipped() -> None:
    records = parse_player_stats(payload(
        (1, "Trae Young", 10, "WAS", 40, 34.5), (2, "Rookie", 11, "WAS", 0, None),
        (3, "Free Agent", 12, "", 5, 8.0),
    ))
    assert records == [PlayerStatRecord("WAS", "Trae Young", 40, 34.5)]


def test_the_fetch_asks_for_the_per_game_regular_season() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=payload())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_player_stats(http, 2026)

    assert asyncio.run(scenario()) == payload()
    assert (seen["Season"], seen["PerMode"], seen["SeasonType"]) == (
        "2025-26", "PerGame", "Regular Season")


async def _setup(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    async with factory() as session:
        rows = await session.execute(select(Team.abbr, Team.id).where(Team.sport == "nba"))
        return factory, ContextRepository(factory), dict(rows.all())


def test_a_seasons_player_rows_are_replaced_and_unknown_teams_reported(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo, ids = await _setup(tmp_path)
        first = await repo.save_player_stats("nba", 2026, [
            rec("WAS", "Trae Young", 40, 34.5), rec("WAS", "Alex Sarr", 39, 30.1),
            rec("XXX", "Nobody", 3, 3.0),
        ], T)
        assert (first.saved, first.unmatched) == (2, ["XXX"])

        async def rows():
            async with factory() as session:
                found = await session.scalars(select(PlayerSeasonStat))
                return {(r.season, r.team_id, r.player): r.minutes_pg for r in found.all()}

        assert await rows() == {
            (2026, ids["WAS"], "trae young"): 34.5, (2026, ids["WAS"], "alex sarr"): 30.1}
        # a trade: next day's file has him on Miami, and Washington's row goes
        await repo.save_player_stats("nba", 2026, [rec("MIA", "Trae Young", 41, 35.0)], T)
        assert await rows() == {(2026, ids["MIA"], "trae young"): 35.0}
        # a file that resolves to nothing leaves the table alone
        assert (await repo.save_player_stats("nba", 2026, [rec("XXX", "A", 9, 9.0)], T)).saved == 0
        assert len(await rows()) == 1

    asyncio.run(scenario())


def _final(i):
    return GameRecord(
        espn_id=str(i), season=2026, season_type="REG", week=None,
        start_time=datetime(2026, 1, 1, 20, tzinfo=timezone.utc) + timedelta(days=i),
        status="final", home_name="MIA", away_name="WAS", home_score=100, away_score=90,
        neutral=False,
    )


def test_early_in_the_season_last_years_minutes_decide(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo, ids = await _setup(tmp_path)
        await repo.save_player_stats("nba", 2025, [
            rec("WAS", f"old{n}", 70, 36.0 - n) for n in range(4)], T)
        await repo.save_player_stats("nba", 2026, [
            rec("WAS", f"new{n}", 20, 36.0 - n) for n in range(4)], T)
        games = GamesRepository(factory)
        await games.upsert_games("nba", "espn", [_final(i) for i in range(3)], T)
        async with factory() as session:
            stars = await season_stars(session, "nba", 2026, {ids["WAS"], ids["MIA"]}, T)
        assert stars[ids["WAS"]] == {"old0", "old1", "old2"}  # 3 games played
        assert stars[ids["MIA"]] == set()  # no minutes on file for them at all
        await games.upsert_games("nba", "espn", [_final(i) for i in range(3, 10)], T)
        async with factory() as session:
            stars = await season_stars(session, "nba", 2026, {ids["WAS"]}, T)
        assert stars[ids["WAS"]] == {"new0", "new1", "new2"}  # 10 games: this season decides

    asyncio.run(scenario())
