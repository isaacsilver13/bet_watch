"""grade_bets: settle bets on finished games and copy closing lines onto bets already kicked off.

Local and free (no provider calls), so it simply runs every hour; both steps are idempotent."""

import logging
from datetime import datetime, timezone

from app.jobs.runner import JobContext
from app.jobs.slots import Slot, hourly_slots
from app.storage.settlement import fill_clv, settle_final

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GradeBetsJob:
    name = "grade_bets"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        now = _now()
        settled = await settle_final(ctx.session_factory, now)
        closed = await fill_clv(ctx.session_factory, now)
        logger.info("grade_bets: %s settled, %s closing lines filled", settled, closed)
        return "done"
