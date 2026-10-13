"""Take one paid Odds API snapshot: save the raw response first, then parse, store and link it."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.coverage import snapshot_coverage
from app.domain.models import Sport
from app.identity import canonicalize_comparison
from app.ingest.odds_raw import observed_at_from_path, read_raw, write_raw
from app.providers.the_odds_api.adapter import TheOddsApiProvider
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.games_repository import GamesRepository
from app.storage.repository import OddsRepository

PROVIDER = "the_odds_api"
SPORT = Sport.NFL


@dataclass(frozen=True)
class SnapshotResult:
    state: str  # done | partial | empty
    coverage: dict


async def fetch_snapshot(
    client: TheOddsApiClient, raw_dir: Path, slot_key: str, now: datetime
) -> tuple[Path, Optional[int]]:
    """One paid call. The body is on disk before anything reads it; returns (file, credits left).

    The time is cut to whole seconds because it becomes part of the file name, and a re-parse
    must stamp rows with exactly the time the first parse did."""
    body = await client.fetch_odds_raw(SPORT)
    path = write_raw(raw_dir, PROVIDER, slot_key, now.replace(microsecond=0), body)
    return path, client.quota_remaining


async def ingest_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    path: Path,
    targets: tuple[datetime, datetime],
    quota_remaining: Optional[int],
) -> SnapshotResult:
    """Parse a saved response, store it (once), link events to games and score the coverage.

    `targets` is the kickoff window of the games this snapshot was for. Safe to repeat: the
    store step does nothing when the snapshot is already saved."""
    observed_at = observed_at_from_path(path)
    comparisons = [
        canonicalize_comparison(comparison)
        for comparison in TheOddsApiProvider.normalize_events(
            json.loads(read_raw(path)), SPORT, observed_at
        )
    ]
    await OddsRepository(session_factory).record_snapshot(
        PROVIDER, SPORT, comparisons, observed_at, quota_remaining
    )
    games = GamesRepository(session_factory)
    await games.link_events(SPORT.value)  # before scoring, so a linked game can be matched
    scheduled = await games.scheduled_games(SPORT.value, *targets)
    coverage = snapshot_coverage(comparisons, [(g.id, g.event_id) for g in scheduled])
    if not scheduled:
        return SnapshotResult("empty", coverage)
    return SnapshotResult("done" if not coverage["missing"] else "partial", coverage)
