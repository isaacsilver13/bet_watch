"""Hand-worked NBA season. Viewed game: BOS at MIA, Thursday 2026-01-15 7:30pm ET.

BOS before it:  Jan 10 W 110-100 vs MIA (BOS -5.5, covered; 210 pts, total 215.5, under)
                | Jan 14 W 108-99 at DET (DET -2, so BOS covered; 207 pts, total 212.5, under)
MIA before it:  Jan 10 L 100-110 at BOS (MIA +5.5, did not cover) | Jan 13 W 105-99 vs DET
                (MIA -3, covered; 204 pts, total 208, under)
Team ratings come from each game's possessions: BOS offense (110 x 100 + 113.7 x 95) / 195.
"""

import asyncio
from datetime import date, datetime, timezone

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_injuries
from app.ingest.nba_stats import GameLogRecord
from app.ingest.records import GameRecord, LineRecord
from app.ingest.seed import SEEDS, seed_teams
from app.main import app
from app.storage.context_repository import ContextRepository
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)  # morning of the viewed game


def utc(month, day, hour=0, minute=30):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def final(espn_id, when, home, away, home_score, away_score, spread_home, total):
    close = LineRecord("close", spread_home=spread_home, spread_home_price=-110,
                       spread_away_price=-110, total=total, over_price=-110, under_price=-110)
    return GameRecord(
        espn_id=espn_id, season=2026, season_type="REG", week=None, start_time=when,
        status="final", home_name=home, away_name=away, home_score=home_score,
        away_score=away_score, neutral=False, lines=(close,),
    )


GAMES = [
    final("1", utc(1, 11), "BOS", "MIA", 110, 100, -5.5, 215.5),  # Jan 10, Eastern
    final("2", utc(1, 14), "MIA", "DET", 105, 99, -3.0, 208.0),  # Jan 13
    final("3", utc(1, 15), "DET", "BOS", 99, 108, -2.0, 212.5),  # Jan 14
    GameRecord(
        espn_id="4", season=2026, season_type="REG", week=None, start_time=utc(1, 16),
        status="scheduled", home_name="MIA", away_name="BOS", home_score=None,
        away_score=None, neutral=False,
    ),  # Jan 15, 7:30pm Eastern
]


def log(day, team, opponent, off, defense, poss, pace=100.0):
    stats = {"OFF_RATING": off, "DEF_RATING": defense, "POSS": poss, "PACE": pace, "MIN": 48.0}
    return GameLogRecord(2026, day, team, opponent, stats)


JAN10, JAN13, JAN14 = date(2026, 1, 10), date(2026, 1, 13), date(2026, 1, 14)
LOGS = [
    log(JAN10, "BOS", "MIA", 110.0, 100.0, 100),
    log(JAN10, "MIA", "BOS", 100.0, 110.0, 100),
    log(JAN13, "MIA", "DET", 107.1, 101.0, 98),
    log(JAN13, "DET", "MIA", 101.0, 107.1, 98),
    log(JAN14, "BOS", "DET", 113.7, 104.2, 95),
    log(JAN14, "DET", "BOS", 104.2, 113.7, 95),
]


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _client(tmp_path, now=NOW, with_data=True) -> TestClient:
    async def setup():
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        if with_data:
            await GamesRepository(factory).upsert_games("nba", "espn", GAMES, NOW)
            repo = ContextRepository(factory)
            await repo.save_game_log_stats("nba", LOGS, NOW)
            report = parse_injuries(load_json("espn_nba/injuries_league.json"))
            await repo.save_injuries("nba", report, datetime(2026, 1, 15, 14, tzinfo=timezone.utc))

    asyncio.run(setup())
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    return TestClient(app)


def _viewed(tmp_path) -> int:
    async def lookup() -> int:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == "4"))

    return asyncio.run(lookup())


