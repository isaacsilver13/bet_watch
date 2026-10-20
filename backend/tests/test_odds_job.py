import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.config import load_settings
from app.ingest.espn import parse_scoreboard
from app.ingest.odds_raw import write_raw
from app.ingest.seed import seed_teams
from app.jobs import odds as odds_job
from app.jobs.odds import OddsSnapshotJob, daily_odds_slots, pre_kick_slots
from app.jobs.runner import JobContext, get_slot_info, run_due, run_once, update_slot
from app.jobs.slots import CENTRAL
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, JobSlot, OfferObservation, ProviderRefreshRun
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

SLOT = "odds_snapshot:nfl:2026-10-15:thu"
# Thursday 10:30 CT: inside the Thursday window (10:00-12:00), nine hours before DEN-SEA kicks off.
NOW = datetime(2026, 10, 15, 10, 30, tzinfo=CENTRAL).astimezone(timezone.utc)
SEEDED = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


def _body(keep_books: int = 5) -> str:
    payload = load_json("the_odds_api/nfl_week6.json")
    payload[0]["bookmakers"] = payload[0]["bookmakers"][:keep_books]
    return json.dumps(payload)


class Setup:
    def __init__(self, ctx: JobContext, calls: list) -> None:
        self.ctx = ctx
        self.calls = calls


async def _setup(tmp_path, monkeypatch, body=None, games=True, live=True, now=NOW) -> Setup:
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    if games:
        records = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
        await GamesRepository(factory).upsert_games("nfl", "espn", records, SEEDED)
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(
            200, text=body or _body(), headers={"x-requests-remaining": "450"}
        )

    client = TheOddsApiClient("test-key", transport=httpx.MockTransport(handler))
    ctx = JobContext(
        factory, httpx.AsyncClient(), tmp_path / "raw",
        settings=load_settings(), odds=client if live else None,
    )
    monkeypatch.setattr(odds_job, "_now", lambda: now)
    return Setup(ctx, calls)


async def _observations(ctx) -> int:
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count(OfferObservation.id)))


def test_daily_slots_follow_the_weekly_rhythm_in_central_time() -> None:
    def keys(day, hour=10, minute=15):
        now = datetime(2026, 10, day, hour, minute, tzinfo=CENTRAL)
        return [s.key for s in daily_odds_slots(now)]

    assert keys(13) == ["odds_snapshot:nfl:2026-10-13:open"]  # Tuesday: the opening lines
    assert keys(14) == ["odds_snapshot:nfl:2026-10-13:open"]  # Wednesday closes out Tuesday
    assert keys(12) == ["odds_snapshot:nfl:2026-10-11:sun"]  # Monday closes out Sunday
    assert keys(15) == ["odds_snapshot:nfl:2026-10-15:thu"]
    assert keys(16, 9) == ["odds_snapshot:nfl:2026-10-15:thu"]  # Friday still closes out Thursday
    assert keys(18) == ["odds_snapshot:nfl:2026-10-17:sat", "odds_snapshot:nfl:2026-10-18:sun"]
    sunday = daily_odds_slots(datetime(2026, 10, 18, 10, 15, tzinfo=CENTRAL))[-1]
    assert sunday.window_end - sunday.window_start == timedelta(minutes=90)
    # Judged on its own week's games only: Sunday's snapshot ends at Tuesday 00:00, because the
    # following Thursday's lines are not posted yet.
    assert sunday.targets == (sunday.window_start, datetime(2026, 10, 20, tzinfo=CENTRAL))
    tuesday = daily_odds_slots(datetime(2026, 10, 13, 10, 15, tzinfo=CENTRAL))[-1]
    assert tuesday.targets[1] == datetime(2026, 10, 20, tzinfo=CENTRAL)  # a full week ahead
    after_dst = daily_odds_slots(datetime(2026, 11, 1, 10, 15, tzinfo=CENTRAL))[-1]
    assert after_dst.window_start.astimezone(timezone.utc).hour == 16  # 10:00 CST is 16:00Z


