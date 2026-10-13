"""Run each job's due slots once. Attempts are logged; every slot has one current state."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.jobs.slots import TERMINAL_STATES, Slot
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.models import JobSlot, ProviderRefreshRun

logger = logging.getLogger(__name__)
HEARTBEAT_PROVIDER = "run_due"


@dataclass
class JobContext:
    session_factory: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    raw_dir: Path
    settings: Optional[Settings] = None
    odds: Optional[TheOddsApiClient] = None  # only built in live mode with a key


@dataclass(frozen=True)
class SlotInfo:
    state: str
    attempts: int
    raw_path: Optional[str]
    coverage: Optional[dict]


class Job(Protocol):
    """`slots(now)` is enough for most jobs. One that needs the database to know its slots
    defines `async plan_slots(ctx, now)` as well, and that is used instead."""

    name: str
    max_attempts: int

    def slots(self, now: datetime) -> list[Slot]: ...

    async def run(self, ctx: JobContext, slot: Slot) -> str: ...


async def _log_attempt(
    ctx: JobContext, provider: str, started_at: datetime, error: Optional[BaseException] = None
) -> None:
    async with ctx.session_factory() as session, session.begin():
        session.add(
            ProviderRefreshRun(
                provider=provider,
                status="failed" if error else "succeeded",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                error_type=type(error).__name__ if error else None,
                error_message=str(error)[:500] if error else None,
            )
        )


async def get_slot_info(ctx: JobContext, job: str, key: str) -> SlotInfo:
    async with ctx.session_factory() as session:
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
    if row is None:
        return SlotInfo("pending", 0, None, None)
    return SlotInfo(row.state, row.attempts, row.raw_path, row.coverage)


async def update_slot(
    ctx: JobContext,
    job: str,
    key: str,
    *,
    state: Optional[str] = None,
    attempts: Optional[int] = None,
    raw_path: Optional[str] = None,
    coverage: Optional[dict] = None,
    at: Optional[datetime] = None,
) -> None:
    """Change only the fields given; a job uses this to record the raw file it just paid for.
    `at` is the run's clock (the real time unless a test is driving a simulated one)."""
    async with ctx.session_factory() as session, session.begin():
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
        if row is None:
            row = JobSlot(job=job, slot_key=key, state="pending", attempts=0)
            session.add(row)
        if state is not None:
            row.state = state
        if attempts is not None:
            row.attempts = attempts
        if raw_path is not None:
            row.raw_path = raw_path
        if coverage is not None:
            row.coverage = coverage
        row.updated_at = at or datetime.now(timezone.utc)


async def _planned_slots(job: Job, ctx: JobContext, now: datetime) -> list[Slot]:
    planner = getattr(job, "plan_slots", None)
    return await planner(ctx, now) if planner else job.slots(now)


async def run_due(jobs: Sequence[Job], ctx: JobContext, now: datetime) -> dict[str, str]:
    results: dict[str, str] = {}
    for job in jobs:
        for slot in await _planned_slots(job, ctx, now):
            if now < slot.window_start:
                continue
            info = await get_slot_info(ctx, job.name, slot.key)
            state, attempts = info.state, info.attempts
            if state in TERMINAL_STATES:
                continue
            # A raw_saved slot is a response we already paid for: it runs again after its
            # window so the job can parse it, rather than being written off as missed.
            if now >= slot.window_end and state != "raw_saved":
                await update_slot(ctx, job.name, slot.key, state="missed", at=now)
                results[slot.key] = "missed"
                continue
            started_at = datetime.now(timezone.utc)
            try:
                state = await job.run(ctx, slot)
                await _log_attempt(ctx, f"job:{job.name}", started_at)
            except Exception as error:  # one job's failure must not stop the others
                logger.exception("job %s slot %s failed", job.name, slot.key)
                await _log_attempt(ctx, f"job:{job.name}", started_at, error)
                # The job may have saved a paid response before it crashed; keep that state.
                saved = await get_slot_info(ctx, job.name, slot.key)
                state = "raw_saved" if saved.state == "raw_saved" else "pending"
            attempts += 1
            # Never fail a raw_saved slot: its response is paid for and only needs parsing.
            if state not in TERMINAL_STATES | {"raw_saved"} and attempts >= job.max_attempts:
                state = "failed"
            await update_slot(ctx, job.name, slot.key, state=state, attempts=attempts, at=now)
            results[slot.key] = state
    await _log_attempt(ctx, HEARTBEAT_PROVIDER, now)
    return results


async def run_once(job: Job, ctx: JobContext) -> str:
    """Manual run (python -m app.jobs <name>): no slot bookkeeping, attempt still logged."""
    started_at = datetime.now(timezone.utc)
    slot = Slot(f"{job.name}:manual:{started_at.isoformat()}", started_at, started_at)
    try:
        state = await job.run(ctx, slot)
    except Exception as error:
        await _log_attempt(ctx, f"job:{job.name}", started_at, error)
        raise
    await _log_attempt(ctx, f"job:{job.name}", started_at)
    return state
