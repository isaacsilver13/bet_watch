"""The Fly deployment's only callers: cron-job.org ticks run-due every 15 min, and the nba repo's
daily Actions workflow uploads that day's predictions. Both send INTERNAL_JOBS_SECRET as
X-Internal-Secret; the private-data boundary in main.py lets /internal/* through to this check.
Responses carry slot-state counts and game counts only, never bets or stakes."""

import asyncio
import hmac
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api import routes
from app.jobs.__main__ import JOBS, job_context
from app.jobs.model_picks import Predictions
from app.jobs.runner import run_due

router = APIRouter(prefix="/internal")
# ponytail: a per-process lock, enough while Fly runs one machine (the /data volume pins it).
# A second machine would need a database lock; Neon's pooler breaks session advisory locks.
_tick_lock = asyncio.Lock()


def require_internal_secret(x_internal_secret: Optional[str] = Header(default=None)) -> None:
    expected = routes.settings.internal_jobs_secret
    if not expected:
        raise HTTPException(503, "Internal jobs are not configured on this deployment.")
    if not x_internal_secret or not hmac.compare_digest(
        x_internal_secret.encode(), expected.encode()
    ):
        raise HTTPException(401, "Invalid or missing internal secret.")


@router.post("/run-due", dependencies=[Depends(require_internal_secret)])
async def run_due_tick() -> dict:
    """Always 200: cron-job.org disables a job after repeated failures, and a failed or missed
    slot is reported here and on /ops instead."""
    if _tick_lock.locked():
        return {"status": "busy"}  # the last tick is still running; slots are idempotent
    async with _tick_lock:
        async with job_context(routes.settings) as ctx:
            results = await run_due(JOBS, ctx, datetime.now(timezone.utc))
    return {
        "status": "failures" if results.had_failures else "ok",
        "slots": dict(Counter(results.values())),
    }


@router.post("/nba-predictions", status_code=201, dependencies=[Depends(require_internal_secret)])
async def upload_predictions(preds: Predictions) -> dict:
    """Stores the file model_picks reads; the newest upload for a slate wins."""
    directory = Path(routes.settings.nba_predictions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{preds.slate_date.isoformat()}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(preds.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)  # load_predictions treats a file that exists as complete
    return {"slate_date": preds.slate_date.isoformat(), "games": len(preds.games)}