def test_there_is_one_pre_kick_slot_per_distinct_kickoff() -> None:
    thursday = datetime(2026, 10, 15, 19, 15, tzinfo=CENTRAL)
    sunday = datetime(2026, 10, 18, 12, 0, tzinfo=CENTRAL)
    slots = pre_kick_slots({sunday, thursday, sunday})
    assert [s.key for s in slots] == [
        "odds_snapshot:nfl:2026-10-15:pre_kick:1915",
        "odds_snapshot:nfl:2026-10-18:pre_kick:1200",
    ]
    assert slots[0].window_start == thursday - timedelta(minutes=30)
    assert slots[0].window_end == thursday
    assert slots[0].targets == (thursday, thursday + timedelta(minutes=1))


def test_a_complete_snapshot_is_saved_linked_and_done_and_not_repeated(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch)
        assert await run_due([OddsSnapshotJob()], s.ctx, NOW) == {SLOT: "done"}
        assert len(s.calls) == 1
        assert await _observations(s.ctx) == 30
        info = await get_slot_info(s.ctx, "odds_snapshot", SLOT)
        assert (info.state, info.attempts) == ("done", 1)
        assert info.coverage["targeted"] == 1 and info.coverage["complete"] == 1
        assert info.raw_path and (tmp_path / "raw").exists()
        async with s.ctx.session_factory() as session:
            game = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
            runs = (await session.scalars(select(ProviderRefreshRun).where(
                ProviderRefreshRun.provider == "the_odds_api"))).all()
        assert game.event_id and game.event_id.startswith("event:")
        assert [r.quota_remaining for r in runs] == [450]
        assert await run_due([OddsSnapshotJob()], s.ctx, NOW + timedelta(minutes=15)) == {}
        assert len(s.calls) == 1  # a terminal slot is never paid for twice

    asyncio.run(scenario())


def test_a_crash_after_the_raw_write_parses_the_file_without_another_call(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch)
        real = odds_job.ingest_snapshot
        crashed = []

        async def crash_once(*args, **kwargs):
            if not crashed:
                crashed.append(True)
                raise RuntimeError("process killed after the paid response was saved")
            return await real(*args, **kwargs)

        monkeypatch.setattr(odds_job, "ingest_snapshot", crash_once)
        job = OddsSnapshotJob()
        assert await run_due([job], s.ctx, NOW) == {SLOT: "raw_saved"}
        assert len(s.calls) == 1 and await _observations(s.ctx) == 0
        # Even after the window has closed, the saved response is parsed instead of written off.
        later = NOW + timedelta(hours=3)
        assert await run_due([job], s.ctx, later) == {SLOT: "done"}
        assert len(s.calls) == 1 and await _observations(s.ctx) == 30

    asyncio.run(scenario())


def test_a_crash_between_the_insert_and_the_slot_update_does_not_duplicate_rows(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch)
        real = odds_job.update_slot
        killed = []

        async def kill_before_coverage(ctx, job, key, **fields):
            if "coverage" in fields and not killed:
                killed.append(True)
                raise RuntimeError("process killed after the rows were committed")
            return await real(ctx, job, key, **fields)

        monkeypatch.setattr(odds_job, "update_slot", kill_before_coverage)
        job = OddsSnapshotJob()
        assert await run_due([job], s.ctx, NOW) == {SLOT: "raw_saved"}
        assert await _observations(s.ctx) == 30
        assert await run_due([job], s.ctx, NOW + timedelta(minutes=15)) == {SLOT: "done"}
        assert await _observations(s.ctx) == 30 and len(s.calls) == 1

    asyncio.run(scenario())


