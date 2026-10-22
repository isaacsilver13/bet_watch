import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from app import config
from app.analytics.market import BookQuote, consensus
from app.analytics.picks import (
    PickFacts,
    expected_value,
    game_picks,
    home_cover_prob,
    kelly,
    track_record,
)
from app.analytics.pricing import implied_prob, novig
from app.api.slate import get_session_factory
from app.config import load_settings
from app.domain.models import Sport
from app.ingest.espn import parse_scoreboard
from app.ingest.odds_raw import write_raw
from app.ingest.odds_snapshot import ingest_snapshot
from app.ingest.records import LineRecord
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import model_picks as picks_job
from app.jobs.__main__ import JOBS
from app.jobs.model_picks import ModelPicksJob, push_report
from app.jobs.runner import JobContext, run_due, update_slot
from app.jobs.slots import CENTRAL
from app.main import app
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import ModelPick
from app.storage.settlement import grade_model_picks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.local_client import TestClient
from tests.matchup_helpers import db_url, load_json, new_session_factory

SEEDED = datetime(2026, 1, 14, 15, tzinfo=timezone.utc)
SNAPSHOT = datetime(2026, 1, 15, 10, 0, tzinfo=CENTRAL).astimezone(timezone.utc)
NOW = datetime(2026, 1, 15, 10, 30, tzinfo=CENTRAL).astimezone(timezone.utc)
TIP = datetime(2026, 1, 16, 3, 0, tzinfo=timezone.utc)  # NY at GS, 9pm Central
ODDS_OPEN = "odds_snapshot_nba:nba:2026-01-15:open"
SLOT = "model_picks:2026-01-15"
ON = date(2026, 1, 15)
PREDICTIONS = {
    "slate_date": "2026-01-15", "model_version": "margin-noline-test",
    "generated_at": "2026-01-15T15:30:00+00:00", "data_through": "2026-01-14",
    "games": [
        {"game_date": "2026-01-15", "home": "GSW", "away": "NYK", "pred_margin": 10.0,
         "p_home_win": 0.80, "sigma": 14.0},
        {"game_date": "2026-01-15", "home": "BOS", "away": "LAL", "pred_margin": 2.0,
         "p_home_win": 0.55, "sigma": 14.0},  # no such game in bet_watch
    ],
}


def teardown_function() -> None:
    app.dependency_overrides.clear()


def quote(book, **prices) -> BookQuote:
    return BookQuote(book=book, observed_at=SNAPSHOT, **prices)


# --- pricing ---------------------------------------------------------------------------------


def test_fanduel_charges_its_fee_below_25_and_waives_it_from_25() -> None:
    fd = [quote("fanduel", ml_home=-110, ml_away=-110)]
    market = consensus(fd)
    fair = 0.6 * (1 + 100 / 110) - 1
    at = {stake: game_picks(0, 0.6, 14, fd, market, ON, stake, 0.3, 0.08)[0].ev
          for stake in (20.0, 24.99, 25.0)}
    assert at[20.0] == pytest.approx(fair - 0.5 / 20)
    assert at[24.99] == pytest.approx(fair - 0.5 / 24.99)
    assert at[25.0] == pytest.approx(fair)


def test_a_fee_can_sink_a_moneyline_under_the_targeted_bar() -> None:
    # 55% at -110 is 5.0% EV; a $0.50 fee on $20 takes 2.5% of it.
    fd = [quote("fanduel", ml_home=-110, ml_away=-110)]
    assert game_picks(0, 0.55, 14, fd, consensus(fd), ON, 20.0, 0.3, 0.08) == []
    assert len(game_picks(0, 0.55, 14, fd, consensus(fd), ON, 25.0, 0.3, 0.08)) == 1


def test_draftkings_waives_its_fee_only_from_50() -> None:
    dk = [quote("draftkings", ml_home=-110, ml_away=-110)]
    market = consensus(dk)
    fair = 0.6 * (1 + 100 / 110) - 1
    assert game_picks(0, 0.6, 14, dk, market, ON, 25.0, 0.3, 0.08)[0].ev == pytest.approx(
        fair - 0.5 / 25)
    assert game_picks(0, 0.6, 14, dk, market, ON, 49.99, 0.3, 0.08)[0].ev < fair
    assert game_picks(0, 0.6, 14, dk, market, ON, 50.0, 0.3, 0.08)[0].ev == pytest.approx(fair)


def test_fees_only_apply_from_their_effective_date() -> None:
    dk = [quote("draftkings", ml_home=-110, ml_away=-110)]
    before = game_picks(0, 0.6, 14, dk, consensus(dk), date(2025, 6, 30), 10.0, 0.3, 0.08)
    assert before[0].ev == pytest.approx(0.6 * (1 + 100 / 110) - 1)


