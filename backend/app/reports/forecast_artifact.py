"""Import one forecast release asset from the private NBA data repository."""

import argparse
import asyncio
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from app.domain.forecasts import Predictions
from app.storage.database import create_engine, create_session_factory
from app.storage.forecast_repository import (
    get_forecast,
    slate_matchups,
    store_forecast,
    validate_forecast_for_slate,
)
from app.storage.trial_branch import trial_settings

PRIVATE_DATA_REPO = "isaacsilver13/nba-odds-raw"


def download_forecast(day: date) -> Predictions:
    """Use gh's existing authentication; never store a token or an unverified payload."""
    name = f"predictions-{day.isoformat()}.json"
    with tempfile.TemporaryDirectory(prefix="nba-paper-forecast-") as folder:
        result = subprocess.run(
            ["gh", "release", "download", f"state-{day.isoformat()}",
             "-R", PRIVATE_DATA_REPO, "-p", name, "-D", folder],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if result.returncode:
            raise RuntimeError(f"Private forecast asset is unavailable for {day}")
        path = Path(folder) / name
        if not path.is_file():
            raise RuntimeError(f"Private forecast asset is missing for {day}")
        forecast = Predictions.model_validate_json(path.read_text(encoding="utf-8"))
    if forecast.slate_date != day:
        raise ValueError("Private forecast asset has a different slate date")
    return forecast


async def import_forecast(day: date, now: datetime | None = None) -> bool:
    forecast = download_forecast(day)
    engine = create_engine(trial_settings())
    try:
        factory = create_session_factory(engine)
        if await get_forecast(factory, day) is not None:
            # The repository checks the canonical digest and rejects a changed replay.
            return await store_forecast(factory, forecast)
        decision_time = now or datetime.now(timezone.utc)
        matchups = await slate_matchups(factory, day)
        validate_forecast_for_slate(forecast, matchups, decision_time)
        return await store_forecast(factory, forecast)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    created = asyncio.run(import_forecast(args.date))
    print("stored" if created else "already stored")


if __name__ == "__main__":
    main()
