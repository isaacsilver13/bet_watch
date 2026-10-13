import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_scoreboard
from app.ingest.seed import SEEDS, seed_teams
from app.main import app
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)  # 1pm Eastern on the 15th


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _days(client, **params):
    return client.get("/api/v1/slate/days", params={"sport": "nba", **params})


def _client(tmp_path, now=NOW, extra_days=True, load=True) -> TestClient:
    tmp_path.mkdir(parents=True, exist_ok=True)

    async def setup():
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        if not load:
            return
        records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
        repo = GamesRepository(factory)
        await repo.upsert_games("nba", "espn", records, now)
        if extra_days:
            # The same four games a week later and a week earlier, with new ids.
            for shift_days, offset in ((7, 1000), (-7, 2000)):
                moved = [
                    replace(
                        r, espn_id=str(int(r.espn_id) + offset),
                        start_time=r.start_time.replace(day=r.start_time.day + shift_days),
                    )
                    for r in records
                ]
                await repo.upsert_games("nba", "espn", moved, now)

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    return TestClient(app)


def test_a_day_lists_its_games_in_tip_order_with_lines_and_the_sport(tmp_path) -> None:
    body = _days(_client(tmp_path), date="2026-01-15").json()
    assert (body["sport"], body["date"]) == ("nba", "2026-01-15")
    games = body["games"]
    assert [g["espn_id"] for g in games] == ["401810433", "401810434", "401810435", "401810439"]
    assert {g["sport"] for g in games} == {"nba"}
    assert (games[0]["home"]["abbr"], games[0]["away"]["abbr"], games[0]["neutral"]) == (
        "ORL", "MEM", True)
    assert games[0]["week"] is None
    scheduled = games[3]
    assert (scheduled["home"]["abbr"], scheduled["status"]) == ("GSW", "scheduled")
    assert scheduled["lines"]["current"]["spread_home"] == -7.5
    assert scheduled["lines"]["open"]["total"] == 226.5


def test_the_previous_and_next_days_with_games_skip_the_empty_ones(tmp_path) -> None:
    body = _days(_client(tmp_path), date="2026-01-15").json()
    assert (body["previous"], body["next"]) == ("2026-01-08", "2026-01-22")
    off_day = _days(_client(tmp_path / "b"), date="2026-01-18").json()
    assert off_day["games"] == [] and off_day["previous"] == "2026-01-15"
    assert off_day["next"] == "2026-01-22"


def test_the_eastern_day_decides_which_games_belong_to_it(tmp_path) -> None:
    """BOS-MIA tips 00:30 UTC on the 16th, still the evening of the 15th in New York."""
    client = _client(tmp_path, extra_days=False)
    jan15 = _days(client, date="2026-01-15").json()
    assert "401810435" in [g["espn_id"] for g in jan15["games"]]
    assert _days(client, date="2026-01-16").json()["games"] == []


def test_the_default_day_is_today_or_the_next_with_games(tmp_path) -> None:
    assert _days(_client(tmp_path)).json()["date"] == "2026-01-15"
    seventeenth = datetime(2026, 1, 17, 18, tzinfo=timezone.utc)
    later = _days(_client(tmp_path / "b", now=seventeenth)).json()
    assert later["date"] == "2026-01-22"  # nothing on the 17th: the next day with games
    june = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert _days(_client(tmp_path / "c", now=june)).json()["date"] == "2026-01-22"  # season over


def test_bad_requests_and_an_empty_database(tmp_path) -> None:
    client = _client(tmp_path)
    assert _days(client, date="soon").status_code == 422
    assert client.get("/api/v1/slate/days", params={"sport": "nfl"}).status_code == 422
    empty = _days(_client(tmp_path / "e", load=False))
    assert empty.status_code == 404 and "nba_history" in empty.json()["detail"]
