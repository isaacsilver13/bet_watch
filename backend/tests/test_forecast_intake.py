"""The forecast handoff is private, immutable, and consumed by model_picks."""

import asyncio
from datetime import date, timedelta

import httpx
from app.analytics.market import SideOffer
from app.api.slate import get_now, get_session_factory
from app.domain.forecasts import Predictions
from app.main import app
from app.storage.forecast_repository import get_forecast, store_forecast
from app.storage.models import Game, NbaForecast, Team
from sqlalchemy import select, update

from tests.matchup_helpers import new_session_factory
from tests.test_model_picks import NOW, PREDICTIONS, TIP, _setup


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_quote_coverage_requires_synchronized_two_sided_main_market() -> None:
    from app.jobs.model_picks import _has_priceable_main_market

    def offer(market, side, line=None, minute=0):
        captured = NOW - timedelta(minutes=5 - minute)
        return SideOffer("fanduel", market, side, line, -110, captured, captured)

    def covered(offers):
        return _has_priceable_main_market(offers, NOW)

    assert not covered([offer("total", "over"), offer("total", "under")])
    assert not covered([offer("moneyline", "home")])
    assert not covered([
        offer("moneyline", "home", minute=1), offer("moneyline", "away")
    ])
    assert not covered([
        offer("spread", "home", -4.5), offer("spread", "away", 3.5)
    ])
    assert covered([
        offer("moneyline", "home"), offer("moneyline", "away")
    ])
    assert covered([
        offer("spread", "home", -4.5), offer("spread", "away", 4.5)
    ])


def test_remote_delivery_requires_secret_and_is_idempotent(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch, predictions=None)
        factory = ctx.session_factory
        app.dependency_overrides[get_session_factory] = lambda: factory
        app.dependency_overrides[get_now] = lambda: NOW
        monkeypatch.setenv("NBA_PREDICTION_INGEST_SECRET", "test-ingest-secret")
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 50000))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://example.test"
        ) as client:
            path = "/api/v1/internal/nba-predictions"
            assert (await client.post(path, json=PREDICTIONS)).status_code == 403
            headers = {"X-Internal-Secret": "test-ingest-secret"}
            app.dependency_overrides[get_now] = lambda: TIP + timedelta(minutes=1)
            assert (await client.post(path, json=PREDICTIONS, headers=headers)).status_code == 422
            app.dependency_overrides[get_now] = lambda: NOW
            bad = {**PREDICTIONS, "games": [{**PREDICTIONS["games"][0], "sigma": -1}]}
            assert (await client.post(path, json=bad, headers=headers)).status_code == 422
            wrong_game = {**PREDICTIONS, "games": [
                {**PREDICTIONS["games"][0], "home": "BOS", "away": "LAL"}
            ]}
            assert (await client.post(path, json=wrong_game, headers=headers)).status_code == 422
            future = {**PREDICTIONS, "generated_at": "2026-01-15T18:00:00+00:00"}
            assert (await client.post(path, json=future, headers=headers)).status_code == 422
            stale = {**PREDICTIONS, "data_through": "2025-12-15"}
            assert (await client.post(path, json=stale, headers=headers)).status_code == 422
            first = await client.post(path, json=PREDICTIONS, headers=headers)
            app.dependency_overrides[get_now] = lambda: TIP + timedelta(minutes=1)
            again = await client.post(path, json=PREDICTIONS, headers=headers)
            app.dependency_overrides[get_now] = lambda: NOW
            assert (first.status_code, first.json()["stored"]) == (201, True)
            assert (again.status_code, again.json()["stored"]) == (200, False)
            changed = {**PREDICTIONS, "model_version": "changed"}
            assert (await client.post(path, json=changed, headers=headers)).status_code == 409
        saved = await get_forecast(factory, date(2026, 1, 15))
        assert saved is not None and saved.model_version == PREDICTIONS["model_version"]
        async with factory() as session:
            rows = (await session.scalars(select(NbaForecast))).all()
            assert len(rows) == 1 and rows[0].schema_version == 1

    asyncio.run(scenario())