def test_fees_are_found_by_venue_from_the_feeds_book_title() -> None:
    title = [quote("FanDuel", ml_home=-110, ml_away=-110)]
    (pick,) = game_picks(0, 0.6, 14, title, consensus(title), ON, 20.0, 0.3, 0.08)
    assert pick.book == "fanduel"
    assert pick.ev == pytest.approx(0.6 * (1 + 100 / 110) - 1 - 0.5 / 20)


def test_the_book_worth_most_after_its_fee_wins_over_the_best_raw_price() -> None:
    # DraftKings has the better number, but at a $10 stake its $0.50 fee costs 5%.
    quotes = [quote("draftkings", ml_home=-105, ml_away=-115),
              quote("betmgm", ml_home=-110, ml_away=-110)]
    picks = game_picks(0, 0.6, 14, quotes, consensus(quotes), ON, 10.0, 0.3, 0.08)
    assert [(p.market, p.side, p.book, p.price_american) for p in picks] == [
        ("moneyline", "home", "betmgm", -110)]


def test_kelly_is_the_fractional_textbook_kelly_and_is_capped() -> None:
    ev = expected_value(0.55, 120, fee=0.0, stake=25.0)
    b = 1.2
    assert kelly(ev, 120, 0.30, 0.08) == pytest.approx(0.30 * (0.55 * (b + 1) - 1) / b)
    assert kelly(expected_value(0.9, 120, 0.0, 25.0), 120, 0.30, 0.08) == 0.08
    assert kelly(-0.05, 120, 0.30, 0.08) == 0.0


def test_moneylines_over_3pct_are_targeted_and_spreads_are_a_watchlist() -> None:
    quotes = [
        quote("betrivers", ml_home=-150, ml_away=130, spread_home=-3.5,
              spread_home_price=-110, spread_away_price=-110),
        quote("betmgm", ml_home=-155, ml_away=135, spread_home=-4.5,  # off the consensus line
              spread_home_price=150, spread_away_price=-200),
        quote("espnbet", ml_home=-150, ml_away=130, spread_home=-3.5,
              spread_home_price=-105, spread_away_price=-115),
    ]
    market = consensus(quotes)
    # 62% at -150 is 3.3% EV; the cover at -3.5 with a 6 point edge is 57%.
    picks = game_picks(6.0, 0.62, 14.0, quotes, market, ON, 25.0, 0.3, 0.08)
    ml, spread = picks
    assert (ml.tier, ml.side, ml.book, ml.price_american) == ("targeted", "home", "betrivers", -150)
    assert ml.ev == pytest.approx(0.62 * (1 + 100 / 150) - 1)
    assert ml.market_prob_novig == pytest.approx(market.home_win_prob)
    cover = home_cover_prob(6.0, 14.0, -3.5)
    assert cover == pytest.approx(0.5709, abs=1e-4)  # Phi(2.5 / 14)
    assert (spread.tier, spread.side, spread.line, spread.book) == (
        "watchlist", "home", -3.5, "espnbet")
    assert spread.model_prob == pytest.approx(cover)
    assert spread.market_prob_novig == pytest.approx(
        (novig(implied_prob(-110), implied_prob(-110))[0]
         + novig(implied_prob(-105), implied_prob(-115))[0]) / 2)
    # 60% on the moneyline is under 3% EV at -150: no targeted pick.
    assert [p.tier for p in game_picks(6.0, 0.60, 14.0, quotes, market, ON, 25.0, 0.3, 0.08)] \
        == ["watchlist"]


def test_the_track_record_counts_results_flat_roi_and_clv_per_tier() -> None:
    rows = track_record([
        PickFacts("targeted", 150, "won", 0.04),
        PickFacts("targeted", -110, "lost", -0.01),
        PickFacts("targeted", -110, None, None),
        PickFacts("watchlist", -110, "push", 0.0),
    ])
    targeted, watchlist = rows
    assert (targeted.picks, targeted.graded, targeted.won, targeted.lost) == (3, 2, 1, 1)
    assert targeted.flat_roi == pytest.approx((1.5 - 1) / 2)
    assert targeted.avg_clv == pytest.approx(0.015) and targeted.beat_close_pct == 50
    assert (watchlist.push, watchlist.flat_roi, watchlist.beat_close_pct) == (1, 0.0, 0)


# --- the job ---------------------------------------------------------------------------------


