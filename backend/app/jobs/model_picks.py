"""model_picks: price the nba repo's line-free predictions against the 10:00 CT NBA snapshot,
store the picks, and push the day's report to AIsaac.

Free (no provider calls). Its 10:15 CT slot is only planned once today's `odds_snapshot_nba` open
slot is terminal. It prices only post-forecast offers observed today (Central), and waits for
every scheduled game's quote before storing picks. A manual run does not wait for the snapshot
slot. Stored picks survive a failed AIsaac push, whose slot stays pending for retry before tip.
Reads the delivered forecast first, then `$NBA_PREDICTIONS_DIR/<date>.json` locally. Missing
forecasts or quotes use a waiting slot that expires as missed after the Central day ends."""

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.analytics.market import SideOffer, book_quotes, consensus, fresh_paired_main_offers
from app.analytics.picks import PickFacts, game_picks, track_record
from app.analytics.spread_curve import spread_mass
from app.config import Settings, load_settings
from app.domain.forecasts import Predictions
from app.domain.venues import venue_for_book
from app.jobs.odds import nba_open_slots
from app.jobs.runner import JobContext, get_slot_info
from app.jobs.slots import CENTRAL, TERMINAL_STATES, Slot, daily_slot
from app.storage.forecast_repository import (
    get_forecast,
    slate_matchups,
    validate_forecast_for_slate,
)
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers
from app.storage.models import Game, JobSlot, ModelPick, NbaForecast, NbaReport, Team

logger = logging.getLogger(__name__)
NAME = "model_picks"
ODDS_JOB = "odds_snapshot_nba"
EASTERN = ZoneInfo("America/New_York")  # the NBA's schedule date
REPORT_PATH = "/internal/nba-report"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class QuoteCoveragePending(Exception):
    """The slate changed or a complete fresh price disappeared before storage."""


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


async def _schedule_verified(factory, day: date) -> bool:
    """A successful scoreboard slot establishes an empty slate; no rows alone do not."""
    async with factory() as session:
        result = await session.scalar(
            select(JobSlot.id).where(
                JobSlot.job == "nba_scoreboard",
                JobSlot.slot_key.like(f"nba_scoreboard:{day.isoformat()}T%"),
                JobSlot.state.in_(("done", "empty")),
            ).limit(1)
        )
    return result is not None


def _has_priceable_main_market(offers: list[SideOffer], now: datetime) -> bool:
    return bool(fresh_paired_main_offers(offers, now))


async def _quotes_complete(ctx: JobContext, preds: Predictions, now: datetime) -> bool:
    today = datetime.combine(now.astimezone(CENTRAL).date(), time(0), CENTRAL).astimezone(
        timezone.utc
    )
    since = max(today, preds.generated_at.astimezone(timezone.utc))
    async with ctx.session_factory() as session:
        games = await _games(session, preds)
        for pred in preds.games:
            match = games.get((pred.game_date, pred.home, pred.away))
            if match is None:
                return False
            game = match[0]
            if game.status != "scheduled" or aware(game.start_time) <= now:
                return False
            offers = await side_offers(session, game, since=since, until=now)
            if not _has_priceable_main_market(offers, now):
                return False
    return True


