import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from app.analytics.pricing import fair_american
from app.domain.bets import BetIn, BetPatch
from app.ingest.espn import parse_scoreboard
from app.ingest.odds_raw import write_raw
from app.ingest.odds_snapshot import ingest_snapshot
from app.ingest.records import LineRecord
from app.ingest.seed import seed_teams
from app.jobs import bets as bets_job
from app.jobs.__main__ import JOBS
from app.jobs.bets import GradeBetsJob
from app.jobs.runner import JobContext, run_due
from app.jobs.slots import CENTRAL, Slot
from app.storage.bets_repository import BetsRepository
from app.storage.games_repository import GamesRepository
from app.storage.settlement import fill_clv, settle_final
from sqlalchemy import text

from tests.matchup_helpers import load_json, new_session_factory

KICKOFF = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
AFTER = KICKOFF + timedelta(minutes=5)
SEEDED = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


def _records():
    return parse_scoreboard(load_json("espn/scoreboard_week6.json"))


async def _setup(tmp_path):
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    games = GamesRepository(factory)
    await games.upsert_games("nfl", "espn", _records(), SEEDED)
    async with factory() as session:
        game_id = (await session.execute(
            text("select id from games where espn_id = '401872995'"))).scalar_one()
        future_id = (await session.execute(
            text("select id from games where espn_id = '401872999'"))).scalar_one()
    return factory, games, BetsRepository(factory), game_id, future_id


def _bet(game_id, **changes):
    fields = {"game_id": game_id, "venue": "draftkings", "market": "spread", "selection": "home",
              "line": -2.5, "price_american": -110, "stake": 11.0}
    fields.update(changes)
    return BetIn(**fields)


async def _snapshot(factory, raw_dir, taken):
    body = json.dumps(load_json("the_odds_api/nfl_week6.json"))
    path = write_raw(raw_dir, "the_odds_api", f"snapshot-{taken:%H%M}", taken, body)
    await ingest_snapshot(factory, path, (KICKOFF, KICKOFF + timedelta(minutes=1)), 450)


def test_final_games_settle_their_open_bets_exactly_once(tmp_path) -> None:
    async def scenario() -> None:
        factory, games, repo, game_id, future_id = await _setup(tmp_path)
        final = replace(next(r for r in _records() if r.espn_id == "401872995"),
                        status="final", home_score=24, away_score=20)
        await games.upsert_games("nfl", "espn", [final], AFTER)
        ids = {
            "won": await repo.create(_bet(game_id), AFTER),  # DEN -2.5, won by 4
            "lost": await repo.create(_bet(game_id, market="total", selection="over", line=44.5,
                                           price_american=-105, stake=20.0), AFTER),  # 44 points
            "push": await repo.create(_bet(game_id, market="total", selection="under", line=44.0),
                                      AFTER),
            "ml": await repo.create(_bet(game_id, market="moneyline", selection="away", line=None,
                                         price_american=150, stake=10.0), AFTER),
            "future": await repo.create(_bet(future_id), AFTER),
        }
        voided = await repo.create(_bet(game_id), AFTER)
        await repo.patch(voided, BetPatch(status="void"))

        assert await settle_final(factory, AFTER) == 4
        rows = {name: (await repo.get(bet_id)).bet for name, bet_id in ids.items()}
        assert (rows["won"].status, rows["won"].payout) == ("won", 21.0)
        assert (rows["lost"].status, rows["lost"].payout) == ("lost", 0.0)
        assert (rows["push"].status, rows["push"].payout) == ("push", 11.0)
        assert (rows["ml"].status, rows["ml"].payout) == ("lost", 0.0)
        assert rows["won"].settled_at is not None
        assert (rows["future"].status, rows["future"].payout) == ("open", None)
        assert (await repo.get(voided)).bet.status == "void"
        assert await settle_final(factory, AFTER + timedelta(hours=1)) == 0  # nothing left to grade

    asyncio.run(scenario())


