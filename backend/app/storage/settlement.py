"""Settle bets and model picks on finished games, and copy the closing line onto those that
have kicked off."""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.clv import clv, pick_close
from app.analytics.grading import grade, payout
from app.analytics.market import BookQuote, SideOffer, book_quotes, consensus
from app.analytics.pricing import implied_prob
from app.storage.bets_repository import OPEN
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers
from app.storage.models import Bet, Game, GameLine, ModelPick


async def settle_final(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Grade every open bet whose game has a final score."""
    settled = 0
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                select(Bet, Game)
                .join(Game, Bet.game_id == Game.id)
                .where(
                    Bet.status == OPEN,
                    Game.status == "final",
                    Game.home_score.is_not(None),
                    Game.away_score.is_not(None),
                )
            )
        ).all()
        for bet, game in rows:
            scores = (game.home_score, game.away_score)
            bet.status = grade(bet.market, bet.selection, bet.line, *scores)
            bet.payout = payout(bet.status, bet.stake, bet.price_american)
            bet.settled_at = now
            settled += 1
    return settled


async def fill_clv(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Copy the closing line onto every bet whose game has kicked off and has none yet."""
    filled = 0
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                select(Bet, Game)
                .join(Game, Bet.game_id == Game.id)
                .where(Bet.close_source.is_(None), Bet.status != "void", Game.start_time <= now)
            )
        ).all()
        by_game: dict[int, list[Bet]] = defaultdict(list)
        games: dict[int, Game] = {}
        for bet, game in rows:
            by_game[game.id].append(bet)
            games[game.id] = game
        for game_id, bets in by_game.items():
            snapshot, kickoff, espn = await _close_inputs(session, games[game_id])
            for bet in bets:
                close = pick_close(snapshot, kickoff, espn, bet.market, bet.selection)
                if close is None:
                    continue  # no close yet; the next run tries again
                result = clv(bet.market, bet.selection, bet.line, bet.price_american, close)
                bet.close_line = result.close_line
                bet.close_price = result.close_price
                bet.close_source = result.source
                bet.clv_points = result.points
                bet.clv_prob = result.prob
                filled += 1
    return filled


async def grade_model_picks(
    session_factory: async_sessionmaker[AsyncSession], now: datetime
) -> tuple[int, int]:
    """(results filled, closes filled) for model picks. Same close rule as bets."""
    graded = closed = 0
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                select(ModelPick, Game)
                .join(Game, ModelPick.game_id == Game.id)
                .where(
                    (ModelPick.result.is_(None) & (Game.status == "final"))
                    | (ModelPick.close_source.is_(None) & (Game.start_time <= now))
                )
            )
        ).all()
        inputs: dict[int, tuple] = {}
        for pick, game in rows:
            if pick.result is None and game.home_score is not None and game.away_score is not None:
                pick.result = grade(pick.market, pick.side, pick.line, game.home_score,
                                    game.away_score)
                graded += 1
            if pick.close_source is not None or aware(game.start_time) > now:
                continue
            if game.id not in inputs:
                inputs[game.id] = await _close_inputs(session, game)
            close = pick_close(*inputs[game.id], pick.market, pick.side)
            if close is None:
                continue  # no close yet; the next run tries again
            result = clv(pick.market, pick.side, pick.line, pick.price_american, close)
            pick.close_line = result.close_line
            pick.close_price = result.close_price
            pick.close_source = result.source
            pick.clv_points = result.points
            # Ratio CLV, p_close / p_pick - 1 (clv.ratio_clv without rounding the close price).
            pick.clv = None if result.prob is None else result.prob / implied_prob(
                pick.price_american)
            closed += 1
    return graded, closed


async def _close_inputs(session: AsyncSession, game: Game):
    """(newest pre-kick snapshot, kickoff, ESPN close): what `pick_close` chooses between."""
    kickoff = aware(game.start_time)
    snapshot = _last_snapshot(await side_offers(session, game, until=kickoff))
    return snapshot, kickoff, await _espn_close(session, game.id)


def _last_snapshot(offers: list[SideOffer]):
    """(time, consensus) of the newest snapshot among these offers, or None."""
    if not offers:
        return None
    latest = max(o.observed_at for o in offers)
    return latest, consensus(book_quotes(o for o in offers if o.observed_at == latest))


async def _espn_close(session: AsyncSession, game_id: int) -> Optional[BookQuote]:
    line = await session.scalar(
        select(GameLine).where(
            GameLine.game_id == game_id, GameLine.source == "espn", GameLine.kind == "close"
        )
    )
    if line is None:
        return None
    return BookQuote(
        book="espn",
        observed_at=aware(line.captured_at),
        spread_home=line.spread_home,
        spread_home_price=line.spread_home_price,
        spread_away_price=line.spread_away_price,
        total=line.total,
        over_price=line.over_price,
        under_price=line.under_price,
        ml_home=line.ml_home,
        ml_away=line.ml_away,
    )
