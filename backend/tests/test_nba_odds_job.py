import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from app.config import load_settings
from app.domain.models import Sport
from app.ingest.espn import parse_scoreboard
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import odds as odds_job
from app.jobs.odds import OddsSnapshotJob, nba_open_slots, nba_pre_tip_slots
from app.jobs.runner import JobContext, get_slot_info, run_due
from app.jobs.slots import CENTRAL
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, OfferObservation, ProviderRefreshRun
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

EASTERN = ZoneInfo("America/New_York")
NAME = "odds_snapshot_nba"
OPEN = f"{NAME}:nba:2026-01-15:open"
YESTERDAY = f"{NAME}:nba:2026-01-14:open"  # never ran: closed out as missed on the first pass
# Thursday 10:30 CT: inside the open window (10:00-12:00); NY at GS tips at 9pm CT tonight.
NOW = datetime(2026, 1, 15, 10, 30, tzinfo=CENTRAL).astimezone(timezone.utc)
SEEDED = datetime(2026, 1, 14, 15, tzinfo=timezone.utc)


def tip(hour, minute=0, day=15):
    return datetime(2026, 1, day, hour, minute, tzinfo=EASTERN).astimezone(timezone.utc)


def test_the_open_slot_is_a_morning_snapshot_judged_on_that_central_days_slate() -> None:
    thursday = datetime(2026, 1, 15, 10, 15, tzinfo=CENTRAL)
    today = [s for s in nba_open_slots(thursday, NAME) if "01-15" in s.key][0]
    assert today.key == OPEN
    assert today.window_start == datetime(2026, 1, 15, 10, tzinfo=CENTRAL)
    assert today.window_end - today.window_start == timedelta(hours=2)
    # Covers tonight's 9:30pm Central tip and the 12:30am Eastern tail, not tomorrow's games.
    assert today.targets == (today.window_start, datetime(2026, 1, 16, 6, tzinfo=CENTRAL))
    assert {s.key for s in nba_open_slots(thursday, NAME)} == {
        f"{NAME}:nba:2026-01-14:open", OPEN}  # yesterday's too, so a leftover is closed out


def test_a_busy_night_gets_a_pre_tip_snapshot_before_its_first_and_its_last_tip() -> None:
    tips = {tip(19), tip(19, 30), tip(20), tip(22, 30)}
    slots = nba_pre_tip_slots(tips, NAME)
    assert [s.key for s in slots] == [
        f"{NAME}:nba:2026-01-15:pre_kick:1800",  # 7:00pm Eastern is 6:00pm Central
        f"{NAME}:nba:2026-01-15:pre_kick:2130",
    ]
    early = slots[0]
    assert early.window_start == tip(19) - timedelta(minutes=30) and early.window_end == tip(19)
    assert early.targets == (tip(19), tip(19) + timedelta(minutes=1))  # games tipping then
    assert early.retry_after == timedelta(minutes=10)


def test_tips_less_than_ninety_minutes_apart_share_one_snapshot() -> None:
    quiet = nba_pre_tip_slots({tip(19), tip(20)}, NAME)
    assert [s.key for s in quiet] == [f"{NAME}:nba:2026-01-15:pre_kick:1800"]
    assert len(nba_pre_tip_slots({tip(19)}, NAME)) == 1
    assert nba_pre_tip_slots(set(), NAME) == []


def test_each_central_date_has_its_own_early_and_late_snapshot() -> None:
    slots = nba_pre_tip_slots({tip(19), tip(22), tip(19, day=16), tip(22, day=16)}, NAME)
    assert [s.key.split(":")[2] for s in slots] == ["2026-01-15"] * 2 + ["2026-01-16"] * 2


def _body() -> str:
    return json.dumps(load_json("the_odds_api/nba_jan15.json"))


async def _setup(tmp_path, monkeypatch, now=NOW):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    repo = GamesRepository(factory)
    nba_games = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
    nfl_games = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
    await repo.upsert_games("nba", "espn", nba_games, SEEDED)
    await repo.upsert_games("nfl", "espn", nfl_games, SEEDED)
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, text=_body(), headers={"x-requests-remaining": "450"})

    client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))
    ctx = JobContext(factory, httpx.AsyncClient(), tmp_path / "raw",
                     settings=load_settings(), odds=client)
    monkeypatch.setattr(odds_job, "_now", lambda: now)
    return ctx, calls


def _job() -> OddsSnapshotJob:
    return OddsSnapshotJob(Sport.NBA, NAME)


def test_the_nba_morning_snapshot_is_stored_and_linked_to_the_nba_game(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx, calls = await _setup(tmp_path, monkeypatch)
        assert await run_due([_job()], ctx, NOW) == {YESTERDAY: "missed", OPEN: "done"}
        assert len(calls) == 1 and "basketball_nba" in calls[0].path
        info = await get_slot_info(ctx, NAME, OPEN)
        assert info.coverage["targeted"] == 1 and info.coverage["complete"] == 1
        async with ctx.session_factory() as session:
            assert await session.scalar(select(func.count(OfferObservation.id))) == 30
            game = await session.scalar(select(Game).where(Game.espn_id == "401810439"))
            nfl = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
        assert game.event_id and game.event_id.startswith("event:")
        assert nfl.event_id is None  # the NFL game is untouched
        assert await run_due([_job()], ctx, NOW + timedelta(minutes=15)) == {}
        assert len(calls) == 1  # a terminal slot is never paid for twice

    asyncio.run(scenario())


def test_the_two_sports_draw_on_one_credit_pool(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, calls = await _setup(tmp_path, monkeypatch)
        async with ctx.session_factory() as session, session.begin():
            session.add(ProviderRefreshRun(
                provider="the_odds_api", sport="nfl", status="succeeded", started_at=SEEDED,
                completed_at=SEEDED, quota_remaining=load_settings().provider_min_quota_remaining))
        results = await run_due([_job()], ctx, NOW)
        assert results == {YESTERDAY: "missed", OPEN: "skipped_quota"}  # NFL spent the credits
        assert calls == []

    asyncio.run(scenario())


def test_each_job_plans_only_its_own_sports_games(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        evening = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        ctx, _ = await _setup(tmp_path, monkeypatch, now=evening)
        nba = {s.key for s in await _job().plan_slots(ctx, evening)}
        assert f"{NAME}:nba:2026-01-15:pre_kick:2100" in nba  # NY at GS, 9pm Central
        nfl = {s.key for s in await OddsSnapshotJob().plan_slots(ctx, evening)}
        assert not [key for key in nfl if "pre_kick" in key]  # no NFL game near this night
        assert all(key.startswith("odds_snapshot:nfl:") for key in nfl)

    asyncio.run(scenario())


def test_the_nba_job_is_registered_next_to_the_nfl_one() -> None:
    from app.jobs.__main__ import JOBS

    names = {job.name for job in JOBS}
    assert {"odds_snapshot", "odds_snapshot_nba"} <= names
