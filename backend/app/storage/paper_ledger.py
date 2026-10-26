"""Immutable NBA paper decisions and settlement credits for independent variants."""

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from math import isclose
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.analytics.grading import grade, payout
from app.analytics.market import fresh_paired_main_offers
from app.analytics.portfolio import allocate_shadow_profiles, candidates_from_report
from app.domain.forecasts import Predictions
from app.domain.venues import venue_for_book
from app.storage.games_repository import aware
from app.storage.lines_repository import side_offers
from app.storage.models import (
    Game,
    NbaForecast,
    NbaReport,
    OfferObservation,
    PaperAllocationBatch,
    PaperSettlement,
    PaperWager,
    Team,
)

EASTERN = ZoneInfo("America/New_York")
CENTRAL = ZoneInfo("America/Chicago")
PROFILES = ("all", *(f"top_{n}" for n in range(1, 11)))


def _report_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _lock_writer(session: AsyncSession) -> None:
    # Serialize before reading balances or open settlements across app processes.
    if session.bind.dialect.name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
    elif session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(843614201)"))


async def _available_cash(session: AsyncSession, profile: str) -> float:
    rows = (await session.execute(
        select(PaperWager.stake, PaperWager.fee, PaperSettlement.payout)
        .join(PaperAllocationBatch, PaperWager.batch_id == PaperAllocationBatch.id)
        .outerjoin(PaperSettlement, PaperSettlement.wager_id == PaperWager.id)
        .where(PaperAllocationBatch.profile == profile)
    )).all()
    return round(1000.0 + sum(
        (returned or 0.0) - stake - fee for stake, fee, returned in rows
    ), 2)


