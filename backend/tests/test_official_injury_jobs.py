import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from app.ingest.espn import parse_scoreboard
from app.ingest.records import NamedInjury
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import nba as nba_jobs
from app.jobs.__main__ import JOBS
from app.jobs.context import NflOfficialInjuriesJob
from app.jobs.nba import NbaOfficialInjuriesJob, NbaPlayerStatsJob
from app.jobs.runner import JobContext
from app.jobs.slots import CENTRAL, Slot
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import Injury, PlayerSeasonStat
from sqlalchemy import func, select

from tests.matchup_helpers import FIXTURES, load_json, new_session_factory

NFL_NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)  # Monday; week 6 is next
NBA_NOW = datetime(2026, 1, 15, 20, 37, tzinfo=timezone.utc)  # 3:37pm Eastern
PDF = (FIXTURES / "nba_injury" / "report_2026-02-28_0500PM_p1-3.pdf").read_bytes()
HEADER = (
    "season,season_type,game_type,team,week,gsis_id,position,full_name,first_name,last_name,"
    "report_primary_injury,report_secondary_injury,report_status,practice_primary_injury,"
    "practice_secondary_injury,practice_status"
)


def nix_csv(week):
    return (f"{HEADER}\n2026,REG,REG,DEN,{week},00-1,QB,Bo Nix,Bo,Nix,Ankle,,Questionable,Ankle,,"
            "Limited Participation in Practice\n")


async def _ctx(tmp_path, handler):
    tmp_path.mkdir(parents=True, exist_ok=True)
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JobContext(factory, http, tmp_path / "raw")


async def _count(ctx, model):
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def _nfl_handler(text, status=200):
    return lambda request: httpx.Response(status, text=text)


async def _nfl_ctx(tmp_path, monkeypatch, handler):
    ctx = await _ctx(tmp_path, handler)
    records = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
    await GamesRepository(ctx.session_factory).upsert_games("nfl", "espn", records, NFL_NOW)
    monkeypatch.setattr("app.jobs.context._now", lambda: NFL_NOW)
    return ctx


def test_the_nfl_job_writes_nothing_until_the_files_newest_week_is_the_upcoming_one(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        stale = await _nfl_ctx(tmp_path / "stale", monkeypatch, _nfl_handler(nix_csv(5)))
        job = NflOfficialInjuriesJob()
        assert await job.run(stale, Slot("t", NFL_NOW, NFL_NOW)) == "empty"  # still last week's
        assert await _count(stale, Injury) == 0
        missing = await _nfl_ctx(tmp_path / "missing", monkeypatch, _nfl_handler("", 404))
        assert await job.run(missing, Slot("t", NFL_NOW, NFL_NOW)) == "empty"
        fresh = await _nfl_ctx(tmp_path / "fresh", monkeypatch, _nfl_handler(nix_csv(6)))
        assert await job.run(fresh, Slot("t", NFL_NOW, NFL_NOW)) == "done"
        async with fresh.session_factory() as session:
            [row] = (await session.scalars(select(Injury))).all()
        assert (row.source, row.player, row.position, row.status, row.detail) == (
            "nflverse", "Bo Nix", "QB", "Questionable", "Ankle · Limited in practice")

    asyncio.run(scenario())


def test_the_nfl_job_runs_daily_and_on_wednesday_to_friday_evenings() -> None:
    job = NflOfficialInjuriesJob()
    wednesday = datetime(2026, 10, 14, 20, 0, tzinfo=CENTRAL)
    assert [s.key for s in job.slots(wednesday)] == [
        "nfl_injuries_official:2026-10-14", "nfl_injuries_official:2026-10-14T18"]
    saturday = datetime(2026, 10, 17, 20, 0, tzinfo=CENTRAL)
    assert [s.key for s in job.slots(saturday)] == ["nfl_injuries_official:2026-10-17"]


def _pdf_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PDF)


