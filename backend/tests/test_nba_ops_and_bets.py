import asyncio
from datetime import datetime, timedelta, timezone

from app.api.slate import get_now, get_session_factory
from app.domain.bets import BetIn
from app.ingest.espn import parse_scoreboard
from app.ingest.seed import SEEDS, seed_teams
from app.main import app
from app.storage.bets_repository import BetsRepository
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import EventRecord, Game, JobSlot
from app.storage.settlement import settle_final
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)
TIP = datetime(2026, 1, 16, 3, 0, tzinfo=timezone.utc)


def teardown_function() -> None:
    app.dependency_overrides.clear()


async def _setup(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
    await GamesRepository(factory).upsert_games("nba", "espn", records, NOW)
    return factory


def test_health_counts_each_sports_unlinked_events_and_missing_closes(tmp_path) -> None:
    async def setup():
        factory = await _setup(tmp_path)
        async with factory() as session, session.begin():
            session.add(EventRecord(
                id="event:nba-ghost", sport="nba", league="NBA", home_team="Atlantis Waves",
                away_team="Golden State Warriors", start_time=TIP + timedelta(days=1),
                first_observed_at=NOW, last_observed_at=NOW))

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    body = TestClient(app).get("/api/v1/ops/health").json()
    assert body["unlinked_events"] == ["event:nba-ghost"]
    assert body["finals_missing_close"] == 3  # the three final NBA games have no close yet


def test_an_nba_bet_is_graded_from_the_final_score_and_summarized_under_nba(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _setup(tmp_path)
        repo = BetsRepository(factory)
        async with factory() as session:
            game_id = await session.scalar(select(Game.id).where(Game.espn_id == "401810433"))
        # ORL beat MEM 118-111 in Berlin: ORL -5.5 wins, the 229.5 over loses (229 points).
        spread = await repo.create(BetIn(
            game_id=game_id, venue="fanduel", market="spread", selection="home", line=-5.5,
            price_american=-110, stake=11.0), NOW)
        total = await repo.create(BetIn(
            game_id=game_id, venue="draftkings", market="total", selection="over", line=229.5,
            price_american=-105, stake=20.0), NOW)
        assert await settle_final(factory, NOW) == 2
        won, lost = (await repo.get(spread)).bet, (await repo.get(total)).bet
        assert (won.status, won.payout) == ("won", 21.0)
        assert (lost.status, lost.payout) == ("lost", 0.0)
        facts = await repo.facts()
        assert {f.sport for f in facts} == {"nba"}

    asyncio.run(scenario())


def test_health_shows_the_nba_snapshot_coverage_when_it_is_the_latest(tmp_path) -> None:
    async def setup():
        factory = await _setup(tmp_path)
        nfl = {"targeted": 3, "complete": 3, "books": {}}
        nba = {"targeted": 4, "complete": 2, "books": {"FanDuel": {"spread": 4}}}
        async with factory() as session, session.begin():
            session.add(JobSlot(job="odds_snapshot", slot_key="odds_snapshot:nfl:x:open",
                                state="done", attempts=1, coverage=nfl,
                                updated_at=NOW - timedelta(days=2)))
            session.add(JobSlot(job="odds_snapshot_nba", slot_key="odds_snapshot_nba:nba:x:open",
                                state="partial", attempts=1, coverage=nba,
                                updated_at=NOW - timedelta(hours=2)))

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    body = TestClient(app).get("/api/v1/ops/health").json()
    assert body["snapshot_coverage"]["targeted"] == 4