def test_a_thin_snapshot_is_partial_then_retried_after_the_gap_then_gives_up(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch, body=_body(keep_books=2))
        job = OddsSnapshotJob()
        assert await run_due([job], s.ctx, NOW) == {SLOT: "partial"}
        info = await get_slot_info(s.ctx, "odds_snapshot", SLOT)
        assert info.coverage["missing"][0]["books"] == {"moneyline": 2, "spread": 2, "total": 2}
        # Ten minutes later the books have had no time to post more: no second payment yet.
        assert await run_due([job], s.ctx, NOW + timedelta(minutes=10)) == {}
        assert len(s.calls) == 1
        assert await run_due([job], s.ctx, NOW + timedelta(minutes=61)) == {SLOT: "failed"}
        assert len(s.calls) == 2  # the attempt cap is two
        assert await run_due([job], s.ctx, NOW + timedelta(minutes=80)) == {}

    asyncio.run(scenario())


def test_the_quota_floor_skips_the_slot_without_a_call(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch)
        async with s.ctx.session_factory() as session, session.begin():
            session.add(ProviderRefreshRun(
                provider="the_odds_api", sport="nfl", status="succeeded", started_at=SEEDED,
                completed_at=SEEDED, quota_remaining=load_settings().provider_min_quota_remaining,
            ))
        assert await run_due([OddsSnapshotJob()], s.ctx, NOW) == {SLOT: "skipped_quota"}
        assert s.calls == []

    asyncio.run(scenario())


def test_no_games_to_cover_means_no_paid_call(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch, games=False)
        assert await run_due([OddsSnapshotJob()], s.ctx, NOW) == {SLOT: "empty"}
        assert s.calls == []

    asyncio.run(scenario())


def test_the_job_does_nothing_in_fixture_mode(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch, live=False)
        assert await run_due([OddsSnapshotJob()], s.ctx, NOW) == {}
        assert s.calls == []

    asyncio.run(scenario())


def test_preflight_plans_paid_slots_without_a_provider_key_or_call(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch, live=False)
        s.ctx.odds_planning_enabled = True

        slots = await OddsSnapshotJob().plan_slots(s.ctx, NOW)

        assert SLOT in {slot.key for slot in slots}
        assert s.calls == []

    asyncio.run(scenario())


def test_running_it_by_hand_without_a_key_says_why_instead_of_spending(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        s = await _setup(tmp_path, monkeypatch, live=False)
        with pytest.raises(RuntimeError, match="PROVIDER_MODE=live"):
            await run_once(OddsSnapshotJob(), s.ctx)
        assert s.calls == []

    asyncio.run(scenario())


def test_a_pre_kick_snapshot_runs_in_its_window_and_covers_that_kickoff(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        kickoff = datetime(2026, 10, 15, 19, 15, tzinfo=CENTRAL)
        now = kickoff - timedelta(minutes=20)
        s = await _setup(tmp_path, monkeypatch, now=now)
        results = await run_due([OddsSnapshotJob()], s.ctx, now)
        assert results == {
            SLOT: "missed",  # the Thursday morning window passed without a run
            "odds_snapshot:nfl:2026-10-15:pre_kick:1915": "done",
        }
        assert len(s.calls) == 1

    asyncio.run(scenario())


def test_an_unparsed_response_is_found_even_when_its_slot_is_no_longer_planned(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        later = NOW + timedelta(days=2)  # Saturday: Thursday's slots are long gone
        s = await _setup(tmp_path, monkeypatch, now=later)
        saved = NOW + timedelta(minutes=1)
        path = write_raw(tmp_path / "raw", "the_odds_api", "odds_snapshot:nfl:old", saved, _body())
        await update_slot(s.ctx, "odds_snapshot", "odds_snapshot:nfl:old",
                          state="raw_saved", raw_path=str(path))
        async with s.ctx.session_factory() as session, session.begin():
            row = await session.scalar(select(JobSlot).where(JobSlot.slot_key.like("%old")))
            row.updated_at = saved
        results = await run_due([OddsSnapshotJob()], s.ctx, later)
        assert results["odds_snapshot:nfl:old"] in {"done", "empty"}
        assert await _observations(s.ctx) == 30 and s.calls == []

    asyncio.run(scenario())


def test_the_job_is_registered_with_the_runner() -> None:
    from app.jobs.__main__ import JOBS

    assert any(isinstance(job, OddsSnapshotJob) for job in JOBS)
