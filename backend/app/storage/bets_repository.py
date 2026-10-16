"""The bet log: entry, edits and reads. Settlement and closing lines are in settlement.py."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.analytics.bet_stats import BetFacts
from app.analytics.fees import fee_for
from app.analytics.grading import payout
from app.domain.bets import BetIn, BetPatch
from app.jobs.slots import CENTRAL
from app.storage.games_repository import aware
from app.storage.models import Bet, Game, Team

OPEN = "open"


class BetNotFound(Exception):
    pass


class BetNotOpen(Exception):
    pass


class GameNotFound(Exception):
    pass


@dataclass(frozen=True)
class BetRow:
    bet: Bet
    game: Game
    home: str
    away: str


def _placed_on(placed_at: datetime):
    return aware(placed_at).astimezone(CENTRAL).date()


class BetsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create(self, data: BetIn, now: datetime) -> int:
        async with self.session_factory() as session, session.begin():
            if await session.get(Game, data.game_id) is None:
                raise GameNotFound(data.game_id)
            placed_at = data.placed_at or now
            fee = data.fee
            if fee is None:
                fee = fee_for(data.venue, data.stake, _placed_on(placed_at))
            bet = Bet(
                game_id=data.game_id,
                placed_at=placed_at,
                venue=data.venue,
                market=data.market,
                selection=data.selection,
                line=data.line,
                price_american=data.price_american,
                stake=data.stake,
                fee=fee,
                status=OPEN,
                note=data.note,
            )
            session.add(bet)
            await session.flush()
            return bet.id

    async def get(self, bet_id: int) -> Optional[BetRow]:
        rows = await self.rows(Bet.id == bet_id)
        return rows[0] if rows else None

    async def rows(self, *conditions) -> list[BetRow]:
        home, away = aliased(Team), aliased(Team)
        async with self.session_factory() as session:
            found = (
                await session.execute(
                    select(Bet, Game, home.abbr, away.abbr)
                    .join(Game, Bet.game_id == Game.id)
                    .join(home, Game.home_team_id == home.id)
                    .join(away, Game.away_team_id == away.id)
                    .where(*conditions)
                    .order_by(Bet.placed_at.desc(), Bet.id.desc())
                )
            ).all()
        return [BetRow(*columns) for columns in found]

    async def patch(self, bet_id: int, data: BetPatch) -> None:
        async with self.session_factory() as session, session.begin():
            bet = await session.get(Bet, bet_id)
            if bet is None:
                raise BetNotFound(bet_id)
            if bet.status != OPEN:
                raise BetNotOpen(bet_id)
            fields = data.model_dump(exclude_unset=True)
            if bet.market == "moneyline" and fields.get("line") is not None:
                raise ValueError("a moneyline bet has no line")
            for name in ("venue", "line", "price_american", "stake", "note"):
                if fields.get(name) is not None:
                    setattr(bet, name, fields[name])
            if fields.get("line") is not None or fields.get("price_american") is not None:
                # A copied closing line was measured against the old number: refill it next run.
                bet.close_line = bet.close_price = bet.close_source = None
                bet.clv_points = bet.clv_prob = None
            if fields.get("fee") is not None:
                bet.fee = fields["fee"]
            elif "venue" in fields or "stake" in fields:
                bet.fee = fee_for(bet.venue, bet.stake, _placed_on(bet.placed_at))
            if fields.get("status") == "void":
                bet.status = "void"
                bet.payout = payout("void", bet.stake, bet.price_american)

    async def facts(self) -> list[BetFacts]:
        return [
            BetFacts(
                venue=row.bet.venue,
                sport=row.game.sport,
                market=row.bet.market,
                status=row.bet.status,
                stake=row.bet.stake,
                fee=row.bet.fee,
                payout=row.bet.payout,
                clv_points=row.bet.clv_points,
                clv_prob=row.bet.clv_prob,
            )
            for row in await self.rows()
        ]
