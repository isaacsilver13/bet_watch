"""Slate badges: how many bets a game carries, and which teams have a star on the report."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.injuries import InjuredPlayer
from app.analytics.stars import star_flag
from app.domain.slate import BetBadge, SlateStars, StarFlagView
from app.storage.context_repository import ReportedInjury, merged_injuries
from app.storage.models import Bet, Game
from app.storage.stars import season_stars

NBA = "nba"
UPCOMING = ("scheduled", "in_progress")
GRADED = ("won", "lost", "push")


async def bet_badges(session: AsyncSession, game_ids: Sequence[int]) -> dict[int, BetBadge]:
    """One grouped query: bets, open bets and profit after fees over the graded ones."""
    if not game_ids:
        return {}
    profit = case(
        (Bet.status.in_(GRADED), func.coalesce(Bet.payout, 0.0) - Bet.stake - Bet.fee),
        else_=None,
    )
    rows = await session.execute(
        select(
            Bet.game_id,
            func.count(),
            func.sum(case((Bet.status == "open", 1), else_=0)),
            func.sum(profit),
            func.count(profit),
        )
        .where(Bet.game_id.in_(game_ids))
        .group_by(Bet.game_id)
    )
    return {
        game_id: BetBadge(
            total=total, open=int(opened or 0), net=round(net, 2) if settled else None
        )
        for game_id, total, opened, net, settled in rows.all()
    }


def _view(stars: set[str], injured: Sequence[ReportedInjury]) -> StarFlagView:
    flag = star_flag(stars, [InjuredPlayer(i.player, i.position, i.status) for i in injured])
    return StarFlagView(out=flag.out, questionable=flag.questionable)


async def slate_stars(
    session: AsyncSession, games: Sequence[Game], now: datetime
) -> dict[int, SlateStars]:
    """Games with a flagged star, for the upcoming NBA games among `games`."""
    upcoming = [g for g in games if g.sport == NBA and g.status in UPCOMING]
    if not upcoming:
        return {}
    team_ids = {team_id for g in upcoming for team_id in (g.home_team_id, g.away_team_id)}
    injuries, _ = await merged_injuries(session, NBA, team_ids, now)
    stars_by_season: dict[int, dict[int, set[str]]] = {}
    for season in sorted({g.season for g in upcoming}):
        stars_by_season[season] = await season_stars(session, NBA, season, team_ids, now)
    flagged: dict[int, SlateStars] = {}
    for g in upcoming:
        stars = stars_by_season[g.season]
        home = _view(stars[g.home_team_id], injuries[g.home_team_id])
        away = _view(stars[g.away_team_id], injuries[g.away_team_id])
        if home.out or home.questionable or away.out or away.questionable:
            flagged[g.id] = SlateStars(home=home, away=away)
    return flagged