async def _setup(tmp_path, monkeypatch, responses=(200,), predictions=PREDICTIONS):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
    await GamesRepository(factory).upsert_games("nba", "espn", records, SEEDED)
    body = json.dumps(load_json("the_odds_api/nba_jan15.json"))
    raw = write_raw(tmp_path / "raw", "the_odds_api", ODDS_OPEN, SNAPSHOT, body)
    await ingest_snapshot(factory, raw, (SNAPSHOT, TIP + timedelta(hours=6)), 450, Sport.NBA)
    preds_dir = tmp_path / "predictions"
    preds_dir.mkdir()
    if predictions is not None:
        (preds_dir / "2026-01-15.json").write_text(json.dumps(predictions), encoding="utf-8")
    sent: list[httpx.Request] = []
    codes = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(codes.pop(0) if len(codes) > 1 else codes[0])

    settings = replace(
        load_settings(), nba_predictions_dir=str(preds_dir),
        aisaac_dashboard_url="https://aisaac.example/", aisaac_internal_secret="test-secret",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(picks_job, "_now", lambda: NOW)
    return JobContext(factory, http, tmp_path / "raw", settings=settings), sent


async def _picks(ctx) -> list[ModelPick]:
    async with ctx.session_factory() as session:
        return list((await session.scalars(select(ModelPick).order_by(ModelPick.id))).all())


def test_the_job_waits_for_todays_nba_odds_snapshot_to_finish(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch)
        job = ModelPicksJob()
        assert await run_due([job], ctx, NOW) == {}  # the snapshot has not run yet
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="raw_saved")
        assert await run_due([job], ctx, NOW) == {}  # paid for but not parsed yet
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="done")
        early = datetime(2026, 1, 15, 10, 10, tzinfo=CENTRAL)
        assert await run_due([job], ctx, early) == {}  # the slot opens at 10:15
        assert sent == [] and await _picks(ctx) == []
        assert await run_due([job], ctx, NOW) == {SLOT: "done"}
        assert len(sent) == 1
        assert await run_due([job], ctx, NOW + timedelta(minutes=15)) == {}  # done is final

    asyncio.run(scenario())


def test_picks_are_priced_stored_and_reported_without_stakes(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch)
        assert await ModelPicksJob().run(ctx, None) == "done"
        picks = await _picks(ctx)
        # GSW 80% at BetRivers -295 (no fee) beats DraftKings -300 less its $0.50 fee at $25.
        assert [(p.tier, p.market, p.side, p.line, p.book, p.price_american) for p in picks] == [
            ("targeted", "moneyline", "home", None, "betrivers", -295),
            ("watchlist", "spread", "home", -7.5, "betrivers", -105),  # BetMGM's -8 is off-line
        ]
        ml = picks[0]
        assert ml.ev == pytest.approx(0.8 * (1 + 100 / 295) - 1)
        assert ml.kelly_frac == pytest.approx(0.3 * ml.ev / (100 / 295))
        assert (ml.slate_date, ml.model_version) == (ON, "margin-noline-test")

        (request,) = sent
        assert str(request.url) == "https://aisaac.example/internal/nba-report"
        assert request.headers["X-Internal-Secret"] == "test-secret"
        report = json.loads(request.content)
        assert set(report) == {"kind", "slate_date", "generated_at", "model_version",
                               "data_through", "odds_as_of", "games", "picks", "track_record"}
        assert (report["kind"], report["slate_date"]) == ("nba_daily_report", "2026-01-15")
        assert report["odds_as_of"] == SNAPSHOT.isoformat()
        gsw, bos = report["games"]
        assert (gsw["home"], gsw["tip"], gsw["market_spread_home"], gsw["books"]) == (
            "GSW", TIP.isoformat(), -7.5, 5)
        assert bos["game_id"] is None  # predicted, but not on bet_watch's schedule
        first = report["picks"][0]
        assert (first["tier"], first["team"], first["book"], first["price_american"]) == (
            "targeted", "GSW", "betrivers", -295)
        assert report["track_record"][0]["tier"] == "targeted"
        assert report["track_record"][0]["picks"] == 1
        assert not [key for key in json.dumps(report).split('"') if "stake" in key or "$" in key]

    asyncio.run(scenario())


def test_no_predictions_file_means_no_picks_and_no_push(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch, predictions=None)
        assert await ModelPicksJob().run(ctx, None) == "empty"
        assert await _picks(ctx) == [] and sent == []

    asyncio.run(scenario())


