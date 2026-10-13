import asyncio
from datetime import datetime, timezone

import httpx
from app.jobs import nba as nba_jobs
from app.jobs.__main__ import JOBS
from app.jobs.nba import NbaHistoryJob, NbaScoreboardJob, nba_season_year
from app.jobs.nfl import EspnClosesJob, LinkEventsJob, SeedJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot
from app.storage.models import Game, GameLine
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 1, 16, 15, 0, tzinfo=timezone.utc)  # 10:00 ET on Jan 16
SLOT = Slot("test", NOW, NOW)


def _transport(calls, game_day="20260115"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        path = request.url.path
        if path.endswith("/basketball/nba/scoreboard"):
            if request.url.params["dates"] == game_day:
                return httpx.Response(200, json=load_json("espn_nba/scoreboard_day.json"))
            return httpx.Response(200, json={"events": []})
        if path.endswith("/basketball/nba/summary"):
            return httpx.Response(200, json=load_json("espn_nba/summary_final.json"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _ctx(tmp_path, calls, monkeypatch, game_day="20260115"):
    factory = await new_session_factory(tmp_path)
    ctx = JobContext(factory, httpx.AsyncClient(transport=_transport(calls, game_day)), tmp_path)
    await SeedJob().run(ctx, SLOT)
    monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
    return ctx


async def _count(ctx, model):
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_the_nba_season_is_named_for_the_year_it_ends() -> None:
    assert nba_season_year(datetime(2026, 10, 12, tzinfo=timezone.utc)) == 2027  # preseason
    assert nba_season_year(datetime(2026, 12, 31, tzinfo=timezone.utc)) == 2027
    assert nba_season_year(datetime(2027, 1, 1, tzinfo=timezone.utc)) == 2027
    assert nba_season_year(datetime(2027, 6, 20, tzinfo=timezone.utc)) == 2027  # the Finals
    assert nba_season_year(datetime(2027, 9, 30, tzinfo=timezone.utc)) == 2027


def test_the_scoreboard_job_reads_yesterday_through_two_days_ahead(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls: list = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        assert await NbaScoreboardJob().run(ctx, SLOT) == "done"
        days = [url.params["dates"] for url in calls]
        assert days == ["20260115", "20260116", "20260117", "20260118"]  # ET: Jan 16 +/- days
        assert await _count(ctx, Game) == 4  # the same payload four times is still four games
        async with ctx.session_factory() as session:
            games = {g.espn_id: g for g in (await session.scalars(select(Game))).all()}
        assert games["401810433"].neutral is True and games["401810433"].week is None
        assert games["401810439"].status == "scheduled"
        assert games["401810434"].sport == "nba"

    asyncio.run(scenario())


def test_an_empty_scoreboard_is_empty_not_an_error(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path, [], monkeypatch, game_day="19000101")
        assert await NbaScoreboardJob().run(ctx, SLOT) == "empty"

    asyncio.run(scenario())


def test_the_closes_job_backfills_finals_that_have_no_close_yet(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls: list = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        await NbaScoreboardJob().run(ctx, SLOT)
        calls.clear()
        monkeypatch.setattr("app.jobs.nfl._now", lambda: NOW)
        job = EspnClosesJob("nba", "nba_closes")
        assert job.name == "nba_closes"
        assert await job.run(ctx, SLOT) == "done"
        assert len(calls) == 3  # the three final games; the scheduled one is not asked for
        async with ctx.session_factory() as session:
            kinds = {(row.kind) for row in (await session.scalars(select(GameLine))).all()}
        assert {"open", "close"} <= kinds
        calls.clear()
        assert await job.run(ctx, SLOT) == "done" and calls == []  # nothing left to fetch

    asyncio.run(scenario())


def test_the_history_job_loads_the_season_so_far_and_its_closes(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls: list = []
        ctx = await _ctx(tmp_path, calls, monkeypatch)
        monkeypatch.setattr("app.jobs.nfl._now", lambda: NOW)
        assert await NbaHistoryJob().run(ctx, SLOT) == "done"
        days = [u.params["dates"] for u in calls if u.path.endswith("/scoreboard")]
        assert days[0] == "20251001" and days[-1] == "20260118"  # season start to two days ahead
        assert len(days) == len(set(days))
        assert await _count(ctx, Game) == 4
        assert len([u for u in calls if u.path.endswith("/summary")]) == 3

    asyncio.run(scenario())


def test_nba_jobs_are_registered_and_the_link_job_is_per_sport() -> None:
    names = {job.name for job in JOBS}
    assert {"nba_scoreboard", "nba_closes", "nba_link_events", "nba_history"} <= names
    assert LinkEventsJob("nba", "nba_link_events").name == "nba_link_events"
    assert NbaScoreboardJob().slots(NOW)  # runs hourly
    assert NbaHistoryJob().slots(NOW) == []  # manual only
