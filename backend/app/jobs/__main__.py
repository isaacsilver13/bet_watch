"""python -m app.jobs run-due | <job name>  (in production cron-job.org ticks the same run-due
every 15 min through the Fly app's POST /internal/run-due; see app/api/internal.py)."""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import Settings, load_settings
from app.domain.models import Sport
from app.jobs.bets import GradeBetsJob
from app.jobs.context import CONTEXT_JOBS
from app.jobs.model_picks import ModelPicksJob
from app.jobs.nba import NBA_JOBS
from app.jobs.nfl import JOBS as NFL_JOBS
from app.jobs.odds import OddsSnapshotJob
from app.jobs.paper_ledger import PaperLedgerJob, PaperSettlementJob
from app.jobs.runner import JobContext, run_due, run_once
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_engine, create_session_factory

JOBS = [
    *NFL_JOBS,
    *CONTEXT_JOBS,
    *NBA_JOBS,
    OddsSnapshotJob(),
    OddsSnapshotJob(Sport.NBA, "odds_snapshot_nba"),
    ModelPicksJob(),  # after the NBA snapshot, so a tick that finishes it can price it at once
    PaperLedgerJob(),  # freezes today's report before tip; no provider calls
    GradeBetsJob(),
    PaperSettlementJob(),  # independent of personal-bet and CLV grading failures
]


@asynccontextmanager
async def job_context(settings: Settings) -> AsyncIterator[JobContext]:
    """What every run needs: a database, an HTTP client and the Odds API client. Shared by the
    CLI and the Fly deployment's POST /internal/run-due."""
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(
            timeout=settings.provider_timeout_seconds,
            follow_redirects=True,
        ) as http:
            odds = TheOddsApiClient.from_settings(settings)  # None unless live with a key
            yield JobContext(
                create_session_factory(engine), http, Path(settings.raw_data_dir), settings, odds
            )
    finally:
        await engine.dispose()


async def main(argv: Optional[list[str]] = None) -> int:
    by_name = {job.name: job for job in JOBS}
    parser = argparse.ArgumentParser(prog="python -m app.jobs")
    parser.add_argument("command", choices=["run-due", *by_name])
    args = parser.parse_args(argv)

    async with job_context(load_settings()) as ctx:
        if args.command == "run-due":
            results = await run_due(JOBS, ctx, datetime.now(timezone.utc))
            for key, state in results.items():
                print(f"{state:>13}  {key}")
            if results.had_failures:
                return 1
        else:
            print(await run_once(by_name[args.command], ctx))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