class ModelPicksJob:
    name = NAME
    max_attempts = 8  # every tick after the picks are stored only retries the push

    def slots(self, now: datetime) -> list[Slot]:
        return []  # gated on the odds snapshot's state in the database: plan_slots

    async def plan_slots(self, ctx: JobContext, now: datetime) -> list[Slot]:
        slots = []
        today = now.astimezone(CENTRAL).date()
        async with ctx.session_factory() as session:
            waiting_keys = (await session.scalars(
                select(JobSlot.slot_key).where(
                    JobSlot.job == self.name, JobSlot.state == "waiting"
                )
            )).all()
            pending_keys = (await session.scalars(
                select(JobSlot.slot_key).where(
                    JobSlot.job == self.name, JobSlot.state == "pending"
                )
            )).all()
        for key in waiting_keys:
            slot_day = date.fromisoformat(key.removeprefix(f"{self.name}:"))
            if slot_day < today:
                slots.append(Slot(
                    key,
                    datetime.combine(slot_day, time(10, 15), CENTRAL),
                    datetime.combine(slot_day + timedelta(days=1), time(0), CENTRAL),
                ))
        for key in pending_keys:
            slot_day = date.fromisoformat(key.removeprefix(f"{self.name}:"))
            if slot_day < today:
                slots.append(Slot(
                    key,
                    datetime.combine(slot_day, time(10, 15), CENTRAL),
                    datetime.combine(slot_day + timedelta(days=1), time(6), CENTRAL),
                ))
        today_slots = daily_slot(self.name, now, start_hour=10, start_minute=15)
        if today_slots:
            today_open = nba_open_slots(now, ODDS_JOB)[-1].key
            state = (await get_slot_info(ctx, ODDS_JOB, today_open)).state
            if state in TERMINAL_STATES:
                slots.extend(today_slots)
        return slots

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        settings = ctx.settings or load_settings()
        now = _now()
        day = now.astimezone(CENTRAL).date()
        if slot is not None and slot.key.startswith(f"{self.name}:"):
            try:
                day = date.fromisoformat(slot.key.removeprefix(f"{self.name}:"))
            except ValueError:  # manual run has no slate date in its key
                pass
        async with ctx.session_factory() as session:
            saved_report = await session.get(NbaReport, day)
        if saved_report is not None:
            return ("done" if await push_report(ctx.http, settings, saved_report.payload)
                    else "pending")
        preds = await get_forecast(ctx.session_factory, day)
        if preds is None:
            preds = load_predictions(Path(settings.nba_predictions_dir), day)
        matchups = await slate_matchups(ctx.session_factory, day)
        if preds is None:
            if not matchups and await _schedule_verified(ctx.session_factory, day):
                logger.info("model_picks: no NBA slate for %s", day)
                return "empty"
            logger.info("model_picks: awaiting predictions or NBA schedule for %s", day)
            return "waiting"
        try:
            validate_forecast_for_slate(preds, matchups, now)
        except ValueError as error:
            logger.warning("model_picks: unusable forecast for %s: %s", day, error)
            return "waiting"
        if not await _quotes_complete(ctx, preds, now):
            logger.info("model_picks: awaiting post-forecast quotes for every game on %s", day)
            return "waiting"
        try:
            report = await self._store_and_report(ctx, settings, preds, now)
        except QuoteCoveragePending:
            logger.info("model_picks: quote coverage changed before the slate was stored")
            return "waiting"
        if report["odds_as_of"] is None:
            logger.info("model_picks: awaiting post-forecast odds for %s", day)
            return "waiting"
        return "done" if await push_report(ctx.http, settings, report) else "pending"


    async def _store_and_report(
        self, ctx: JobContext, settings: Settings, preds: Predictions, now: datetime
    ) -> dict:
        # UTC bound, as in _games. Older offers are another day's market (a dead API key once had
        # games priced on six-week-old lines).
        today = datetime.combine(now.astimezone(CENTRAL).date(), time(0), CENTRAL).astimezone(
            timezone.utc)
        since = max(today, preds.generated_at.astimezone(timezone.utc))
        forecast_sha256 = hashlib.sha256(json.dumps(
            preds.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        async with ctx.session_factory() as session, session.begin():
            games = await _games(session, preds)
            forecast_row = await session.get(NbaForecast, preds.slate_date)
            durable_forecast = (forecast_row is not None
                                and forecast_row.payload_sha256 == forecast_sha256)
            stored = (await session.scalars(
                select(ModelPick).where(ModelPick.slate_date == preds.slate_date)
            )).all()
            preexisting_picks = bool(stored)
            summaries, new_picks, odds_as_of = [], [], None
            fanduel_prices: dict[tuple[int, str, str, Optional[float]], Optional[int]] = {}
            source_offer_ids: dict[tuple[int, str, str, Optional[float]], Optional[int]] = {}
            spread_push_probs: dict[tuple[int, str, str, Optional[float]], float] = {}
            for pred in preds.games:
                match = games.get((pred.game_date, pred.home, pred.away))
                summary = {
                    "game_id": None, "home": pred.home, "away": pred.away, "tip": None,
                    "pred_margin": pred.pred_margin, "p_home_win": pred.p_home_win,
                    "pred_sigma": pred.sigma,
                    "market_spread_home": None, "market_home_win_prob": None, "books": 0,
                    "odds_as_of": None,
                }
                summaries.append(summary)
                if match is None:
                    raise QuoteCoveragePending
                game = match[0]
                tip = aware(game.start_time)
                summary.update(game_id=game.id, tip=tip.isoformat())
                if game.status != "scheduled" or tip <= now:
                    raise QuoteCoveragePending  # the feed also carries in-game prices
                offers = await side_offers(session, game, since=since, until=now)
                if not offers:
                    raise QuoteCoveragePending
                offers = fresh_paired_main_offers(offers, now)
                if not offers:
                    raise QuoteCoveragePending
                newest = max(o.observed_at for o in offers)
                odds_as_of = max(odds_as_of, newest) if odds_as_of else newest
                summary["odds_as_of"] = newest.isoformat()
                quotes = book_quotes(offers)
                market = consensus(quotes)
                summary.update(market_spread_home=market.spread_home,
                               market_home_win_prob=_round(market.home_win_prob),
                               books=market.books)
                fanduel = next((q for q in quotes if venue_for_book(q.book) == "fanduel"), None)
                for pick in game_picks(
                    pred.pred_margin, pred.p_home_win, pred.sigma, quotes, market,
                    now.astimezone(CENTRAL).date(), settings.model_pick_stake,
                    settings.fractional_kelly, settings.max_kelly,
                ):
                    fd_price = None
                    if fanduel is not None:
                        if pick.market == "moneyline":
                            fd_price = fanduel.ml_home if pick.side == "home" else fanduel.ml_away
                        elif fanduel.spread_home == market.spread_home:
                            fd_price = (fanduel.spread_home_price if pick.side == "home"
                                        else fanduel.spread_away_price)
                    key = (game.id, pick.market, pick.side, pick.line)
                    if not preexisting_picks:
                        if pick.market == "spread" and market.spread_home is not None:
                            spread_push_probs[key] = spread_mass(
                                pred.pred_margin, pred.sigma, market.spread_home
                            ).push
                        fanduel_prices[key] = fd_price
                        matching = [o for o in offers if o.market == pick.market
                                    and o.side == pick.side and o.line == pick.line
                                    and o.price == pick.price_american
                                    and venue_for_book(o.book) == pick.book]
                        source_offer_ids[key] = (matching[0].observation_id
                                                 if len(matching) == 1 else None)
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
                key = (p.game_id, p.market, p.side, p.line)
                push_prob = spread_push_probs.get(key)
                picks.append({
                    "tier": p.tier, "game_id": p.game_id, "home": home, "away": away,
                    "tip": aware(game.start_time).isoformat(), "market": p.market,
                    "side": p.side, "team": home if p.side == "home" else away,
                    "line": p.line, "book": p.book,
                    "price_american": p.price_american,
                    "fanduel_price_american": fanduel_prices.get(
                        (p.game_id, p.market, p.side, p.line)),
                    "quote_observation_id": source_offer_ids.get(
                        (p.game_id, p.market, p.side, p.line)),
                    "paper_win_prob": p.model_prob if durable_forecast
                    and not preexisting_picks else None,
                    "model_prob": _round(p.model_prob),
                    "model_prob_conditional": _round(
                        p.model_prob / (1 - push_prob)
                    ) if push_prob is not None and push_prob < 1 else (
                        _round(p.model_prob) if p.market == "moneyline" else None
                    ),
                    "push_prob": _round(push_prob),
                    "market_prob": _round(p.market_prob_novig), "ev": _round(p.ev),
                    "kelly_frac": _round(p.kelly_frac),
                })
            report = {
                "kind": "nba_daily_report",
                "slate_date": preds.slate_date.isoformat(),
                "generated_at": now.isoformat(),
                "model_version": preds.model_version,
                "forecast_sha256": forecast_sha256 if durable_forecast
                and not preexisting_picks else None,
                "data_through": preds.data_through.isoformat(),
                "odds_as_of": odds_as_of.isoformat() if odds_as_of else None,
                "games": summaries,
                "picks": picks,
                "track_record": [asdict(row) for row in track_record(facts)],
            }
            session.add(NbaReport(slate_date=preds.slate_date, created_at=now,
                                  payload=report))
            return report