def test_the_nba_job_stores_the_report_with_its_own_time(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path, _pdf_handler)
        monkeypatch.setattr(nba_jobs, "_now", lambda: NBA_NOW)
        assert await NbaOfficialInjuriesJob().run(ctx, Slot("t", NBA_NOW, NBA_NOW)) == "done"
        assert await _count(ctx, Injury) == 22
        async with ctx.session_factory() as session:
            row = await session.scalar(select(Injury).where(Injury.player == "Trae Young"))
        assert (row.source, row.status, row.position) == ("nba_official", "Out", None)
        assert row.reported_at.replace(tzinfo=timezone.utc) == datetime(
            2026, 1, 15, 20, 30, tzinfo=timezone.utc)  # the 3:30pm Eastern report
        assert row.observed_at.replace(tzinfo=timezone.utc) == NBA_NOW

    asyncio.run(scenario())


def test_the_nba_job_runs_hourly_from_ten_to_ten_central() -> None:
    evening = datetime(2026, 1, 15, 22, 30, tzinfo=CENTRAL)
    keys = [s.key for s in NbaOfficialInjuriesJob().slots(evening)]
    assert keys[0] == "nba_injuries_official:2026-01-15T10"
    assert keys[-1] == "nba_injuries_official:2026-01-15T22" and len(keys) == 13


def test_a_failed_or_unreadable_report_fails_the_slot_and_keeps_the_last_snapshot(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path / "a", lambda request: httpx.Response(403))
        monkeypatch.setattr(nba_jobs, "_now", lambda: NBA_NOW)
        earlier = NamedInjury("Washington Wizards", "Trae Young", None, "Out", "Knee")
        await ContextRepository(ctx.session_factory).save_official_injuries(
            "nba", "nba_official", [earlier], NBA_NOW.replace(hour=14))
        job, slot = NbaOfficialInjuriesJob(), Slot("t", NBA_NOW, NBA_NOW)
        with pytest.raises(RuntimeError):  # nothing answered for the last four quarter hours
            await job.run(ctx, slot)
        garbled = await _ctx(tmp_path / "b", lambda request: httpx.Response(200, content=b"nope"))
        with pytest.raises(Exception):  # noqa: B017  (pypdf's read error)
            await job.run(garbled, slot)
        assert await _count(ctx, Injury) == 1 and await _count(garbled, Injury) == 0
        # an all-clear report ends the slot empty without writing an empty snapshot
        monkeypatch.setattr(nba_jobs, "parse_report", lambda pdf: [])
        clear = await _ctx(tmp_path / "c", _pdf_handler)
        assert await job.run(clear, slot) == "empty"
        assert await _count(clear, Injury) == 0

    asyncio.run(scenario())


def _stats_payload(*rows):
    return {"resultSets": [{"headers": [
        "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "GP", "MIN"],
        "rowSet": [list(r) for r in rows]}]}


def test_the_player_stats_job_stores_this_season_and_last(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        season = request.url.params["Season"]
        rows = {"2025-26": [(1, "Trae Young", 5, "WAS", 40, 34.5)],
                "2024-25": [(1, "Trae Young", 6, "ATL", 70, 36.0)]}.get(season, [])
        return httpx.Response(200, json=_stats_payload(*rows))

    async def scenario() -> None:
        ctx = await _ctx(tmp_path, handler)
        monkeypatch.setattr(nba_jobs, "_now", lambda: NBA_NOW)
        job = NbaPlayerStatsJob()
        assert await job.run(ctx, Slot("t", NBA_NOW, NBA_NOW)) == "done"
        async with ctx.session_factory() as session:
            rows = (await session.scalars(select(PlayerSeasonStat))).all()
        assert sorted((r.season, r.player, r.games, r.minutes_pg) for r in rows) == [
            (2025, "trae young", 70, 36.0), (2026, "trae young", 40, 34.5)]
        empty = await _ctx(tmp_path / "empty", lambda request: httpx.Response(
            200, json=_stats_payload()))
        assert await job.run(empty, Slot("t", NBA_NOW, NBA_NOW)) == "empty"

    asyncio.run(scenario())


def test_the_new_jobs_are_registered() -> None:
    names = {job.name for job in JOBS}
    assert {"nfl_injuries_official", "nba_injuries_official", "nba_player_stats"} <= names
