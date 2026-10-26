import asyncio
from copy import deepcopy
from datetime import date, timedelta

import httpx
import pytest
from app.api.slate import get_session_factory
from app.domain.forecasts import Predictions
from app.jobs import paper_ledger as paper_job
from app.jobs.model_picks import ModelPicksJob
from app.jobs.paper_ledger import PaperLedgerJob
from app.jobs.runner import run_due, update_slot
from app.main import app
from app.storage.forecast_repository import store_forecast
from app.storage.models import (
    Game,
    NbaForecast,
    NbaReport,
    OfferObservation,
    PaperAllocationBatch,
    PaperSettlement,
    PaperWager,
)
from app.storage.paper_ledger import (
    _report_hash,
    paper_balances,
    paper_day,
    paper_status,
    record_paper_day,
    settle_finished_paper,
)
from app.storage.repository import OddsRepository
from sqlalchemy import func, select

from tests.test_model_picks import NOW, ODDS_OPEN, ON, PREDICTIONS, TIP, _setup


def test_paper_ledger_freezes_decisions_rolls_cash_and_settles_once(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        factory = ctx.session_factory
        await store_forecast(factory, Predictions.model_validate(PREDICTIONS))
        assert await ModelPicksJob().run(ctx, None) == "done"
        first, concurrent = await asyncio.gather(
            record_paper_day(factory, ON, NOW), record_paper_day(factory, ON, NOW)
        )
        assert len(first) == 11
        assert {batch.id for batch in first} == {batch.id for batch in concurrent}
        assert {batch.profile for batch in first} == {
            "all", *(f"top_{n}" for n in range(1, 11))
        }
        assert all(batch.opening_cash == 1000 for batch in first)
        assert [batch.id for batch in await record_paper_day(factory, ON, NOW)] == [
            batch.id for batch in sorted(first, key=lambda row: row.profile)
        ]
        async with factory() as session:
            wagers = (await session.scalars(select(PaperWager))).all()
            assert len(wagers) == 11
            quote_id = wagers[0].quote_observation_id
            assert all(wager.quote_observation_id == quote_id for wager in wagers)
            stake = wagers[0].stake
            assert stake > 0
        before = await paper_balances(factory)
        assert all(value == pytest.approx(1000 - stake) for value in before.values())
        before_status = await paper_status(factory)
        assert len(before_status["profiles"]) == 11
        assert all(row["open_bets"] == 1 and row["settled_bets"] == 0
                   for row in before_status["profiles"])
        open_day = await paper_day(factory, ON)
        assert open_day is not None and len(open_day["profiles"]) == 11
        assert open_day["report_matches_recorded"]
        assert open_day["forecast_matches_recorded"]
        assert all(len(profile["bets"]) == 1 for profile in open_day["profiles"])
        open_bet = open_day["profiles"][0]["bets"][0]
        assert open_bet["quote_observation_id"] == quote_id
        assert open_bet["quote_observed_at"] and open_bet["result"] is None
        previous_override = app.dependency_overrides.get(get_session_factory)
        try:
            app.dependency_overrides[get_session_factory] = lambda: factory
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
                base_url="http://localhost",
            ) as client:
                response = await client.get(f"/api/v1/nba/paper-ledger/slates/{ON}")
                assert response.status_code == 200
                assert response.json()["report_matches_recorded"]
                assert response.json()["profiles"][0]["bets"][0]["quote_observation_id"] == quote_id
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_session_factory, None)
            else:
                app.dependency_overrides[get_session_factory] = previous_override

        # Expiry retains quotes referenced by frozen paper decisions.
        await OddsRepository(factory).prune_expired(1, NOW + timedelta(days=3))
        async with factory() as session:
            assert await session.get(OfferObservation, quote_id) is not None
            game = await session.get(Game, wagers[0].game_id)
            game.status = "final"
            game.home_score = 112
            game.away_score = 100
            game.updated_at = NOW + timedelta(days=1)
            await session.commit()
        settlements = await asyncio.gather(
            settle_finished_paper(factory, NOW + timedelta(days=1)),
            settle_finished_paper(factory, NOW + timedelta(days=1)),
        )
        assert sorted(settlements) == [0, 11]
        async with factory() as session:
            settlements_saved = (await session.scalars(select(PaperSettlement))).all()
            assert len(settlements_saved) == 11
            assert all((item.home_score, item.away_score) == (112, 100)
                       for item in settlements_saved)
        after = await paper_balances(factory)
        assert all(value > 1000 for value in after.values())
        after_status = await paper_status(factory)
        assert all(row["open_bets"] == 0 and row["settled_bets"] == 1
                   and row["settled_net"] > 0 and row["settled_roi"] > 0
                   for row in after_status["profiles"])
        settled_day = await paper_day(factory, ON)
        settled_bet = settled_day["profiles"][0]["bets"][0]
        assert settled_bet["result"] == "won"
        assert settled_bet["home_score"] == 112 and settled_bet["away_score"] == 100
        assert settled_bet["net_return"] == round(settled_bet["payout"] - stake, 2)

        second_day = ON + timedelta(days=1)
        next_forecast = deepcopy(PREDICTIONS)
        next_forecast.update(slate_date=second_day.isoformat(),
                             generated_at="2026-01-16T15:30:00+00:00",
                             data_through=ON.isoformat())
        next_forecast["games"][0]["game_date"] = second_day.isoformat()
        forecast_hash = _report_hash(
            Predictions.model_validate(next_forecast).model_dump(mode="json")
        )
        async with factory() as session, session.begin():
            prior = await session.get(NbaReport, ON)
            payload = deepcopy(prior.payload)
            payload.update(slate_date=second_day.isoformat(), forecast_sha256=forecast_hash,
                           generated_at="2026-01-16T16:30:00+00:00", picks=[])
            session.add(NbaReport(slate_date=second_day, created_at=NOW,
                                  payload=payload))
            session.add(NbaForecast(
                slate_date=second_day, schema_version=1, model_version="test",
                generated_at=NOW, data_through=ON, received_at=NOW,
                payload_sha256=forecast_hash, payload=next_forecast,
            ))
        next_batches = await record_paper_day(factory, second_day, NOW + timedelta(days=1))
        assert len(next_batches) == 11
        assert all(batch.opening_cash == after[batch.profile] for batch in next_batches)
        assert all(not batch.allocation["bets"] for batch in next_batches)
        empty_day = await paper_day(factory, second_day)
        assert empty_day is not None and all(not profile["bets"]
                                              for profile in empty_day["profiles"])
        next_status = await paper_status(factory)
        assert all(row["recorded_days"] == 2
                   and row["last_slate_date"] == second_day.isoformat()
                   and row["cash"] == round(after[row["profile"]], 2)
                   for row in next_status["profiles"])
        with pytest.raises(ValueError, match="backfill"):
            await record_paper_day(factory, date(2026, 1, 14), NOW)

        # A fee-bearing archived row still reports its net return after the fee.
        async with factory() as session, session.begin():
            wager = await session.get(PaperWager, wagers[0].id)
            wager.fee = 1.25
        fee_day = await paper_day(factory, ON)
        fee_bet = fee_day["profiles"][0]["bets"][0]
        assert fee_bet["net_return"] == round(fee_bet["payout"] - stake - 1.25, 2)

        # A later report correction must be visible beside the frozen decision.
        async with factory() as session, session.begin():
            report = await session.get(NbaReport, ON)
            changed = deepcopy(report.payload)
            changed["audit_test_marker"] = True
            report.payload = changed
        corrected_day = await paper_day(factory, ON)
        assert not corrected_day["report_matches_recorded"]
        assert corrected_day["forecast_matches_recorded"]

    asyncio.run(scenario())


