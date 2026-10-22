"""model_picks: price the nba repo's line-free predictions against the 10:00 CT NBA snapshot,
store the picks, and push the day's report to AIsaac.

Free (no provider calls). Its 10:15 CT slot is only planned once today's `odds_snapshot_nba` open
slot is terminal, so it never prices yesterday's lines. A manual run does not wait. The picks are
stored once per slate; a failed push leaves the slot pending, so the next tick retries the push
only. Reads `$NBA_PREDICTIONS_DIR/<date>.json`; no file means no picks today."""

import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analytics.market import book_quotes, consensus
from app.analytics.picks import PickFacts, game_picks, track_record
from app.config import Settings, load_settings
from app.jobs.odds import nba_open_slots
from app.jobs.runner import JobContext, get_slot_info
from app.jobs.slots import CENTRAL, TERMINAL_STATES, Slot, daily_slot
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers
from app.storage.models import Game, ModelPick, Team

logger = logging.getLogger(__name__)
NAME = "model_picks"
ODDS_JOB = "odds_snapshot_nba"
EASTERN = ZoneInfo("America/New_York")  # the NBA's schedule date
REPORT_PATH = "/internal/nba-report"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class Prediction(BaseModel):
    game_date: date
    home: str
    away: str
    pred_margin: float
    p_home_win: float = Field(ge=0, le=1)
    sigma: float = Field(gt=0)


class Predictions(BaseModel):
    """The nba repo's tools/predict_today.py output (isaacsilver13/nba#14)."""

    slate_date: date
    model_version: str = Field(max_length=80)
    generated_at: datetime
    data_through: date
    games: list[Prediction]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_predictions(directory: Path, day: date) -> Optional[Predictions]:
    path = directory / f"{day.isoformat()}.json"
    if not path.exists():
        return None  # written atomically, so a file that exists is complete
    return Predictions.model_validate_json(path.read_text(encoding="utf-8"))


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


async def _games(session: AsyncSession, preds: Predictions) -> dict[tuple, tuple[Game, str, str]]:
    """(Eastern date, home, away) -> (game, home abbr, away abbr) for the predicted dates."""
    dates = {g.game_date for g in preds.games}
    # UTC bounds: SQLite compares stored naive UTC with the bound value's wall clock.
    start = datetime.combine(min(dates), time(0), EASTERN).astimezone(timezone.utc)
    end = datetime.combine(max(dates) + timedelta(days=1), time(0), EASTERN).astimezone(
        timezone.utc)
    home, away = aliased(Team), aliased(Team)
    rows = await session.execute(
        select(Game, home.abbr, away.abbr)
        .join(home, Game.home_team_id == home.id)
        .join(away, Game.away_team_id == away.id)
        .where(Game.sport == "nba", Game.start_time >= start, Game.start_time < end)
    )
    return {
        (aware(game.start_time).astimezone(EASTERN).date(), h, a): (game, h, a)
        for game, h, a in rows.all()
    }


