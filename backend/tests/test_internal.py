import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import httpx
from app.api import internal, routes
from app.jobs.model_picks import load_predictions
from app.jobs.runner import RunResults
from app.jobs.slots import Slot
from app.main import app

from tests.local_client import TestClient
from tests.matchup_helpers import db_url, new_session_factory

SECRET = {"X-Internal-Secret": "jobs-secret"}
PREDICTIONS = {
    "slate_date": "2026-10-22", "model_version": "margin-noline-test",
    "generated_at": "2026-10-22T14:35:00+00:00", "data_through": "2026-10-21",
    "games": [{"game_date": "2026-10-22", "home": "GSW", "away": "NYK", "pred_margin": 4.0,
               "p_home_win": 0.62, "sigma": 14.0}],
}


class FakeJob:
    name = "fake"
    max_attempts = 1

    def slots(self, now: datetime) -> list[Slot]:
        return [Slot("fake:today", now - timedelta(minutes=1), now + timedelta(hours=1))]

    async def run(self, ctx, slot) -> str:
        return "done"


def _configure(monkeypatch, tmp_path, **overrides):
    asyncio.run(new_session_factory(tmp_path))  # the schema, in a file DB the tick reopens
    settings = replace(routes.settings, internal_jobs_secret="jobs-secret",
                       database_url=db_url(tmp_path),
                       nba_predictions_dir=str(tmp_path / "predictions"), **overrides)
    monkeypatch.setattr(routes, "settings", settings)
    monkeypatch.setattr(internal, "JOBS", [FakeJob()])


def test_internal_routes_need_the_secret(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)
    client = TestClient(app)
    assert client.post("/internal/run-due").status_code == 401
    wrong = {"X-Internal-Secret": "nope"}
    assert client.post("/internal/run-due", headers=wrong).status_code == 401
    assert client.post("/internal/nba-predictions", json=PREDICTIONS).status_code == 401
    monkeypatch.setattr(routes, "settings", replace(routes.settings, internal_jobs_secret=None))
    assert client.post("/internal/run-due", headers=SECRET).status_code == 503


def test_a_tick_runs_due_slots_and_reports_counts_only(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)
    response = TestClient(app).post("/internal/run-due", headers=SECRET)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "slots": {"done": 1}}


def test_an_overlapping_tick_is_busy_instead_of_running_twice(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_run_due(jobs, ctx, now):
        started.set()
        await release.wait()
        return RunResults({"fake:today": "done"})

    monkeypatch.setattr(internal, "run_due", slow_run_due)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            first = asyncio.create_task(client.post("/internal/run-due", headers=SECRET))
            await started.wait()
            second = await client.post("/internal/run-due", headers=SECRET)
            assert second.json() == {"status": "busy"}
            release.set()
            assert (await first).json() == {"status": "ok", "slots": {"done": 1}}

    asyncio.run(scenario())


def test_uploaded_predictions_are_what_model_picks_reads(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch, tmp_path)
    client = TestClient(app)
    response = client.post("/internal/nba-predictions", headers=SECRET, json=PREDICTIONS)
    assert response.status_code == 201
    assert response.json() == {"slate_date": "2026-10-22", "games": 1}
    preds = load_predictions(tmp_path / "predictions", date(2026, 10, 22))
    assert preds is not None and preds.games[0].home == "GSW"
    assert not list((tmp_path / "predictions").glob("*.tmp"))

    junk = dict(PREDICTIONS, games=[{"home": "GSW"}])
    assert client.post("/internal/nba-predictions", headers=SECRET, json=junk).status_code == 422
    bad_prob = json.loads(json.dumps(PREDICTIONS))
    bad_prob["games"][0]["p_home_win"] = 1.5
    assert client.post("/internal/nba-predictions", headers=SECRET,
                       json=bad_prob).status_code == 422


def test_from_the_internet_only_internal_routes_get_past_the_private_boundary(
    tmp_path, monkeypatch
) -> None:
    _configure(monkeypatch, tmp_path)
    remote = TestClient(app, client=("203.0.113.7", 443), base_url="https://bet-watch.fly.dev")
    assert remote.post("/internal/run-due", headers=SECRET).status_code == 200
    assert remote.get("/api/v1/bets").status_code == 403
    assert remote.get("/api/v1/health").status_code == 200
    assert remote.get("/internal/run-due").status_code == 403  # only POST is let through
