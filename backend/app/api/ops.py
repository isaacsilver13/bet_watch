"""Is the machine doing its job? The numbers behind the go/no-go check before real bets."""

from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.slate import get_now, get_session_factory
from app.ingest.odds_snapshot import PROVIDER
from app.jobs.runner import HEARTBEAT_PROVIDER
from app.jobs.slots import CENTRAL
from app.storage.games_repository import (
    MAX_EVENT_GAP,
    GamesRepository,
    alias_map,
    aware,
    games_for_event,
)
from app.storage.models import EventRecord, Game, JobSlot, ProviderRefreshRun
from app.storage.repository import OddsRepository

router = APIRouter(prefix="/api/v1")
HEARTBEAT_STALE_AFTER = timedelta(minutes=45)  # Task Scheduler fires every 15; three misses
LOOKBACK = timedelta(days=7)
SPORTS = ("nfl", "nba")
EVENT_HORIZON = timedelta(days=10)  # the schedule is only loaded a week or two ahead


class JobStatus(BaseModel):
    job: str
    slot_key: str
    state: str
    attempts: int
    updated_at: datetime


class OpsHealth(BaseModel):
    generated_at: datetime
    run_due_last_at: Optional[datetime]
    run_due_stale: bool  # no run-due heartbeat lately: Task Scheduler is not firing
    jobs: list[JobStatus]  # the latest slot of each job
    slots_today: dict[str, int]  # Central-time calendar day, by state
    credits_remaining: Optional[int]
    snapshot_coverage: Optional[dict]  # books x markets of the latest odds_snapshot
    unlinked_events: list[str]
    stuck_raw_saved: list[str]  # paid responses saved but not yet parsed
    finals_missing_close: int


def _start_of_day(now: datetime) -> datetime:
    day: date = now.astimezone(CENTRAL).date()
    return datetime.combine(day, time(0), CENTRAL)


async def unlinked_events(
    session_factory: async_sessionmaker[AsyncSession], sport: str, since: datetime, until: datetime
) -> list[str]:
    """Odds events starting in the window that no game has claimed (read-only)."""
    async with session_factory() as session:
        aliases = await alias_map(session, sport)
        linked = select(Game.event_id).where(Game.event_id.is_not(None))
        events = (
            await session.scalars(
                select(EventRecord)
                .where(
                    EventRecord.sport == sport,
                    EventRecord.start_time >= since,
                    EventRecord.start_time <= until,
                    EventRecord.id.not_in(linked),
                )
                .order_by(EventRecord.start_time, EventRecord.id)
            )
        ).all()
        unlinked = []
        for event in events:
            matches = await games_for_event(session, aliases, sport, event, MAX_EVENT_GAP)
            # A game that has its own event already has retired this one.
            if not (len(matches) == 1 and matches[0].event_id is not None):
                unlinked.append(event.id)
        return unlinked


async def _latest_slots(session: AsyncSession, now: datetime) -> list[JobSlot]:
    rows = (
        await session.scalars(
            select(JobSlot)
            .where(JobSlot.updated_at >= now - LOOKBACK)
            .order_by(JobSlot.updated_at.desc(), JobSlot.id.desc())
        )
    ).all()
    latest: dict[str, JobSlot] = {}
    for row in rows:
        latest.setdefault(row.job, row)
    return sorted(latest.values(), key=lambda row: row.job)


@router.get("/ops/health", response_model=OpsHealth)
async def ops_health(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> OpsHealth:
    games = GamesRepository(session_factory)
    async with session_factory() as session:
        beat = await session.scalar(
            select(ProviderRefreshRun.completed_at)
            .where(ProviderRefreshRun.provider == HEARTBEAT_PROVIDER)
            .order_by(ProviderRefreshRun.completed_at.desc())
            .limit(1)
        )
        latest = await _latest_slots(session, now)
        today = (
            await session.scalars(
                select(JobSlot.state).where(JobSlot.updated_at >= _start_of_day(now))
            )
        ).all()
        stuck = (
            await session.scalars(select(JobSlot.slot_key).where(JobSlot.state == "raw_saved"))
        ).all()
        snapshots = (
            await session.scalars(
                select(JobSlot)
                .where(JobSlot.job == "odds_snapshot", JobSlot.coverage.is_not(None))
                .order_by(JobSlot.updated_at.desc())
                .limit(1)
            )
        ).all()
    unlinked: list[str] = []
    missing_closes = 0
    for sport in SPORTS:
        unlinked.extend(
            await unlinked_events(
                session_factory, sport, now - timedelta(hours=12), now + EVENT_HORIZON
            )
        )
        missing_closes += len(
            await games.finals_missing_close(sport, since=now - timedelta(days=14))
        )
    return OpsHealth(
        generated_at=now,
        run_due_last_at=aware(beat) if beat else None,
        run_due_stale=beat is None or now - aware(beat) > HEARTBEAT_STALE_AFTER,
        jobs=[
            JobStatus(
                job=row.job,
                slot_key=row.slot_key,
                state=row.state,
                attempts=row.attempts,
                updated_at=aware(row.updated_at),
            )
            for row in latest
        ],
        slots_today=dict(Counter(today)),
        credits_remaining=await OddsRepository(session_factory).latest_quota(PROVIDER),
        snapshot_coverage=snapshots[0].coverage if snapshots else None,
        unlinked_events=unlinked,
        stuck_raw_saved=sorted(stuck),
        finals_missing_close=missing_closes,
    )