def _context(client, game_id, **params):
    response = client.get(f"/api/v1/games/{game_id}/context", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_records_use_the_closing_lines_before_the_game(tmp_path) -> None:
    client = _client(tmp_path)
    body = _context(client, _viewed(tmp_path))
    assert body["sport"] == "nba"
    bos, mia = body["away"], body["home"]
    assert (bos["team"]["abbr"], mia["team"]["abbr"]) == ("BOS", "MIA")
    assert bos["records"]["season"] == {
        "su": {"win": 2, "loss": 0, "push": 0}, "ats": {"win": 2, "loss": 0, "push": 0},
        "ou": {"win": 0, "loss": 2, "push": 0},  # both games stayed under: loss = under
    }
    assert mia["records"]["season"] == {
        "su": {"win": 1, "loss": 1, "push": 0}, "ats": {"win": 1, "loss": 1, "push": 0},
        "ou": {"win": 0, "loss": 2, "push": 0},
    }
    meetings = body["head_to_head"]
    assert [(m["home"], m["away"], m["home_score"]) for m in meetings["meetings"]] == [
        ("BOS", "MIA", 110)]
    assert meetings["home_su"] == {"win": 0, "loss": 1, "push": 0}  # MIA, home tonight, lost it
    assert meetings["home_ats"] == {"win": 0, "loss": 1, "push": 0}


def test_ratings_are_possession_weighted_and_ranked_across_the_league(tmp_path) -> None:
    body = _context(_client(tmp_path), _viewed(tmp_path))
    bos, mia = body["away"]["efficiency"], body["home"]["efficiency"]
    assert bos["off_rtg"] == {"value": 111.8, "rank": 1, "of": 3}  # three teams have played
    assert bos["def_rtg"]["value"] == 102.05 and bos["def_rtg"]["rank"] == 1  # lower is better
    assert bos["net_rtg"] == {"value": 9.76, "rank": 1, "of": 3}
    assert mia["net_rtg"] == {"value": -2.03, "rank": 2, "of": 3}
    assert mia["off_rtg"]["value"] == 103.51 and mia["def_rtg"]["value"] == 105.55
    assert body["data_as_of"]["efficiency"] is not None


def test_schedule_spots_count_the_games_before_tonight(tmp_path) -> None:
    body = _context(_client(tmp_path), _viewed(tmp_path))
    bos, mia = body["away"]["situation"], body["home"]["situation"]
    # BOS played at Detroit last night; the Jan 10 game is five days back.
    assert (bos["rest_days"], bos["back_to_back"], bos["previous_opponent"]) == (1, True, "DET")
    assert (bos["three_in_four"], bos["four_in_six"], bos["games_last_7"]) == (False, False, 2)
    assert bos["travel_miles"] == 1156 and bos["tz_crossed"] == 0  # Detroit to Miami
    # MIA played at home on the 13th.
    assert (mia["rest_days"], mia["back_to_back"], mia["previous_opponent"]) == (2, False, "DET")
    assert (mia["travel_miles"], mia["games_last_7"]) == (0, 2)
    assert bos["short_week"] is False and bos["off_bye"] is False  # NFL notions


def test_tomorrows_game_counts_tonights_game_that_has_not_been_played_yet(tmp_path) -> None:
    client = _client(tmp_path)
    tomorrow = GameRecord(
        espn_id="5", season=2026, season_type="REG", week=None, start_time=utc(1, 17),
        status="scheduled", home_name="CHA", away_name="BOS", home_score=None,
        away_score=None, neutral=False,
    )  # Jan 16, 7:30pm Eastern: BOS plays tonight at MIA and again tomorrow

    async def add() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        await GamesRepository(factory).upsert_games("nba", "espn", [tomorrow], NOW)

    asyncio.run(add())
    bos = _context(client, _game_id(tmp_path, "5"))["away"]["situation"]
    assert (bos["rest_days"], bos["back_to_back"], bos["previous_opponent"]) == (1, True, "MIA")
    assert bos["games_last_7"] == 3  # Jan 10, Jan 14 and tonight's


def test_injuries_come_from_the_nba_report_and_there_is_no_quarterback_flag(tmp_path) -> None:
    body = _context(_client(tmp_path), _viewed(tmp_path))
    assert body["away"]["qb_flag"] is None and body["home"]["qb_flag"] is None
    assert body["data_as_of"]["injuries"].startswith("2026-01-15T14:00:00")
    miami = {i["player"] for i in body["home"]["injuries"]}
    assert miami  # Miami is in the fixture report
    assert {i["status"] for i in body["home"]["injuries"] + body["away"]["injuries"]} <= {
        "Out", "Day-To-Day"}


def test_nba_has_no_primetime_and_divisional_means_the_same_division(tmp_path) -> None:
    body = _context(_client(tmp_path), _viewed(tmp_path))
    assert body["primetime"] is False  # a 7:30pm tip is not a scheduling spot in the NBA
    assert body["divisional"] is False  # Boston is Atlantic, Miami is Southeast


def test_a_finished_game_shows_only_what_was_known_before_it(tmp_path) -> None:
    client = _client(tmp_path, now=utc(1, 20))
    third = _context(client, _game_id(tmp_path, "3"))  # DET vs BOS on Jan 14, now over
    bos = third["away"]
    assert bos["efficiency"]["off_rtg"]["value"] == 110.0  # only the Jan 10 game
    assert bos["records"]["season"]["su"] == {"win": 1, "loss": 0, "push": 0}
    assert (bos["situation"]["rest_days"], bos["situation"]["back_to_back"]) == (4, False)


def _game_id(tmp_path, espn_id) -> int:
    async def lookup() -> int:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        async with factory() as session:
            return await session.scalar(select(Game.id).where(Game.espn_id == espn_id))

    return asyncio.run(lookup())


def test_a_team_with_no_history_returns_empty_sections_not_an_error(tmp_path) -> None:
    client = _client(tmp_path, with_data=False)

    async def only_the_game() -> None:
        factory = create_session_factory(create_async_engine(db_url(tmp_path)))
        await GamesRepository(factory).upsert_games("nba", "espn", [GAMES[3]], NOW)

    asyncio.run(only_the_game())
    body = _context(client, _viewed(tmp_path))
    assert body["home"]["efficiency"] == {} and body["away"]["efficiency"] == {}
    assert body["home"]["situation"]["rest_days"] is None
    assert body["home"]["situation"]["back_to_back"] is False
    assert body["head_to_head"]["meetings"] == []