def test_paper_ledger_rejects_unverified_forecast_and_quote(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        factory = ctx.session_factory
        await store_forecast(factory, Predictions.model_validate(PREDICTIONS))
        assert await ModelPicksJob().run(ctx, None) == "done"
        with pytest.raises(ValueError, match="pregame"):
            await record_paper_day(factory, ON, TIP + timedelta(minutes=1))
        async with factory() as session, session.begin():
            row = await session.get(NbaReport, ON)
            original = deepcopy(row.payload)
            changed = deepcopy(row.payload)
            changed["picks"][0]["quote_observation_id"] = 999999
            row.payload = changed
        with pytest.raises(ValueError, match="selected pregame quote"):
            await record_paper_day(factory, ON, NOW)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(PaperAllocationBatch)) == 0
            row = await session.get(NbaReport, ON)
            changed = deepcopy(original)
            changed["picks"][0]["paper_win_prob"] = 0.99
            row.payload = changed
            await session.commit()
        with pytest.raises(ValueError, match="probability differs"):
            await record_paper_day(factory, ON, NOW)
        async with factory() as session, session.begin():
            row = await session.get(NbaReport, ON)
            quote = await session.get(
                OfferObservation, original["picks"][0]["quote_observation_id"]
            )
            stale = OfferObservation(
                event_id=quote.event_id, provider=quote.provider,
                bookmaker=quote.bookmaker, market_type=quote.market_type,
                selection=quote.selection, line=quote.line,
                price_american=quote.price_american,
                price_decimal=quote.price_decimal, player_name=quote.player_name,
                provider_offer_id=quote.provider_offer_id,
                provider_updated_at=quote.provider_updated_at - timedelta(hours=2),
                observed_at=quote.observed_at - timedelta(hours=2),
                deep_link=quote.deep_link, status=quote.status,
            )
            session.add(stale)
            await session.flush()
            changed = deepcopy(original)
            changed["picks"][0]["quote_observation_id"] = stale.id
            row.payload = changed
        with pytest.raises(ValueError, match="selected pregame quote"):
            await record_paper_day(factory, ON, NOW)
        async with factory() as session:
            row = await session.get(NbaReport, ON)
            changed = deepcopy(original)
            changed["forecast_sha256"] = "c" * 64
            row.payload = changed
            await session.commit()
        with pytest.raises(ValueError, match="durable forecast"):
            await record_paper_day(factory, ON, NOW)

    asyncio.run(scenario())