def _push_url(settings: Settings) -> Optional[str]:
    """The report endpoint, or None when the push is unconfigured or would send the secret
    in the clear to another machine."""
    base = (settings.aisaac_dashboard_url or "").rstrip("/")
    if not base or not settings.aisaac_internal_secret:
        return None
    parsed = urlparse(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        return None
    return base + REPORT_PATH


async def push_report(http: httpx.AsyncClient, settings: Settings, report: dict) -> bool:
    url = _push_url(settings)
    if url is None:
        logger.warning("model_picks: AIsaac push is not configured (or not https); not sent")
        return False
    try:
        response = await http.post(
            url, json=report, headers={"X-Internal-Secret": settings.aisaac_internal_secret},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        logger.warning("model_picks: AIsaac push failed (%s)", type(error).__name__)
        return False
    return True


class ModelPicksJob:
    name = NAME
    max_attempts = 8  # every tick after the picks are stored only retries the push

    def slots(self, now: datetime) -> list[Slot]:
        return []  # gated on the odds snapshot's state in the database: plan_slots

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        slots = daily_slot(self.name, now, start_hour=10, start_minute=15)
        if not slots:
            return []
        today_open = nba_open_slots(now, ODDS_JOB)[-1].key
        state = (await get_slot_info(ctx, ODDS_JOB, today_open)).state
        return slots if state in TERMINAL_STATES else []

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        settings = ctx.settings or load_settings()
        now = _now()
        day = now.astimezone(CENTRAL).date()
        preds = load_predictions(Path(settings.nba_predictions_dir), day)
        if preds is None or not preds.games:
            logger.info("model_picks: no predictions for %s", day)
            return "empty"
        report = await self._store_and_report(ctx, settings, preds, now)
        return "done" if await push_report(ctx.http, settings, report) else "pending"

    async def _store_and_report(
        self, ctx: JobContext, settings: Settings, preds: Predictions, now: datetime
    ) -> dict:
        async with ctx.session_factory() as session, session.begin():
            games = await _games(session, preds)
            stored = (await session.scalars(
                select(ModelPick).where(ModelPick.slate_date == preds.slate_date)
            )).all()
            summaries, new_picks, odds_as_of = [], [], None
            for pred in preds.games:
                match = games.get((pred.game_date, pred.home, pred.away))
                summary = {
                    "game_id": None, "home": pred.home, "away": pred.away, "tip": None,
                    "pred_margin": pred.pred_margin, "p_home_win": pred.p_home_win,
                    "market_spread_home": None, "market_home_win_prob": None, "books": 0,
                }
                summaries.append(summary)
                if match is None:
                    logger.warning("model_picks: no game for %s %s@%s",
                                   pred.game_date, pred.away, pred.home)
                    continue
                game = match[0]
                tip = aware(game.start_time)
                summary.update(game_id=game.id, tip=tip.isoformat())
                if game.status != "scheduled" or tip <= now:
                    continue  # the feed also carries in-game prices
                offers = await side_offers(session, game, until=now)
                if not offers:
                    continue
                newest = max(o.observed_at for o in offers)
                odds_as_of = max(odds_as_of, newest) if odds_as_of else newest
                quotes = book_quotes(offers)
                market = consensus(quotes)
                summary.update(market_spread_home=market.spread_home,
                               market_home_win_prob=_round(market.home_win_prob),
                               books=market.books)
                for pick in game_picks(
                    pred.pred_margin, pred.p_home_win, pred.sigma, quotes, market,
                    now.astimezone(CENTRAL).date(), settings.model_pick_stake,
                    settings.fractional_kelly, settings.max_kelly,
                ):
                    new_picks.append(ModelPick(
                        game_id=game.id, slate_date=preds.slate_date,
                        model_version=preds.model_version, market=pick.market, side=pick.side,
                        line=pick.line, book=pick.book, price_american=pick.price_american,
                        model_prob=pick.model_prob, market_prob_novig=pick.market_prob_novig,
                        ev=pick.ev, kelly_frac=pick.kelly_frac, tier=pick.tier, created_at=now,
                    ))
            if not stored:  # a retry only re-sends: the picks are the ones first priced
                session.add_all(new_picks)
                stored = new_picks
            facts = [PickFacts(p.tier, p.price_american, p.result, p.clv)
                     for p in await session.scalars(select(ModelPick))]
        by_id = {g.id: (g, h, a) for g, h, a in games.values()}
        picks = []
        for p in sorted(stored, key=lambda p: (p.tier != "targeted", -p.ev)):
            game, home, away = by_id[p.game_id]
            picks.append({
                "tier": p.tier, "game_id": p.game_id, "home": home, "away": away,
                "tip": aware(game.start_time).isoformat(), "market": p.market, "side": p.side,
                "team": home if p.side == "home" else away, "line": p.line, "book": p.book,
                "price_american": p.price_american, "model_prob": _round(p.model_prob),
                "market_prob": _round(p.market_prob_novig), "ev": _round(p.ev),
                "kelly_frac": _round(p.kelly_frac),
            })
        return {
            "kind": "nba_daily_report",
            "slate_date": preds.slate_date.isoformat(),
            "generated_at": now.isoformat(),
            "model_version": preds.model_version,
            "data_through": preds.data_through.isoformat(),
            "odds_as_of": odds_as_of.isoformat() if odds_as_of else None,
            "games": summaries,
            "picks": picks,
            "track_record": [asdict(row) for row in track_record(facts)],
        }
