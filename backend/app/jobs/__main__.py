"""python -m app.jobs run-due | <job name>  (Task Scheduler runs `run-due` every 15 min)."""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import load_settings
from app.jobs.context import CONTEXT_JOBS
from app.jobs.nfl import JOBS as NFL_JOBS
from app.jobs.odds import OddsSnapshotJob
from app.jobs.runner import JobContext, run_due, run_once
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_engine, create_session_factory

JOBS = [*NFL_JOBS, *CONTEXT_JOBS, OddsSnapshotJob()]


async def main(argv: Optional[list[str]] = None) -> int:
    by_name = {job.name: job for job in JOBS}
    parser = argparse.ArgumentParser(prog="python -m app.jobs")
    parser.add_argument("command", choices=["run-due", *by_name])
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(
            timeout=settings.provider_timeout_seconds,
            follow_redirects=True,
        ) as http:
            odds = TheOddsApiClient.from_settings(settings)  # None unless live with a key
            ctx = JobContext(
                create_session_factory(engine), http, Path(settings.raw_data_dir), settings, odds
            )
            if args.command == "run-due":
                for key, state in (await run_due(JOBS, ctx, datetime.now(timezone.utc))).items():
                    print(f"{state:>13}  {key}")
            else:
                print(await run_once(by_name[args.command], ctx))
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