def test_partial_quote_coverage_waits_without_storing_picks(tmp_path, monkeypatch) -> None:
    from app.jobs.model_picks import ModelPicksJob

    from tests.test_model_picks import _picks

    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch, predictions=None)
        async with ctx.session_factory() as session, session.begin():
            bos = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "BOS"))
            lal = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "LAL"))
            session.add(Game(
                sport="nba", season=2026, season_type="REG", week=None,
                start_time=TIP + timedelta(hours=1), home_team_id=bos.id,
                away_team_id=lal.id, neutral=False, status="scheduled", espn_id="test-second",
                updated_at=NOW,
            ))
        payload = {**PREDICTIONS, "games": [
            *PREDICTIONS["games"],
            {**PREDICTIONS["games"][0], "home": "BOS", "away": "LAL"},
        ]}
        await store_forecast(ctx.session_factory, Predictions.model_validate(payload))
        assert await ModelPicksJob().run(ctx, None) == "waiting"
        assert await _picks(ctx) == [] and sent == []

    asyncio.run(scenario())


def test_model_job_reads_delivered_forecast_without_local_file(tmp_path, monkeypatch) -> None:
    from app.jobs.model_picks import ModelPicksJob
    from app.jobs.runner import run_due, update_slot

    from tests.test_model_picks import ODDS_OPEN, SLOT, _picks

    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch, predictions=None)
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="done")
        job = ModelPicksJob()
        for n in range(9):
            assert await run_due([job], ctx, NOW + timedelta(minutes=15 * n)) == {
                SLOT: "waiting"
            }
        await store_forecast(ctx.session_factory, Predictions.model_validate(PREDICTIONS))
        assert await run_due([job], ctx, NOW + timedelta(minutes=135)) == {SLOT: "done"}
        assert len(await _picks(ctx)) == 2 and len(sent) == 1

    asyncio.run(scenario())


def test_unconfigured_intake_fails_closed(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        app.dependency_overrides[get_session_factory] = lambda: factory
        monkeypatch.delenv("NBA_PREDICTION_INGEST_SECRET", raising=False)
        monkeypatch.setattr("app.config._DOTENV_PATH", tmp_path / "absent.env")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.5", 50000)),
            base_url="https://example.test",
        ) as client:
            response = await client.post(
                "/api/v1/internal/nba-predictions", json=PREDICTIONS,
                headers={"X-Internal-Secret": "anything"},
            )
            assert response.status_code == 503

    asyncio.run(scenario())


def test_missing_schedule_waits_until_a_successful_scoreboard_run(tmp_path, monkeypatch) -> None:
    from app.jobs.model_picks import ModelPicksJob
    from app.jobs.runner import update_slot

    async def scenario() -> None:
        ctx, sent = await _setup(tmp_path, monkeypatch, predictions=None)
        async with ctx.session_factory() as session, session.begin():
            await session.execute(
                update(Game).where(Game.sport == "nba").values(status="postponed")
            )
        job = ModelPicksJob()
        assert await job.run(ctx, None) == "waiting"
        await update_slot(
            ctx, "nba_scoreboard", "nba_scoreboard:2026-01-15T10", state="done"
        )
        assert await job.run(ctx, None) == "empty"
        assert sent == []

    asyncio.run(scenario())


def test_waiting_forecast_slot_expires_after_midnight(tmp_path, monkeypatch) -> None:
    from app.jobs.model_picks import ModelPicksJob
    from app.jobs.runner import run_due, update_slot

    from tests.test_model_picks import ODDS_OPEN, SLOT

    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch, predictions=None)
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="done")
        job = ModelPicksJob()
        assert await run_due([job], ctx, NOW) == {SLOT: "waiting"}
        after_midnight = NOW + timedelta(hours=14)
        result = await run_due([job], ctx, after_midnight)
        assert result == {SLOT: "missed"}
        assert result.had_failures

    asyncio.run(scenario())