def test_a_failed_push_keeps_the_picks_and_the_next_tick_only_retries_the_push(
    tmp_path, monkeypatch
) -> None:
    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch, responses=(503, 200))
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="done")
        job = ModelPicksJob()
        assert await run_due([job], ctx, NOW) == {SLOT: "pending"}
        first = [(p.id, p.price_american) for p in await _picks(ctx)]
        assert len(first) == 2 and len(sent) == 1
        # Prices moved since; the retry re-sends the picks already priced, not new ones.
        async with ctx.session_factory() as session, session.begin():
            for pick in (await session.scalars(select(ModelPick))).all():
                pick.price_american = -290
        assert await run_due([job], ctx, NOW + timedelta(minutes=15)) == {SLOT: "done"}
        assert [p.id for p in await _picks(ctx)] == [pid for pid, _ in first]
        assert json.loads(sent[1].content)["picks"][0]["price_american"] == -290

    asyncio.run(scenario())


def test_the_push_needs_https_or_loopback_and_never_raises(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch)
        settings = ctx.settings
        for url in (None, "http://aisaac.example", "ftp://127.0.0.1"):
            assert not await push_report(ctx.http, replace(settings, aisaac_dashboard_url=url), {})
        assert not await push_report(ctx.http, replace(settings, aisaac_internal_secret=None), {})
        assert sent == []
        assert await push_report(
            ctx.http, replace(settings, aisaac_dashboard_url="http://127.0.0.1:8001"), {})

        def down(request):
            raise httpx.ConnectError("refused")

        broken = httpx.AsyncClient(transport=httpx.MockTransport(down))
        assert not await push_report(broken, settings, {})

    asyncio.run(scenario())


def test_the_push_target_falls_back_to_the_shared_aisaac_push_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "aisaac_push.json"
    path.write_text(json.dumps({"AISAAC_DASHBOARD_URL": "https://from-file.example",
                                "AISAAC_INTERNAL_SECRET": "file-secret"}), encoding="utf-8")
    monkeypatch.setattr(config, "_PUSH_CONFIG_PATH", path)
    monkeypatch.delenv("AISAAC_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("AISAAC_INTERNAL_SECRET", raising=False)
    settings = load_settings()
    assert (settings.aisaac_dashboard_url, settings.aisaac_internal_secret) == (
        "https://from-file.example", "file-secret")
    monkeypatch.setenv("AISAAC_DASHBOARD_URL", "https://from-env.example")
    assert load_settings().aisaac_dashboard_url == "https://from-env.example"


def test_the_job_is_registered_after_the_nba_snapshot() -> None:
    names = [job.name for job in JOBS]
    assert names.index("odds_snapshot_nba") < names.index("model_picks") < names.index(
        "grade_bets")


# --- grading ---------------------------------------------------------------------------------


def test_grading_copies_the_close_onto_model_picks_and_computes_clv(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        await ModelPicksJob().run(ctx, None)
        assert await grade_model_picks(ctx.session_factory, NOW) == (0, 0)  # not tipped yet
        # GSW won by 12: -7.5 covers. The 10:00 snapshot is no close, so ESPN's close is used.
        final = replace(
            next(r for r in parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
                 if r.espn_id == "401810439"),
            status="final", home_score=120, away_score=108,
            lines=(LineRecord("close", spread_home=-8.5, spread_home_price=-110,
                              spread_away_price=-110, ml_home=-320, ml_away=260),),
        )
        after = TIP + timedelta(hours=3)
        await GamesRepository(ctx.session_factory).upsert_games("nba", "espn", [final], after)
        assert await grade_model_picks(ctx.session_factory, after) == (2, 2)
        ml, spread = await _picks(ctx)
        close_home = novig(implied_prob(-320), implied_prob(260))[0]
        assert (ml.result, ml.close_source, ml.close_line, ml.clv_points) == (
            "won", "espn", None, None)
        assert ml.clv == pytest.approx(close_home / implied_prob(-295) - 1)
        assert ml.clv < 0  # -295 implies more than the no-vig close of -320 / +260
        assert (spread.result, spread.close_line, spread.clv_points) == ("won", -8.5, 1.0)
        assert spread.clv == pytest.approx(0.5 / implied_prob(-105) - 1)
        assert await grade_model_picks(ctx.session_factory, after) == (0, 0)  # copied once

        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        app.dependency_overrides[get_session_factory] = lambda: factory
        body = TestClient(app).get("/api/v1/model-picks").json()
        targeted = body["record"][0]
        assert (targeted["tier"], targeted["won"], targeted["clv_samples"]) == ("targeted", 1, 1)
        assert targeted["flat_roi"] == pytest.approx(100 / 295)
        assert [(p["tier"], p["game"]["home"], p["result"]) for p in body["picks"]] == [
            ("watchlist", "GSW", "won"), ("targeted", "GSW", "won")]

    asyncio.run(scenario())
