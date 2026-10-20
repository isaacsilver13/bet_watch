"""python -m app.jobs check-due | run-due | <job name>."""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import ProviderMode, load_settings
from app.jobs.registry import JOBS
from app.jobs.runner import JobContext, check_due, run_due, run_once
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_engine, create_session_factory


def run_due_exit_code(results: dict[str, str]) -> int:
    return 1 if "failed" in results.values() else 0


async def main(argv: Optional[list[str]] = None) -> int:
    by_name = {job.name: job for job in JOBS}
    parser = argparse.ArgumentParser(prog="python -m app.jobs")
    parser.add_argument("command", choices=["check-due", "run-due", *by_name])
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
                create_session_factory(engine),
                http,
                Path(settings.raw_data_dir),
                settings,
                odds,
                odds_planning_enabled=(
                    args.command == "check-due"
                    and settings.provider_mode is ProviderMode.LIVE
                ),
            )
            if args.command == "check-due":
                due = await check_due(JOBS, ctx, datetime.now(timezone.utc))
                for key in due:
                    print(key)
                return 0 if due else 10
            elif args.command == "run-due":
                results = await run_due(JOBS, ctx, datetime.now(timezone.utc))
                for key, state in results.items():
                    print(f"{state:>13}  {key}")
                return run_due_exit_code(results)
            else:
                print(await run_once(by_name[args.command], ctx))
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
