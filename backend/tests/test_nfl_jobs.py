import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from app.jobs.__main__ import main, run_due_exit_code
from app.jobs.nfl import EspnClosesJob, EspnScoreboardJob, NflHistoryJob, SeedJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot
from app.storage.models import Game, GameLine, Team
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory
from tests.test_nflverse import _csv, _row

SLOT = Slot(
    "test", datetime(2026, 10, 11, tzinfo=timezone.utc), datetime(2026, 10, 12, tzinfo=timezone.utc)
)


def _transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/scoreboard"):
            if request.url.params.get("week") == "7":
                return httpx.Response(200, json={"events": []})
            return httpx.Response(200, json=load_json("espn/scoreboard_week6.json"))
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=load_json("espn/summary_final.json"))
        if request.url.path.endswith("games.csv"):
            return httpx.Response(
                200,
                text=_csv(
                    _row(
                        espn="401872964",
                        home_team="CLE",
                        away_team="PIT",
                        season="2026",
                        gameday="2026-10-04",
                    )
                ),
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _ctx(tmp_path, calls):
    factory = await new_session_factory(tmp_path)
    ctx = JobContext(factory, httpx.AsyncClient(transport=_transport(calls)), tmp_path / "raw")
    await SeedJob().run(ctx, SLOT)
    return ctx


async def _count(ctx, model):
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_scoreboard_job_fetches_current_and_next_week(tmp_path) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        assert await EspnScoreboardJob().run(ctx, SLOT) == "done"
        assert await _count(ctx, Game) == 4
        assert any(
            "week=7" in url and "seasontype=2" in url and "dates=2026" in url for url in calls
        )
        # The previous week too, so a game last seen live still gets its final after a PC-off gap.
        assert any("week=5" in url and "seasontype=2" in url for url in calls)

    asyncio.run(scenario())


def test_closes_job_backfills_final_game(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        await EspnScoreboardJob().run(ctx, SLOT)
        monkeypatch.setattr(
            "app.jobs.nfl._now", lambda: datetime(2026, 10, 11, 15, tzinfo=timezone.utc)
        )
        assert await EspnClosesJob().run(ctx, SLOT) == "done"
        async with ctx.session_factory() as session:
            close = await session.scalar(
                select(GameLine).where(
                    GameLine.kind == "close", GameLine.source == "espn", GameLine.ml_home == 124
                )
            )
        assert close is not None
        assert sum("summary" in url for url in calls) == 1
        assert await EspnClosesJob().run(ctx, SLOT) == "done"  # nothing left to fetch
        assert sum("summary" in url for url in calls) == 1

    asyncio.run(scenario())


def test_history_job_writes_raw_file_and_upserts(tmp_path) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        assert await NflHistoryJob().run(ctx, SLOT) == "done"
        assert (tmp_path / "raw" / "nflverse" / "games.csv").exists()
        async with ctx.session_factory() as session:
            line = await session.scalar(select(GameLine).where(GameLine.source == "nflverse"))
        assert line.kind == "close" and line.spread_home == 2.5

    asyncio.run(scenario())


def test_cli_rejects_unknown_job() -> None:
    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(main(["not-a-job"]))
    assert excinfo.value.code == 2


def test_run_due_cli_fails_only_for_a_terminal_job_failure() -> None:
    assert run_due_exit_code({"job:slot": "failed"}) == 1
    assert run_due_exit_code({"job:slot": "pending", "other:slot": "raw_saved"}) == 0


def test_history_runs_daily_so_a_missed_tuesday_cannot_hide_the_qb_flag() -> None:
    sunday = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)
    assert [s.key for s in NflHistoryJob().slots(sunday)] == ["nfl_history:2026-10-11"]


def test_the_seed_job_seeds_every_sport(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path, [])
        async with ctx.session_factory() as session:
            by_sport = dict(
                (await session.execute(select(Team.sport, func.count()).group_by(Team.sport))).all()
            )
        assert by_sport == {"nfl": 32, "nba": 30}

    asyncio.run(scenario())
