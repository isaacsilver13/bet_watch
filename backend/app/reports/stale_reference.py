"""Research-only NBA forecast comparison against saved ESPN reference prices.

These prices may be stale or unavailable to bet. This module never records a wager,
applies Kelly sizing, or labels a reference edge as an actionable value pick.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.analytics.pricing import decimal_odds, implied_prob, novig
from app.analytics.spread_curve import spread_mass
from app.domain.forecasts import Prediction, Predictions
from app.storage.database import create_engine, create_session_factory
from app.storage.forecast_repository import get_forecast
from app.storage.games_repository import aware
from app.storage.models import Game, GameLine, Team
from app.storage.trial_branch import trial_settings

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ReferenceLine:
    home: str
    away: str
    tip: datetime
    captured_at: datetime
    spread_home: Optional[float]
    spread_home_price: Optional[int]
    spread_away_price: Optional[int]
    ml_home: Optional[int]
    ml_away: Optional[int]


def _valid_price(price: Optional[int]) -> bool:
    return price is not None and (price >= 100 or price <= -100)


def _moneyline(pred: Prediction, line: ReferenceLine) -> list[dict]:
    if not (_valid_price(line.ml_home) and _valid_price(line.ml_away)):
        return []
    home_market, away_market = novig(implied_prob(line.ml_home), implied_prob(line.ml_away))
    return [
        {
            "market": "moneyline", "side": side, "price_american": price,
            "model_win_prob": round(prob, 4), "reference_prob_novig": round(market_prob, 4),
            "gross_ev_at_reference": round(prob * decimal_odds(price) - 1, 4),
        }
        for side, price, prob, market_prob in (
            ("home", line.ml_home, pred.p_home_win, home_market),
            ("away", line.ml_away, 1 - pred.p_home_win, away_market),
        )
    ]


def _spread(pred: Prediction, line: ReferenceLine) -> list[dict]:
    if (line.spread_home is None or not _valid_price(line.spread_home_price)
            or not _valid_price(line.spread_away_price)):
        return []
    mass = spread_mass(pred.pred_margin, pred.sigma, line.spread_home)
    home_market, away_market = novig(
        implied_prob(line.spread_home_price), implied_prob(line.spread_away_price)
    )
    return [
        {
            "market": "spread", "side": side, "line": point,
            "price_american": price, "model_win_prob": round(win, 4),
            "model_push_prob": round(mass.push, 4),
            "reference_prob_novig": round(market_prob, 4),
            "gross_ev_at_reference": round(win * (decimal_odds(price) - 1) - loss, 4),
        }
        for side, point, price, win, loss, market_prob in (
            ("home", line.spread_home, line.spread_home_price,
             mass.home_cover, mass.away_cover, home_market),
            ("away", -line.spread_home, line.spread_away_price,
             mass.away_cover, mass.home_cover, away_market),
        )
    ]


def build_reference_preview(
    forecast: Predictions, lines: dict[tuple[str, str], ReferenceLine], as_of: datetime
) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a time zone")
    games = []
    for pred in forecast.games:
        line = lines.get((pred.home, pred.away))
        row = {
            "home": pred.home, "away": pred.away, "pred_margin": pred.pred_margin,
            "p_home_win": pred.p_home_win, "sigma": pred.sigma,
            "reference_source": "espn_current", "book_attribution": "unverified",
            "source_quote_time_available": False, "eligible_for_paper": False,
            "quotes": [],
        }
        if line is None or aware(line.captured_at) > as_of:
            row["status"] = "no_saved_reference"
        elif aware(line.tip) <= as_of:
            row["status"] = "past_tip"
        else:
            row["status"] = "reference_only"
            row["captured_at"] = aware(line.captured_at).isoformat()
            row["capture_age_hours"] = round(
                (as_of - aware(line.captured_at)).total_seconds() / 3600, 2
            )
            row["quotes"] = [*_moneyline(pred, line), *_spread(pred, line)]
        games.append(row)
    return {
        "kind": "nba_reference_preview", "slate_date": forecast.slate_date.isoformat(),
        "as_of": as_of.isoformat(), "model_version": forecast.model_version,
        "forecast_generated_at": forecast.generated_at.isoformat(),
        "data_through": forecast.data_through.isoformat(),
        "prices_are_actionable": False, "games": games,
    }


async def load_reference_lines(
    factory: async_sessionmaker[AsyncSession], day: date, as_of: datetime
) -> dict[tuple[str, str], ReferenceLine]:
    start = datetime.combine(day, time(0), EASTERN).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), EASTERN)
    end = end.astimezone(timezone.utc)
    home, away = aliased(Team), aliased(Team)
    async with factory() as session:
        rows = (await session.execute(
            select(Game, home.abbr, away.abbr, GameLine)
            .join(home, Game.home_team_id == home.id)
            .join(away, Game.away_team_id == away.id)
            .join(GameLine, GameLine.game_id == Game.id)
            .where(
                Game.sport == "nba", Game.season_type == "REG",
                Game.start_time >= start, Game.start_time < end,
                GameLine.source == "espn", GameLine.kind == "current",
                GameLine.captured_at <= as_of,
            )
        )).all()
    return {
        (home_abbr, away_abbr): ReferenceLine(
            home_abbr, away_abbr, aware(game.start_time), aware(line.captured_at),
            line.spread_home, line.spread_home_price, line.spread_away_price,
            line.ml_home, line.ml_away,
        )
        for game, home_abbr, away_abbr, line in rows
    }


async def preview_for_day(day: date) -> dict:
    engine = create_engine(trial_settings())
    try:
        factory = create_session_factory(engine)
        forecast = await get_forecast(factory, day)
        if forecast is None:
            raise RuntimeError(f"No verified forecast has been imported for {day}")
        now = datetime.now(timezone.utc)
        lines = await load_reference_lines(factory, day, now)
        return build_reference_preview(forecast, lines, now)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(preview_for_day(args.date)), indent=2))


if __name__ == "__main__":
    main()
