import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_scoreboard
from app.ingest.seed import seed_teams
from app.jobs.slots import CENTRAL
from app.main import app
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import EventRecord, JobSlot, NbaContextRefresh, ProviderRefreshRun
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 10, 15, 11, 0, tzinfo=CENTRAL).astimezone(timezone.utc)
KICKOFF = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _slot(job, key, state, at, attempts=1, coverage=None, raw_path=None):
    return JobSlot(
        job=job,
        slot_key=key,
        state=state,
        attempts=attempts,
        updated_at=at,
        coverage=coverage,
        raw_path=raw_path,
    )


def _client(tmp_path, heartbeat_minutes_ago=10) -> TestClient:
    async def setup():
        factory = await new_session_factory(tmp_path)
        await seed_teams(factory)
        await GamesRepository(factory).upsert_games(
            "nfl", "espn", parse_scoreboard(load_json("espn/scoreboard_week6.json")), NOW
        )
        coverage = {"targeted": 1, "complete": 1, "books": {"DraftKings": {"spread": 1}}}
        async with factory() as session, session.begin():
            if heartbeat_minutes_ago is not None:
                beat = NOW - timedelta(minutes=heartbeat_minutes_ago)
                session.add(
                    ProviderRefreshRun(
                        provider="run_due", status="succeeded", started_at=beat, completed_at=beat
                    )
                )
            session.add(
                ProviderRefreshRun(
                    provider="the_odds_api",
                    sport="nfl",
                    status="succeeded",
                    started_at=NOW,
                    completed_at=NOW,
                    quota_remaining=471,
                )
            )
            session.add_all(
                [
                    _slot(
                        "odds_snapshot",
                        "odds_snapshot:nfl:2026-10-15:thu",
                        "done",
                        NOW - timedelta(hours=1),
                        coverage=coverage,
                    ),
                    _slot(
                        "odds_snapshot",
                        "odds_snapshot:nfl:2026-10-13:open",
                        "missed",
                        NOW - timedelta(days=2),
                    ),
                    _slot(
                        "grade_bets", "grade_bets:2026-10-15T10", "done", NOW - timedelta(hours=1)
                    ),
                    _slot(
                        "odds_snapshot",
                        "odds_snapshot:nfl:2026-10-14:sun",
                        "raw_saved",
                        NOW - timedelta(days=1),
                        raw_path="x.gz",
                    ),
                ]
            )
            # One event lines up with a game; the other names a team that is not in the schedule.
            for event_id, home in (("event:den", "Denver Broncos"), ("event:ghost", "Atoms")):
                session.add(
                    EventRecord(
                        id=event_id,
                        sport="nfl",
                        league="NFL",
                        home_team=home,
                        away_team="Seattle Seahawks",
                        start_time=KICKOFF,
                        first_observed_at=NOW,
                        last_observed_at=NOW,
                    )
                )

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_health_reports_the_heartbeat_jobs_credits_coverage_and_problems(tmp_path) -> None:
    body = _client(tmp_path).get("/api/v1/ops/health").json()
    assert body["run_due_stale"] is False
    assert body["run_due_last_at"].startswith("2026-10-15T15:50:00")
    assert {j["job"]: j["state"] for j in body["jobs"]} == {
        "grade_bets": "done",
        "odds_snapshot": "done",
    }  # the latest slot of each
    assert body["slots_today"] == {"done": 2}  # the missed and raw_saved ones are older
    assert body["credits_remaining"] == 471
    assert body["snapshot_coverage"]["complete"] == 1
    assert body["stuck_raw_saved"] == ["odds_snapshot:nfl:2026-10-14:sun"]
    assert body["unlinked_events"] == ["event:den", "event:ghost"]  # nothing linked them yet
    assert body["finals_missing_close"] == 1  # the final PIT-CLE game has no close in this DB


@pytest.mark.parametrize("minutes_ago", [50, None])
def test_a_missing_or_old_heartbeat_means_the_scheduler_is_not_running(
    tmp_path, minutes_ago
) -> None:
    client = _client(tmp_path, heartbeat_minutes_ago=minutes_ago)
    assert client.get("/api/v1/ops/health").json()["run_due_stale"] is True


def test_health_exposes_nba_context_requests_coverage_and_error(tmp_path) -> None:
    client = _client(tmp_path)

    async def add_context_runs() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session, session.begin():
            session.add_all(
                [
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="request:scoreboard:2026-10-14",
                        covered_date=NOW - timedelta(days=1),
                        observed_at=NOW,
                        status="requested",
                        team_rows=0,
                        player_rows=0,
                    ),
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="request:summary:123",
                        covered_date=NOW - timedelta(days=1),
                        observed_at=NOW,
                        status="requested",
                        team_rows=0,
                        player_rows=0,
                    ),
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="123",
                        covered_date=NOW - timedelta(days=1),
                        observed_at=NOW,
                        status="complete",
                        team_rows=2,
                        player_rows=24,
                    ),
                ]
            )

    asyncio.run(add_context_runs())
    context = client.get("/api/v1/ops/health").json()["nba_context"]
    assert context["requests_today"] == 2
    assert context["requests_today_estimated"] is False
    assert context["quota_remaining"] is None
    assert context["last_success_at"].startswith("2026-10-15")
    assert context["last_error"] is None


def test_nba_request_count_uses_the_central_calendar_day(tmp_path) -> None:
    client = _client(tmp_path)

    async def add_attempts() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session, session.begin():
            session.add_all(
                [
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="request:scoreboard:prior-central-day",
                        covered_date=NOW,
                        observed_at=datetime(2026, 10, 15, 3, tzinfo=timezone.utc),
                        status="requested",
                        team_rows=0,
                        player_rows=0,
                    ),
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="request:scoreboard:current-central-day",
                        covered_date=NOW,
                        observed_at=datetime(2026, 10, 15, 6, tzinfo=timezone.utc),
                        status="requested",
                        team_rows=0,
                        player_rows=0,
                    ),
                ]
            )

    asyncio.run(add_attempts())
    context = client.get("/api/v1/ops/health").json()["nba_context"]
    assert context["requests_today"] == 1
    assert context["requests_today_estimated"] is False


def test_a_same_run_partial_is_not_hidden_by_another_games_complete_refresh(tmp_path) -> None:
    client = _client(tmp_path)

    async def add_mixed_outcomes() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session, session.begin():
            session.add_all(
                [
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="complete-game",
                        covered_date=NOW,
                        observed_at=NOW,
                        status="complete",
                        team_rows=2,
                        player_rows=20,
                    ),
                    NbaContextRefresh(
                        provider="espn",
                        provider_game_id="partial-game",
                        covered_date=NOW,
                        observed_at=NOW,
                        status="partial",
                        team_rows=0,
                        player_rows=0,
                        error_message="incomplete team or player coverage",
                    ),
                ]
            )

    asyncio.run(add_mixed_outcomes())
    context = client.get("/api/v1/ops/health").json()["nba_context"]
    assert context["last_error"] == "incomplete team or player coverage"
