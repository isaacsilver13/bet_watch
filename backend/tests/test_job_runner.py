import asyncio
from datetime import datetime, timezone

import httpx
from app.jobs.runner import HEARTBEAT_PROVIDER, JobContext, run_due
from app.jobs.slots import CENTRAL, Slot, daily_slot, hourly_slots
from app.storage.models import JobSlot, ProviderRefreshRun
from sqlalchemy import select

from tests.matchup_helpers import new_session_factory

# 10:30 CT on a Sunday (CDT, UTC-5).
NOW = datetime(2026, 10, 11, 10, 30, tzinfo=CENTRAL).astimezone(timezone.utc)


class FakeJob:
    def __init__(self, outcomes, max_attempts=3, slots=None):
        self.name = "fake"
        self.max_attempts = max_attempts
        self.outcomes = list(outcomes)
        self.calls = 0
        self._slots = slots

    def slots(self, now):
        if self._slots is not None:
            return self._slots
        return [Slot("fake:now", now.replace(minute=0), now.replace(minute=59))]

    async def run(self, ctx, slot):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _ctx(tmp_path):
    factory = await new_session_factory(tmp_path)
    return JobContext(factory, httpx.AsyncClient(), tmp_path)


async def _slot(ctx, key):
    async with ctx.session_factory() as session:
        return await session.scalar(select(JobSlot).where(JobSlot.slot_key == key))


def test_hourly_slots_cover_started_hours_in_central_time() -> None:
    slots = hourly_slots("espn_scoreboard", NOW)
    assert [s.key for s in slots] == [
        "espn_scoreboard:2026-10-11T08", "espn_scoreboard:2026-10-11T09",
        "espn_scoreboard:2026-10-11T10",
    ]
    assert slots[-1].window_start == datetime(2026, 10, 11, 10, tzinfo=CENTRAL)
    assert slots[-1].window_end == datetime(2026, 10, 11, 11, tzinfo=CENTRAL)


def test_hourly_slots_before_first_hour_is_empty() -> None:
    early = datetime(2026, 10, 11, 7, 59, tzinfo=CENTRAL)
    assert hourly_slots("x", early) == []


def test_daily_slot_respects_weekday() -> None:
    assert [s.key for s in daily_slot("nfl_history", NOW)] == ["nfl_history:2026-10-11"]
    assert daily_slot("nfl_history", NOW, weekday=1) == []  # Sunday is weekday 6


def test_done_slot_runs_once(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob(["done"])
        assert await run_due([job], ctx, NOW) == {"fake:now": "done"}
        assert await run_due([job], ctx, NOW) == {}
        assert job.calls == 1

    asyncio.run(scenario())


def test_exception_retries_then_fails_at_cap(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob([RuntimeError("espn down"), RuntimeError("espn down")], max_attempts=2)
        assert await run_due([job], ctx, NOW) == {"fake:now": "pending"}
        assert await run_due([job], ctx, NOW) == {"fake:now": "failed"}
        assert await run_due([job], ctx, NOW) == {}
        slot = await _slot(ctx, "fake:now")
        assert (slot.state, slot.attempts) == ("failed", 2)
        async with ctx.session_factory() as session:
            failures = (await session.scalars(select(ProviderRefreshRun).where(
                ProviderRefreshRun.provider == "job:fake", ProviderRefreshRun.status == "failed"
            ))).all()
        assert len(failures) == 2 and failures[0].error_type == "RuntimeError"

    asyncio.run(scenario())


def test_partial_retries_until_cap(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob(["partial", "partial"], max_attempts=2)
        await run_due([job], ctx, NOW)
        assert (await _slot(ctx, "fake:now")).state == "partial"
        await run_due([job], ctx, NOW)
        assert (await _slot(ctx, "fake:now")).state == "failed"

    asyncio.run(scenario())


def test_past_window_is_missed_exactly_once_without_running(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        past = Slot("fake:08", datetime(2026, 10, 11, 8, tzinfo=CENTRAL),
                    datetime(2026, 10, 11, 9, tzinfo=CENTRAL))
        job = FakeJob([], slots=[past])
        assert await run_due([job], ctx, NOW) == {"fake:08": "missed"}
        assert await run_due([job], ctx, NOW) == {}
        assert job.calls == 0

    asyncio.run(scenario())


def test_heartbeat_recorded(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        await run_due([], ctx, NOW)
        async with ctx.session_factory() as session:
            beat = await session.scalar(select(ProviderRefreshRun).where(
                ProviderRefreshRun.provider == HEARTBEAT_PROVIDER
            ))
        assert beat is not None and beat.status == "succeeded"

    asyncio.run(scenario())