def test_the_pre_kick_snapshot_becomes_the_closing_line(tmp_path) -> None:
    async def scenario() -> None:
        factory, _, repo, game_id, future_id = await _setup(tmp_path)
        await _snapshot(factory, tmp_path / "raw", KICKOFF - timedelta(minutes=20))
        spread = await repo.create(_bet(game_id, line=2.5), AFTER)  # DEN +2.5, close DEN +1.5
        under = await repo.create(_bet(game_id, market="total", selection="under", line=42.5),
                                  AFTER)
        moneyline = await repo.create(_bet(game_id, market="moneyline", selection="home",
                                           line=None, price_american=105), AFTER)
        future = await repo.create(_bet(future_id), AFTER)  # has not kicked off at AFTER

        assert await fill_clv(factory, AFTER) == 3
        bet = (await repo.get(spread)).bet
        assert (bet.close_source, bet.close_line, bet.clv_points) == ("odds_api", 1.5, 1.0)
        # dk .5, fd .49568, rivers .48916, espn .5 cover at +1.5 -> no-vig close .49621
        assert bet.clv_prob == pytest.approx(0.49621 - 110 / 210, abs=1e-4)
        assert bet.close_price == fair_american(0.49621)
        total = (await repo.get(under)).bet
        assert (total.close_line, total.clv_points) == (42.5, 0.0)  # took the closing number
        assert total.clv_prob == pytest.approx((1 - 0.49837) - 110 / 210, abs=1e-3)
        ml = (await repo.get(moneyline)).bet
        assert ml.clv_points is None and ml.close_line is None
        assert ml.clv_prob == pytest.approx(0.4693 - 100 / 205, abs=2e-3)
        assert (await repo.get(future)).bet.close_source is None
        assert await fill_clv(factory, AFTER) == 0  # a close is copied once and then kept

    asyncio.run(scenario())


def test_without_a_fresh_snapshot_espns_close_is_used_when_it_exists(tmp_path) -> None:
    async def scenario() -> None:
        factory, games, repo, game_id, _ = await _setup(tmp_path)
        morning = KICKOFF - timedelta(hours=3)  # too early to be a close
        await _snapshot(factory, tmp_path / "raw", morning)
        bet_id = await repo.create(_bet(game_id, line=2.5), AFTER)
        assert await fill_clv(factory, AFTER) == 0  # nothing yet: wait for ESPN rather than guess
        close = LineRecord("close", spread_home=1.0, spread_home_price=-110,
                           spread_away_price=-110, total=43.5, over_price=-110, under_price=-110)
        await games.save_lines("401872995", "espn", [close], AFTER)
        assert await fill_clv(factory, AFTER + timedelta(hours=1)) == 1
        bet = (await repo.get(bet_id)).bet
        assert (bet.close_source, bet.close_line, bet.clv_points) == ("espn", 1.0, 1.5)
        assert bet.clv_prob == pytest.approx(0.5 - 110 / 210)

    asyncio.run(scenario())


def test_the_job_settles_and_fills_and_is_registered(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory, games, repo, game_id, _ = await _setup(tmp_path)
        final = replace(next(r for r in _records() if r.espn_id == "401872995"),
                        status="final", home_score=24, away_score=20,
                        lines=(LineRecord("close", spread_home=-3.0, spread_home_price=-110,
                                          spread_away_price=-110),))
        await games.upsert_games("nfl", "espn", [final], AFTER)
        bet_id = await repo.create(_bet(game_id), AFTER)
        monkeypatch.setattr(bets_job, "_now", lambda: AFTER + timedelta(hours=3))
        ctx = JobContext(factory, None, tmp_path)
        slot = Slot("grade_bets:x", AFTER, AFTER)
        assert await GradeBetsJob().run(ctx, slot) == "done"
        bet = (await repo.get(bet_id)).bet
        assert (bet.status, bet.close_source, bet.clv_points) == ("won", "espn", 0.5)
        due = datetime(2026, 10, 16, 9, 0, tzinfo=CENTRAL)
        assert await run_due([GradeBetsJob()], ctx, due) == {
            "grade_bets:2026-10-16T08": "missed",  # the first hour of the day had no run
            "grade_bets:2026-10-16T09": "done",
        }

    asyncio.run(scenario())
    assert any(isinstance(job, GradeBetsJob) for job in JOBS)
