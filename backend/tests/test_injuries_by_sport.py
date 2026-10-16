import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from app.ingest.espn import parse_injuries
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import context as context_jobs
from app.jobs.context import InjuriesJob
from app.jobs.runner import JobContext
from app.jobs.slots import CENTRAL, Slot
from app.storage.context_repository import ContextRepository
from app.storage.models import Injury, Team
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

T1 = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=3)
SLOT = Slot("test", T1, T1)


async def _seeded(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    return factory


async def _ids(factory, sport, *abbrs):
    async with factory() as session:
        rows = await session.execute(
            select(Team.id).where(Team.sport == sport, Team.abbr.in_(abbrs)))
        return {row[0] for row in rows.all()}


def test_a_newer_snapshot_in_one_sport_does_not_hide_the_other_sports_report(tmp_path) -> None:
    """The NFL job ran at T2; the NBA report from T1 is still the newest NBA report."""

    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        repo = ContextRepository(factory)
        nba_report = parse_injuries(load_json("espn_nba/injuries_league.json"))
        nfl_report = parse_injuries(load_json("espn/injuries_league.json"))
        await repo.save_injuries("nba", nba_report, T1)
        await repo.save_injuries("nfl", nfl_report, T2)
        nba = await _ids(factory, "nba", "ATL", "BOS", "MIA")
        by_team, observed = await repo.injury_snapshot("nba", nba, T2 + timedelta(days=1))
        assert observed == T1
        assert sum(len(rows) for rows in by_team.values()) == 10
        nfl = await _ids(factory, "nfl", "DEN", "SEA")
        _, nfl_observed = await repo.injury_snapshot("nfl", nfl, T2 + timedelta(days=1))
        assert nfl_observed == T2

    asyncio.run(scenario())


def test_the_snapshot_at_a_cutoff_ignores_later_reports(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        repo = ContextRepository(factory)
        records = parse_injuries(load_json("espn_nba/injuries_league.json"))
        await repo.save_injuries("nba", records, T1)
        await repo.save_injuries("nba", records[:2], T2)
        ids = await _ids(factory, "nba", "ATL", "BOS", "MIA")
        _, before = await repo.injury_snapshot("nba", ids, T1 + timedelta(hours=1))
        assert before == T1
        by_team, after = await repo.injury_snapshot("nba", ids, T2)
        assert after == T2 and sum(len(rows) for rows in by_team.values()) == 2
        none_yet, observed = await repo.injury_snapshot("nba", ids, T1 - timedelta(days=1))
        assert observed is None and all(rows == [] for rows in none_yet.values())

    asyncio.run(scenario())


def test_the_nba_injuries_job_stores_the_report_for_nba_teams_only(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=load_json("espn_nba/injuries_league.json"))
            if "/basketball/nba/injuries" in request.url.path
            else httpx.Response(404)
        )
        ctx = JobContext(factory, httpx.AsyncClient(transport=transport), tmp_path)
        monkeypatch.setattr(context_jobs, "_now", lambda: T1)
        job = InjuriesJob("nba", "nba_injuries", step=6)
        assert job.name == "nba_injuries"
        assert await job.run(ctx, SLOT) == "done"
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Injury)) == 10
        # three reports a day, at 10:00, 16:00 and 22:00 Central
        evening = datetime(2026, 1, 15, 22, 30, tzinfo=CENTRAL)
        assert [s.key for s in job.slots(evening)] == [
            f"nba_injuries:2026-01-15T{hour}" for hour in ("10", "16", "22")
        ]
        assert [s.key for s in InjuriesJob().slots(evening)] == ["injuries:2026-01-15"]

    asyncio.run(scenario())
