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

from app.jobs.slots import TERMINAL_STATES, Slot
from app.storage.models import JobSlot, ProviderRefreshRun

logger = logging.getLogger(__name__)
HEARTBEAT_PROVIDER = "run_due"


@dataclass
class JobContext:
    session_factory: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    raw_dir: Path


class Job(Protocol):
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


async def _slot_state(ctx: JobContext, job: str, key: str) -> tuple[str, int]:
    async with ctx.session_factory() as session:
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
    return (row.state, row.attempts) if row else ("pending", 0)


async def _save_slot(ctx: JobContext, job: str, key: str, state: str, attempts: int) -> None:
    async with ctx.session_factory() as session, session.begin():
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
        if row is None:
            row = JobSlot(job=job, slot_key=key)
            session.add(row)
        row.state = state
        row.attempts = attempts
        row.updated_at = datetime.now(timezone.utc)


async def run_due(jobs: Sequence[Job], ctx: JobContext, now: datetime) -> dict[str, str]:
    results: dict[str, str] = {}
    for job in jobs:
        for slot in job.slots(now):
            if now < slot.window_start:
                continue
            state, attempts = await _slot_state(ctx, job.name, slot.key)
            if state in TERMINAL_STATES:
                continue
            if now >= slot.window_end:
                await _save_slot(ctx, job.name, slot.key, "missed", attempts)
                results[slot.key] = "missed"
                continue
            started_at = datetime.now(timezone.utc)
            try:
                state = await job.run(ctx, slot)
                await _log_attempt(ctx, f"job:{job.name}", started_at)
            except Exception as error:  # one job's failure must not stop the others
                logger.exception("job %s slot %s failed", job.name, slot.key)
                await _log_attempt(ctx, f"job:{job.name}", started_at, error)
                state = "pending"
            attempts += 1
            if state not in TERMINAL_STATES and attempts >= job.max_attempts:
                state = "failed"
            await _save_slot(ctx, job.name, slot.key, state, attempts)
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