async def _verified_sides(
    session: AsyncSession, report: dict, slate: date, now: datetime
) -> dict[str, str]:
    if report.get("slate_date") != slate.isoformat():
        raise ValueError("saved report date does not match its database key")
    forecast_hash = report.get("forecast_sha256")
    forecast = await session.get(NbaForecast, slate)
    if not forecast_hash or forecast is None or forecast.payload_sha256 != forecast_hash:
        raise ValueError("saved report lacks a matching durable forecast")
    predictions = Predictions.model_validate(forecast.payload)
    if predictions.slate_date != slate:
        raise ValueError("durable forecast date does not match paper slate")
    if _report_hash(predictions.model_dump(mode="json")) != forecast_hash:
        raise ValueError("durable forecast payload hash does not match paper report")
    forecast_games = {(game.game_date, game.home, game.away): game
                      for game in predictions.games}
    report_games = {game.get("game_id"): game for game in report.get("games", [])}
    try:
        generated_at = datetime.fromisoformat(report["generated_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("saved report lacks a valid decision time") from error
    if generated_at.tzinfo is None:
        raise ValueError("saved report decision time needs a timezone")
    if generated_at > now:
        raise ValueError("saved report was generated after the paper decision time")
    since = max(datetime.combine(slate, time(0), CENTRAL).astimezone(timezone.utc),
                aware(forecast.generated_at))
    sides: dict[str, str] = {}
    for pick in report["picks"]:
        if pick.get("tier") != "targeted" or pick.get("market") != "moneyline":
            continue
        game = await session.get(Game, pick["game_id"])
        if (game is None or game.sport != "nba"
                or aware(game.start_time).astimezone(EASTERN).date() != slate
                or aware(game.start_time) <= generated_at
                or aware(game.start_time) <= now or game.status != "scheduled"):
            raise ValueError("paper pick game is missing or outside the pregame slate")
        summary = report_games.get(game.id)
        home_team = await session.get(Team, game.home_team_id)
        away_team = await session.get(Team, game.away_team_id)
        if (not summary or not home_team or not away_team
                or summary.get("home") != home_team.abbr
                or summary.get("away") != away_team.abbr):
            raise ValueError("paper report matchup differs from scheduled game")
        predicted = (forecast_games.get((slate, summary.get("home"), summary.get("away")))
                     if summary else None)
        if predicted is None or pick.get("side") not in {"home", "away"}:
            raise ValueError("paper pick has no matching forecast game and side")
        expected_prob = (predicted.p_home_win if pick["side"] == "home"
                         else 1 - predicted.p_home_win)
        if not isclose(float(pick.get("paper_win_prob", -1)), expected_prob,
                       rel_tol=0, abs_tol=1e-12):
            raise ValueError("paper pick probability differs from delivered forecast")
        quote_id = pick["quote_observation_id"]
        eligible = fresh_paired_main_offers(
            await side_offers(session, game, since=since, until=generated_at), generated_at
        )
        matched = [offer for offer in eligible
                   if offer.observation_id == quote_id]
        if (len(matched) != 1 or matched[0].market != "moneyline"
                or matched[0].side != pick.get("side")
                or matched[0].price != pick.get("price_american")
                or venue_for_book(matched[0].book) != venue_for_book(pick.get("book", ""))
                or matched[0].observed_at >= aware(game.start_time)):
            raise ValueError("paper pick does not match its selected pregame quote")
        sides[str(quote_id)] = matched[0].side
    return sides


async def record_paper_day(
    factory: async_sessionmaker[AsyncSession], slate: date,
    now: datetime | None = None,
) -> tuple[PaperAllocationBatch, ...]:
    """Freeze all eleven variants together; retries return the original batches."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("paper decision time needs a timezone")
    async with factory() as session, session.begin():
        await _lock_writer(session)
        existing = (await session.scalars(
            select(PaperAllocationBatch).where(PaperAllocationBatch.slate_date == slate)
            .order_by(PaperAllocationBatch.profile)
        )).all()
        if existing:
            if {row.profile for row in existing} != set(PROFILES):
                raise ValueError("incomplete paper batch needs reconciliation")
            return tuple(existing)
        later = await session.scalar(select(PaperAllocationBatch.id).where(
            PaperAllocationBatch.slate_date > slate
        ).limit(1))
        if later is not None:
            raise ValueError("cannot backfill a paper day after a later allocation")
        saved = await session.get(NbaReport, slate)
        if saved is None:
            raise ValueError("no saved NBA report for paper allocation")
        report = saved.payload
        candidates = candidates_from_report(report)
        sides = await _verified_sides(session, report, slate, now)
        if len(sides) != len(candidates):
            raise ValueError("paper candidate and verified quote counts differ")
        cash = {profile: await _available_cash(session, profile) for profile in PROFILES}
        if any(amount <= 0 for amount in cash.values()):
            raise ValueError("paper profile has no available cash")
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        allocations = allocate_shadow_profiles(candidates, slate, cash)
        batches = []
        for allocation in allocations:
            batch = PaperAllocationBatch(
                profile=allocation.profile, slate_date=slate, created_at=now,
                report_sha256=_report_hash(report),
                forecast_sha256=report["forecast_sha256"],
                opening_cash=allocation.opening_bankroll,
                allocation=asdict(allocation),
            )
            session.add(batch)
            await session.flush()
            batches.append(batch)
            for bet in allocation.bets:
                candidate = candidate_by_id[bet.candidate_id]
                session.add(PaperWager(
                    batch_id=batch.id, candidate_id=bet.candidate_id,
                    game_id=bet.game_id, quote_observation_id=int(candidate.quote_id),
                    side=sides[candidate.quote_id], book=candidate.book,
                    price_american=candidate.price_american,
                    win_prob=candidate.win_prob, stake=bet.stake, fee=bet.fee,
                    net_ev=bet.net_ev,
                ))
        return tuple(batches)


async def settle_finished_paper(
    factory: async_sessionmaker[AsyncSession], now: datetime | None = None,
) -> int:
    """Append one payout credit per final-score wager; repeated runs are no-ops."""
    now = now or datetime.now(timezone.utc)
    async with factory() as session, session.begin():
        await _lock_writer(session)
        rows = (await session.execute(
            select(PaperWager, Game)
            .join(Game, PaperWager.game_id == Game.id)
            .outerjoin(PaperSettlement, PaperSettlement.wager_id == PaperWager.id)
            .where(PaperSettlement.wager_id.is_(None), Game.status == "final",
                   Game.home_score.is_not(None), Game.away_score.is_not(None))
        )).all()
        for wager, game in rows:
            result = grade("moneyline", wager.side, None, game.home_score, game.away_score)
            session.add(PaperSettlement(
                wager_id=wager.id, result=result,
                payout=payout(result, wager.stake, wager.price_american), settled_at=now,
                home_score=game.home_score, away_score=game.away_score,
                game_updated_at=game.updated_at,
            ))
        return len(rows)


async def paper_balances(factory: async_sessionmaker[AsyncSession]) -> dict[str, float]:
    async with factory() as session:
        return {profile: await _available_cash(session, profile) for profile in PROFILES}


async def paper_status(factory: async_sessionmaker[AsyncSession]) -> dict:
    """Current cash and open exposure, with settled return separated from open bets."""
    async with factory() as session:
        rows = (await session.execute(
            select(PaperAllocationBatch.id, PaperAllocationBatch.profile,
                   PaperAllocationBatch.slate_date, PaperWager.stake, PaperWager.fee,
                   PaperSettlement.payout, PaperSettlement.result)
            .outerjoin(PaperWager, PaperWager.batch_id == PaperAllocationBatch.id)
            .outerjoin(PaperSettlement, PaperSettlement.wager_id == PaperWager.id)
        )).all()
    profiles = {name: {
        "profile": name, "starting_bankroll": 1000.0, "cash": 1000.0,
        "open_stake": 0.0, "equity_at_cost": 1000.0, "recorded_days": 0,
        "open_bets": 0, "settled_bets": 0, "settled_net": 0.0,
        "settled_roi": None, "last_slate_date": None,
    } for name in PROFILES}
    settled_cost = {name: 0.0 for name in PROFILES}
    seen_batches: set[int] = set()
    for batch_id, name, slate_date, stake, fee, returned, result in rows:
        item = profiles[name]
        if batch_id not in seen_batches:
            seen_batches.add(batch_id)
            item["recorded_days"] += 1
            slate = slate_date.isoformat()
            if item["last_slate_date"] is None or slate > item["last_slate_date"]:
                item["last_slate_date"] = slate
        if stake is None:
            continue
        item["cash"] += (returned or 0.0) - stake - fee
        if result is None:
            item["open_bets"] += 1
            item["open_stake"] += stake
        else:
            item["settled_bets"] += 1
            item["settled_net"] += returned - stake - fee
            settled_cost[name] += stake + fee
    for name, item in profiles.items():
        item["cash"] = round(item["cash"], 2)
        item["open_stake"] = round(item["open_stake"], 2)
        item["equity_at_cost"] = round(item["cash"] + item["open_stake"], 2)
        item["settled_net"] = round(item["settled_net"], 2)
        if settled_cost[name]:
            item["settled_roi"] = round(item["settled_net"] / settled_cost[name], 4)
    return {"profiles": [profiles[name] for name in PROFILES]}


async def paper_day(factory: async_sessionmaker[AsyncSession], slate: date) -> dict | None:
    """Read frozen decisions and result evidence for one recorded slate."""
    home = aliased(Team)
    away = aliased(Team)
    async with factory() as session:
        rows = (await session.execute(
            select(PaperAllocationBatch, PaperWager, PaperSettlement,
                   OfferObservation, Game, home.abbr, away.abbr,
                   NbaReport.payload, NbaForecast.payload_sha256)
            .outerjoin(PaperWager, PaperWager.batch_id == PaperAllocationBatch.id)
            .outerjoin(PaperSettlement, PaperSettlement.wager_id == PaperWager.id)
            .outerjoin(OfferObservation,
                       OfferObservation.id == PaperWager.quote_observation_id)
            .outerjoin(Game, Game.id == PaperWager.game_id)
            .outerjoin(home, home.id == Game.home_team_id)
            .outerjoin(away, away.id == Game.away_team_id)
            .outerjoin(NbaReport, NbaReport.slate_date == PaperAllocationBatch.slate_date)
            .outerjoin(NbaForecast, NbaForecast.slate_date == PaperAllocationBatch.slate_date)
            .where(PaperAllocationBatch.slate_date == slate)
            .order_by(PaperAllocationBatch.id, PaperWager.id)
        )).all()
    if not rows:
        return None
    profiles: dict[str, dict] = {}
    report_payload = rows[0][7]
    forecast_hash = rows[0][8]
    for batch, wager, settlement, quote, game, home_abbr, away_abbr, _, _ in rows:
        if batch.profile not in profiles:
            profiles[batch.profile] = {
                "profile": batch.profile,
                "opening_cash": batch.opening_cash,
                "decision_at": aware(batch.created_at).isoformat(),
                "report_sha256": batch.report_sha256,
                "forecast_sha256": batch.forecast_sha256,
                "policy": batch.allocation["policy"],
                "bets": [],
            }
        if wager is None:
            continue
        if quote is None or game is None or home_abbr is None or away_abbr is None:
            raise ValueError("paper wager is missing quote or matchup provenance")
        profiles[batch.profile]["bets"].append({
            "wager_id": wager.id,
            "candidate_id": wager.candidate_id,
            "game_id": wager.game_id,
            "home": home_abbr,
            "away": away_abbr,
            "side": wager.side,
            "book": wager.book,
            "price_american": wager.price_american,
            "win_prob": wager.win_prob,
            "stake": wager.stake,
            "fee": wager.fee,
            "net_ev": wager.net_ev,
            "quote_observation_id": wager.quote_observation_id,
            "quote_observed_at": aware(quote.observed_at).isoformat(),
            "quote_provider_updated_at": (
                aware(quote.provider_updated_at).isoformat()
                if quote.provider_updated_at else None
            ),
            "result": settlement.result if settlement else None,
            "payout": settlement.payout if settlement else None,
            "net_return": (round(settlement.payout - wager.stake - wager.fee, 2)
                           if settlement else None),
            "settled_at": (aware(settlement.settled_at).isoformat()
                           if settlement else None),
            "home_score": settlement.home_score if settlement else None,
            "away_score": settlement.away_score if settlement else None,
            "game_updated_at": (aware(settlement.game_updated_at).isoformat()
                                if settlement else None),
        })
    return {"slate_date": slate.isoformat(),
            "report_matches_recorded": (report_payload is not None and all(
                _report_hash(report_payload) == profile["report_sha256"]
                for profile in profiles.values()
            )),
            "forecast_matches_recorded": (forecast_hash is not None and all(
                forecast_hash == profile["forecast_sha256"]
                for profile in profiles.values()
            )),
            "profiles": [profiles[name] for name in PROFILES if name in profiles]}
