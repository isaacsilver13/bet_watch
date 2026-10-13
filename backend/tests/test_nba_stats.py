import asyncio
from datetime import date, datetime, timezone

import httpx
from app.ingest.espn import parse_scoreboard
from app.ingest.nba_stats import (
    fetch_team_game_logs,
    parse_team_game_logs,
    season_label,
)
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import nba as nba_jobs
from app.jobs.nba import NbaTeamStatsJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, Team, TeamGameStat
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 1, 16, 15, 0, tzinfo=timezone.utc)
SLOT = Slot("test", NOW, NOW)


def _records():
    return parse_team_game_logs(
        load_json("nba_stats/teamgamelogs_advanced.json"),
        load_json("nba_stats/teamgamelogs_four_factors.json"),
    )


def test_a_season_is_labelled_the_way_stats_nba_com_writes_it() -> None:
    assert season_label(2026) == "2025-26"
    assert season_label(2000) == "1999-00"


def test_advanced_and_four_factor_rows_are_joined_per_team_game() -> None:
    records = _records()
    assert len(records) == 30
    orl = next(r for r in records if r.team == "ORL")
    assert (orl.season, orl.game_date, orl.opponent) == (2026, date(2026, 1, 15), "MEM")
    assert {"OFF_RATING", "DEF_RATING", "NET_RATING", "PACE", "POSS", "EFG_PCT",
            "TM_TOV_PCT", "OREB_PCT"} <= set(orl.stats)  # advanced
    assert {"FTA_RATE", "OPP_EFG_PCT", "OPP_FTA_RATE", "OPP_TOV_PCT",
            "OPP_OREB_PCT"} <= set(orl.stats)  # four factors, same game and team
    assert orl.stats["NET_RATING"] == round(orl.stats["OFF_RATING"] - orl.stats["DEF_RATING"], 1)
    # The two teams of a game see each other's numbers mirrored.
    mem = next(r for r in records if r.team == "MEM" and r.game_date == orl.game_date)
    assert mem.opponent == "ORL"
    assert mem.stats["OFF_RATING"] == orl.stats["DEF_RATING"]


def test_a_game_missing_from_the_four_factors_keeps_its_advanced_numbers() -> None:
    four = load_json("nba_stats/teamgamelogs_four_factors.json")
    four["resultSets"][0]["rowSet"] = []
    records = parse_team_game_logs(load_json("nba_stats/teamgamelogs_advanced.json"), four)
    assert len(records) == 30 and "OFF_RATING" in records[0].stats
    assert "FTA_RATE" not in records[0].stats


def test_the_logs_are_requested_with_browser_headers_for_one_season_and_phase() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load_json("nba_stats/teamgamelogs_advanced.json"))

    async def scenario() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_team_game_logs(http, 2026, "Regular Season", "Four Factors")

    asyncio.run(scenario())
    request = seen[0]
    assert request.url.host == "stats.nba.com" and request.url.path == "/stats/teamgamelogs"
    assert dict(request.url.params) == {
        "MeasureType": "Four Factors", "PerMode": "Totals", "Season": "2025-26",
        "SeasonType": "Regular Season", "LeagueID": "00",
    }
    assert request.headers["referer"] == "https://www.nba.com/"  # the site rejects bare clients


async def _seeded(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
    await GamesRepository(factory).upsert_games("nba", "espn", records, NOW)
    return factory


def test_stats_attach_to_the_game_by_eastern_date_and_team_pair(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        repo = ContextRepository(factory)
        result = await repo.save_game_log_stats("nba", _records(), NOW)
        assert result.saved == 8  # four games in the database, two teams each
        assert len(result.unmatched) == 22  # the other games are not loaded
        again = await repo.save_game_log_stats("nba", _records(), NOW)
        assert again.saved == 8
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(TeamGameStat)) == 8
            game = await session.scalar(select(Game).where(Game.espn_id == "401810433"))
            orl = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "ORL"))
            row = await session.scalar(select(TeamGameStat).where(
                TeamGameStat.game_id == game.id, TeamGameStat.team_id == orl.id))
        assert row.stats["OFF_RATING"] > 0 and "OPP_EFG_PCT" in row.stats

    asyncio.run(scenario())


def test_a_late_game_belongs_to_the_eastern_day_it_tipped_on(tmp_path) -> None:
    """BOS-MIA tips at 00:30 UTC on the 16th: Eastern date the 15th, the date stats.nba.com uses."""

    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        result = await ContextRepository(factory).save_game_log_stats("nba", _records(), NOW)
        assert not [u for u in result.unmatched if "BOS" in u or "MIA" in u]

    asyncio.run(scenario())


def _empty() -> dict:
    empty = load_json("nba_stats/teamgamelogs_advanced.json")
    empty["resultSets"][0]["rowSet"] = []
    return empty


def _transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        params = request.url.params
        if request.url.host == "stats.nba.com" and params["SeasonType"] == "Regular Season":
            name = "advanced" if params["MeasureType"] == "Advanced" else "four_factors"
            return httpx.Response(200, json=load_json(f"nba_stats/teamgamelogs_{name}.json"))
        if request.url.host == "stats.nba.com":
            return httpx.Response(200, json=_empty())
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_the_stats_job_loads_the_current_season_and_skips_empty_phases(tmp_path, monkeypatch):
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        calls: list = []
        ctx = JobContext(factory, httpx.AsyncClient(transport=_transport(calls)), tmp_path)
        monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "done"
        wanted = {(c.params["Season"], c.params["SeasonType"], c.params["MeasureType"])
                  for c in calls}
        assert wanted == {
            ("2025-26", phase, measure)
            for phase in ("Regular Season", "PlayIn", "Playoffs")
            for measure in ("Advanced", "Four Factors")
        }
        async with ctx.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(TeamGameStat)) == 8

    asyncio.run(scenario())


def test_the_stats_job_reports_empty_before_the_season_has_games(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_empty()))
        ctx = JobContext(factory, httpx.AsyncClient(transport=transport), tmp_path)
        monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "empty"

    asyncio.run(scenario())
