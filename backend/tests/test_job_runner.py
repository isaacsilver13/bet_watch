import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from app.jobs.runner import (
    HEARTBEAT_PROVIDER,
    JobContext,
    SlotInfo,
    check_due,
    due_slot_keys,
    get_slot_info,
    run_due,
    run_once,
    update_slot,
)
from app.jobs.slots import CENTRAL, Slot, daily_slot, hourly_slots
from app.storage.models import JobSlot, ProviderRefreshRun
from sqlalchemy import func, select

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
        "espn_scoreboard:2026-10-11T08",
        "espn_scoreboard:2026-10-11T09",
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


def test_read_only_preflight_finds_only_nonterminal_started_slots(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        current = Slot(
            "fake:current",
            NOW.replace(minute=0),
            NOW.replace(minute=59),
        )
        future = Slot(
            "fake:future",
            NOW.replace(minute=0) + timedelta(hours=1),
            NOW.replace(minute=59) + timedelta(hours=1),
        )
        job = FakeJob([], slots=[current, future])

        assert await due_slot_keys([job], ctx, NOW) == ["fake:current"]
        assert job.calls == 0

        await update_slot(ctx, "fake", "fake:current", state="done", at=NOW)
        assert await due_slot_keys([job], ctx, NOW) == []
        assert job.calls == 0

    asyncio.run(scenario())


def test_read_only_preflight_wakes_once_to_close_an_expired_slot(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        past = Slot(
            "fake:08",
            datetime(2026, 10, 11, 8, tzinfo=CENTRAL),
            datetime(2026, 10, 11, 9, tzinfo=CENTRAL),
        )
        job = FakeJob([], slots=[past])

        assert await due_slot_keys([job], ctx, NOW) == ["fake:08"]
        assert await run_due([job], ctx, NOW) == {"fake:08": "missed"}
        assert await due_slot_keys([job], ctx, NOW) == []

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
            failures = (
                await session.scalars(
                    select(ProviderRefreshRun).where(
                        ProviderRefreshRun.provider == "job:fake",
                        ProviderRefreshRun.status == "failed",
                    )
                )
            ).all()
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
        past = Slot(
            "fake:08",
            datetime(2026, 10, 11, 8, tzinfo=CENTRAL),
            datetime(2026, 10, 11, 9, tzinfo=CENTRAL),
        )
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
            beat = await session.scalar(
                select(ProviderRefreshRun).where(ProviderRefreshRun.provider == HEARTBEAT_PROVIDER)
            )
        assert beat is not None and beat.status == "succeeded"

    asyncio.run(scenario())


def test_preflight_records_a_heartbeat_even_when_nothing_is_due(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        assert await check_due([], ctx, NOW) == []
        async with ctx.session_factory() as session:
            beat = await session.scalar(
                select(ProviderRefreshRun).where(ProviderRefreshRun.provider == HEARTBEAT_PROVIDER)
            )
        assert beat is not None and beat.status == "succeeded"

    asyncio.run(scenario())


def test_overlapping_local_run_due_call_does_not_run_or_record_a_heartbeat(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingJob(FakeJob):
            async def run(self, ctx, slot):
                self.calls += 1
                started.set()
                await release.wait()
                return "done"

        job = BlockingJob([])
        first = asyncio.create_task(run_due([job], ctx, NOW))
        await started.wait()
        assert await run_due([job], ctx, NOW) == {}
        assert await run_once(job, ctx) == "busy"
        release.set()
        assert await first == {"fake:now": "done"}
        assert job.calls == 1
        async with ctx.session_factory() as session:
            heartbeats = await session.scalar(
                select(func.count())
                .select_from(ProviderRefreshRun)
                .where(ProviderRefreshRun.provider == HEARTBEAT_PROVIDER)
            )
        assert heartbeats == 1

    asyncio.run(scenario())


class PlannedJob(FakeJob):
    """A job whose slots need the database, so it plans them asynchronously."""

    def slots(self, now):
        raise AssertionError("plan_slots must be used instead")

    async def plan_slots(self, ctx, now):
        return [Slot("fake:planned", now.replace(minute=0), now.replace(minute=59))]


def test_plan_slots_replaces_slots(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = PlannedJob(["done"])
        assert await run_due([job], ctx, NOW) == {"fake:planned": "done"}

    asyncio.run(scenario())


def test_slot_extras_survive_the_state_save(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)

        class Saves(FakeJob):
            async def run(self, ctx, slot):
                await update_slot(ctx, "fake", slot.key, state="raw_saved", raw_path="a/b.gz")
                return "partial"

        await run_due([Saves([])], ctx, NOW)
        info = await get_slot_info(ctx, "fake", "fake:now")
        assert (info.state, info.attempts, info.raw_path) == ("partial", 1, "a/b.gz")
        assert (await get_slot_info(ctx, "fake", "nope")) == SlotInfo("pending", 0, None, None)

    asyncio.run(scenario())


def test_raw_saved_slot_still_runs_after_its_window_closes(tmp_path) -> None:
    """We already paid for that response; the job must get to parse it, not mark it missed."""

    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        past = Slot(
            "fake:08",
            datetime(2026, 10, 11, 8, tzinfo=CENTRAL),
            datetime(2026, 10, 11, 9, tzinfo=CENTRAL),
        )
        await update_slot(ctx, "fake", "fake:08", state="raw_saved", raw_path="x.gz")
        job = FakeJob(["done"], slots=[past])
        assert await run_due([job], ctx, NOW) == {"fake:08": "done"}
        assert job.calls == 1

    asyncio.run(scenario())


def test_a_failed_parse_leaves_the_paid_response_raw_saved_past_the_attempt_cap(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)

        class ParseCrash(FakeJob):
            async def run(self, ctx, slot):
                await update_slot(ctx, "fake", slot.key, state="raw_saved", raw_path="a/b.gz")
                raise ValueError("bad payload")

        job = ParseCrash([], max_attempts=1)
        assert await run_due([job], ctx, NOW) == {"fake:now": "raw_saved"}
        info = await get_slot_info(ctx, "fake", "fake:now")
        assert (info.state, info.attempts, info.raw_path) == ("raw_saved", 1, "a/b.gz")

    asyncio.run(scenario())
