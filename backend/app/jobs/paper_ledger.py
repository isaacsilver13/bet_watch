"""Freeze today's durable NBA paper profiles after the saved report, before tip."""

from datetime import date, datetime, timedelta, timezone

from app.jobs.runner import JobContext
from app.jobs.slots import CENTRAL, Slot, hourly_slots
from app.storage.games_repository import aware
from app.storage.models import NbaReport
from app.storage.paper_ledger import record_paper_day, settle_finished_paper


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaperLedgerJob:
    name = "paper_ledger"
    max_attempts = 8

    def slots(self, now: datetime) -> list[Slot]:
        return []

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        slate = now.astimezone(CENTRAL).date()
        async with ctx.session_factory() as session:
            report = await session.get(NbaReport, slate)
        if report is None or not report.payload.get("forecast_sha256"):
            return []
        tips = [datetime.fromisoformat(game["tip"]) for game in report.payload["games"]
                if game.get("tip")]
        end = min(tips) if tips else datetime.combine(
            slate + timedelta(days=1), datetime.min.time(), CENTRAL
        )
        # The runner captured `now` before an earlier job created this report.
        # Let this tick freeze the portfolio rather than waiting 15 minutes.
        return [Slot(f"{self.name}:{slate.isoformat()}",
                     min(aware(report.created_at), now), end)]

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        key = slot.key.removeprefix(f"{self.name}:")
        try:
            slate = date.fromisoformat(key)
        except ValueError:
            slate = _now().astimezone(CENTRAL).date()
        await record_paper_day(ctx.session_factory, slate, _now())
        return "done"


class PaperSettlementJob:
    name = "paper_settlement"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        await settle_finished_paper(ctx.session_factory, _now())
        return "done"