def test_paper_job_records_only_before_first_tip(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        await store_forecast(ctx.session_factory, Predictions.model_validate(PREDICTIONS))
        assert await ModelPicksJob().run(ctx, None) == "done"
        job = PaperLedgerJob()
        monkeypatch.setattr(paper_job, "_now", lambda: NOW)
        assert await run_due([job], ctx, NOW) == {f"paper_ledger:{ON}": "done"}
        assert await run_due([job], ctx, NOW) == {}
        async with ctx.session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(PaperAllocationBatch))
            assert count == 11

    asyncio.run(scenario())


def test_paper_job_marks_a_late_first_run_missed(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        await store_forecast(ctx.session_factory, Predictions.model_validate(PREDICTIONS))
        assert await ModelPicksJob().run(ctx, None) == "done"
        late = TIP + timedelta(minutes=1)
        assert await run_due([PaperLedgerJob()], ctx, late) == {
            f"paper_ledger:{ON}": "missed"
        }
        async with ctx.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(PaperAllocationBatch)) == 0

    asyncio.run(scenario())


def test_paper_job_records_on_same_tick_as_new_model_report(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        ctx, _ = await _setup(tmp_path, monkeypatch)
        await store_forecast(ctx.session_factory, Predictions.model_validate(PREDICTIONS))
        await update_slot(ctx, "odds_snapshot_nba", ODDS_OPEN, state="done")
        monkeypatch.setattr(paper_job, "_now", lambda: NOW)
        tick_time = NOW - timedelta(seconds=1)
        result = await run_due([ModelPicksJob(), PaperLedgerJob()], ctx, tick_time)
        assert result[f"model_picks:{ON}"] == "done"
        assert result[f"paper_ledger:{ON}"] == "done"

    asyncio.run(scenario())
