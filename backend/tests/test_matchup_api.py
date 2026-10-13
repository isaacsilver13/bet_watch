"""Hand-worked season. Viewed game: SEA @ DEN, Thursday 2026-10-15 8:15pm ET (week 6).

DEN before it:  wk1 W 24-20 vs KC (DEN -3, push on 44) | wk2 L 17-27 at SEA (DEN -1)
                | wk4 W 23-17 at LV (DEN +3).  2025 wk14: W 20-17 vs SEA (DEN +2.5).
SEA before it:  wk2 W 27-17 vs DEN (SEA +1) | wk3 W 30-23 vs LAR (SEA -2.5)
                | wk5 W 20-19 vs ARI (SEA -6.5, a loss against the spread).
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_injuries, parse_scoreboard
from app.ingest.nflverse import parse_games_csv
from app.ingest.nflverse_stats import TeamStatRecord
from app.ingest.open_meteo import WeatherRecord
from app.ingest.records import LineRecord
from app.ingest.seed import seed_teams
from app.main import app
from app.storage.context_repository import ContextRepository
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from app.storage.models import Game
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from tests.matchup_helpers import db_url, load_json, new_session_factory
from tests.test_nflverse import _csv, _row

NOW = datetime(2026, 10, 14, 22, tzinfo=timezone.utc)
VIEWED = "401872995"  # SEA @ DEN
HISTORY = [
    dict(espn="401900001", season="2025", week="14", gameday="2025-12-14", home_team="DEN",
         away_team="SEA", home_score="20", away_score="17", spread_line="-2.5", total_line="40"),
    dict(espn="401900002", season="2026", week="1", gameday="2026-09-13", home_team="DEN",
         away_team="KC", home_score="24", away_score="20", spread_line="3", total_line="44",
         home_qb_name="Bo Nix", away_qb_name="Patrick Mahomes"),
    dict(espn="401900003", season="2026", week="2", gameday="2026-09-20", gametime="16:05",
         home_team="SEA", away_team="DEN", home_score="27", away_score="17", spread_line="-1",
         total_line="41", home_qb_name="Sam Darnold", away_qb_name="Bo Nix"),
    dict(espn="401900004", season="2026", week="3", gameday="2026-09-27", home_team="SEA",
         away_team="LAR", home_score="30", away_score="23", spread_line="2.5", total_line="50",
         home_qb_name="Sam Darnold", away_qb_name="Matthew Stafford"),
    dict(espn="401900005", season="2026", week="4", gameday="2026-10-04", home_team="LV",
         away_team="DEN", home_score="17", away_score="23", spread_line="3", total_line="38",
         home_qb_name="Geno Smith", away_qb_name="Bo Nix"),
    dict(espn="401900006", season="2026", week="5", gameday="2026-10-11", home_team="SEA",
         away_team="ARI", home_score="20", away_score="19", spread_line="6.5", total_line="45",
         home_qb_name="Sam Darnold", away_qb_name="Kyler Murray"),
]
INJURIES = {"injuries": [
    {"id": "7", "injuries": [{
        "status": "Questionable", "date": "2026-10-14T18:00Z",
        "athlete": {"displayName": "Bo Nix", "position": {"abbreviation": "QB"}},
        "details": {"type": "Ankle"},
    }]},
    {"id": "26", "injuries": [{
        "status": "Out", "date": "2026-10-14T18:00Z",
        "athlete": {"displayName": "Tyler Lockett", "position": {"abbreviation": "WR"}},
    }]},
]}


def _offense(**overrides):
    base = {
        "attempts": 30.0, "sacks_suffered": 2.0, "carries": 18.0, "passing_epa": 6.4,
        "rushing_epa": -1.2, "passing_yards": 250.0, "rushing_yards": 100.0,
        "passing_interceptions": 1.0, "sack_fumbles_lost": 1.0, "def_sacks": 3.0,
    }
    base.update(overrides)
    return base


async def _setup(tmp_path, espn_close: bool = False, preseason: bool = False) -> None:
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    games = GamesRepository(factory)
    await games.upsert_games(
        "nfl", "espn", parse_scoreboard(load_json("espn/scoreboard_week6.json")), NOW
    )
    await games.upsert_games(
        "nfl", "nflverse", parse_games_csv(_csv(*[_row(**row) for row in HISTORY])), NOW
    )
    if preseason:  # an August exhibition DEN won 30-0, which must not count anywhere
        den = next(r for r in parse_scoreboard(load_json("espn/scoreboard_week6.json"))
                   if r.espn_id == VIEWED)
        exhibition = replace(
            den, espn_id="900101", season_type="PRE", week=2, status="final",
            start_time=datetime(2026, 8, 22, 23, tzinfo=timezone.utc), away_name="ARI",
            home_score=30, away_score=0, home_qb="Preseason QB",
            lines=(LineRecord(kind="close", spread_home=-3.0, total=40.0),),
        )
        await games.upsert_games("nfl", "espn", [exhibition], NOW)
    if espn_close:  # ESPN's close for DEN-KC disagrees with nflverse's (-3): DEN -4 pushes
        await games.save_lines(
            "401900002", "espn", [LineRecord(kind="close", spread_home=-4.0, total=44.0)], NOW
        )
    context = ContextRepository(factory)
    await context.save_team_stats("nfl", [
        TeamStatRecord(2026, "REG", 2, "SEA", "DEN", _offense(passing_epa=-1.0, rushing_epa=-1.0)),
        TeamStatRecord(2026, "REG", 2, "DEN", "SEA", _offense()),
    ], datetime(2026, 9, 22, tzinfo=timezone.utc))
    snapshots = [
        (datetime(2026, 10, 7, 20, tzinfo=timezone.utc), "Out"),  # older: superseded
        (datetime(2026, 10, 14, 20, tzinfo=timezone.utc), "Questionable"),  # the latest <= now
        (datetime(2026, 10, 15, 20, tzinfo=timezone.utc), "Doubtful"),  # in the future: ignored
    ]
    for observed_at, status in snapshots:
        payload = {"injuries": [dict(INJURIES["injuries"][0], injuries=[
            dict(INJURIES["injuries"][0]["injuries"][0], status=status)]), INJURIES["injuries"][1]]}
        await context.save_injuries("nfl", parse_injuries(payload), observed_at)
    async with factory() as session:
        game_id = await session.scalar(select(Game.id).where(Game.espn_id == VIEWED))
    forecast_time = datetime(2026, 10, 14, 21, tzinfo=timezone.utc)
    await context.save_weather(game_id, WeatherRecord(55.4, 11.8, 60, "Overcast"), forecast_time)


def _client(tmp_path, espn_close: bool = False, preseason: bool = False) -> TestClient:
    asyncio.run(_setup(tmp_path, espn_close, preseason))
    # A fresh engine: pooled connections from setup's event loop must not leak into TestClient's.
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _game_id(client: TestClient, espn_id: str) -> int:
    for week in (6, 5, 4, 3, 2, 1):
        body = client.get("/api/v1/slate", params={"season": 2026, "week": week}).json()
        for game in body["games"]:
            if game["espn_id"] == espn_id:
                return game["id"]
    raise AssertionError(espn_id)


def _context(client: TestClient, espn_id: str = VIEWED, **params) -> dict:
    response = client.get(f"/api/v1/games/{_game_id(client, espn_id)}/context", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _tally(view: dict) -> tuple:
    return view["win"], view["loss"], view["push"]


def test_game_header_has_roof_weather_and_lines(tmp_path) -> None:
    client = _client(tmp_path)
    body = client.get(f"/api/v1/games/{_game_id(client, VIEWED)}").json()
    assert (body["home"]["abbr"], body["away"]["abbr"], body["roof"]) == ("DEN", "SEA", "outdoors")
    assert body["weather"]["temp_f"] == 55.4 and body["weather"]["conditions"] == "Overcast"
    assert body["lines"]["open"]["spread_home"] == 2.5
    slate = client.get("/api/v1/slate", params={"season": 2026, "week": 6}).json()
    den = next(g for g in slate["games"] if g["espn_id"] == VIEWED)
    assert den["weather"]["wind_mph"] == 11.8  # the slate carries it for the card badge


def test_records_use_only_games_before_this_one(tmp_path) -> None:
    ctx = _context(_client(tmp_path))
    den, sea = ctx["home"], ctx["away"]
    assert (den["team"]["abbr"], sea["team"]["abbr"]) == ("DEN", "SEA")
    season = den["records"]["season"]
    assert _tally(season["su"]) == (2, 1, 0) and _tally(season["ats"]) == (2, 1, 0)
    assert _tally(season["ou"]) == (2, 0, 1)  # wk1 pushed on 44; wk2 and wk4 went over
    assert _tally(den["records"]["home"]["su"]) == (1, 0, 0)
    assert _tally(den["records"]["away"]["su"]) == (1, 1, 0)
    assert _tally(den["records"]["favorite"]["ats"]) == (1, 1, 0)  # wk1 covered, wk2 (-1) did not
    assert _tally(den["records"]["underdog"]["ats"]) == (1, 0, 0)  # +3 at LV
    sea_season = sea["records"]["season"]
    assert _tally(sea_season["su"]) == (3, 0, 0) and _tally(sea_season["ats"]) == (2, 1, 0)


def test_last_n_spans_seasons_and_head_to_head_is_chronological(tmp_path) -> None:
    ctx = _context(_client(tmp_path), last_n=2)
    assert _tally(ctx["home"]["records"]["last_n"]["su"]) == (1, 1, 0)  # wk2 L, wk4 W
    meetings = ctx["head_to_head"]["meetings"]
    assert [m["start_time"][:10] for m in meetings] == ["2025-12-14", "2026-09-20"]
    assert (meetings[0]["home"], meetings[0]["spread_home"]) == ("DEN", 2.5)
    assert _tally(ctx["head_to_head"]["home_su"]) == (1, 1, 0)
    assert _tally(ctx["head_to_head"]["home_ats"]) == (1, 1, 0)
    assert len(_context(_client(tmp_path), last_n=1)["head_to_head"]["meetings"]) == 1


def test_situation_flags_the_short_week_and_measures_the_trip(tmp_path) -> None:
    ctx = _context(_client(tmp_path))
    den, sea = ctx["home"]["situation"], ctx["away"]["situation"]
    assert (den["rest_days"], den["short_week"], den["off_bye"]) == (11, False, False)
    assert den["previous_opponent"] == "LV" and den["tz_crossed"] == 1
    assert 605 <= den["travel_miles"] <= 612  # Las Vegas to Denver
    assert (sea["rest_days"], sea["short_week"]) == (4, True)  # Sunday to Thursday
    assert sea["previous_opponent"] == "ARI" and 1015 <= sea["travel_miles"] <= 1020
    assert ctx["primetime"] is True and ctx["divisional"] is False


def test_divisional_game_is_flagged(tmp_path) -> None:
    ctx = _context(_client(tmp_path), espn_id="401900005")  # DEN at LV, both AFC West
    assert ctx["divisional"] is True and ctx["primetime"] is False


def test_efficiency_comes_from_stats_before_kickoff_with_ranks(tmp_path) -> None:
    ctx = _context(_client(tmp_path))
    den, sea = ctx["home"]["efficiency"], ctx["away"]["efficiency"]
    assert den["off_epa_play"] == {"value": 0.104, "rank": 1, "of": 2}
    assert sea["off_epa_play"] == {"value": -0.04, "rank": 2, "of": 2}
    assert den["def_epa_play"]["value"] == -0.04 and den["def_epa_play"]["rank"] == 1
    assert den["points_for_pg"]["value"] == 17.0  # DEN scored 17 in the week 2 game with stats


def test_injuries_use_the_latest_snapshot_up_to_now_and_flag_the_starting_qb(tmp_path) -> None:
    ctx = _context(_client(tmp_path))
    den_injuries = ctx["home"]["injuries"]
    assert [(i["player"], i["status"]) for i in den_injuries] == [("Bo Nix", "Questionable")]
    assert den_injuries[0]["detail"] == "Ankle"
    assert ctx["home"]["qb_flag"] == {"player": "Bo Nix", "status": "Questionable"}
    assert ctx["away"]["qb_flag"] is None  # Lockett is a receiver; Darnold is healthy
    assert [i["player"] for i in ctx["away"]["injuries"]] == ["Tyler Lockett"]
    assert ctx["data_as_of"]["injuries"].startswith("2026-10-14T20:00")
    assert ctx["data_as_of"]["efficiency"].startswith("2026-09-22")


def test_a_game_with_no_history_returns_empty_sections_not_an_error(tmp_path) -> None:
    ctx = _context(_client(tmp_path), espn_id="401872999")  # MIA vs BUF, nothing loaded for them
    assert ctx["home"]["efficiency"] == {} and ctx["home"]["injuries"] == []
    assert _tally(ctx["home"]["records"]["season"]["su"]) == (0, 0, 0)
    assert ctx["home"]["situation"]["rest_days"] is None and ctx["head_to_head"]["meetings"] == []
    assert ctx["data_as_of"]["efficiency"].startswith("2026-09-22")  # league-wide table time


def test_unknown_game_is_404_and_last_n_is_bounded(tmp_path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/games/99999").status_code == 404
    assert client.get("/api/v1/games/99999/context").status_code == 404
    game_id = _game_id(client, VIEWED)
    assert client.get(f"/api/v1/games/{game_id}/context", params={"last_n": 0}).status_code == 422
    assert client.get(f"/api/v1/games/{game_id}/context", params={"last_n": 21}).status_code == 422


def test_ats_prefers_the_espn_close_over_nflverse(tmp_path) -> None:
    ctx = _context(_client(tmp_path, espn_close=True))
    assert _tally(ctx["home"]["records"]["season"]["ats"]) == (1, 1, 1)  # wk1 pushes at DEN -4


def test_a_finished_game_shows_only_what_was_known_before_kickoff(tmp_path) -> None:
    ctx = _context(_client(tmp_path), espn_id="401900005")  # DEN at LV, 2026-10-04
    den = ctx["away"]
    assert den["team"]["abbr"] == "DEN"
    assert _tally(den["records"]["season"]["su"]) == (1, 1, 0)  # wk1 and wk2 only; not wk4 itself
    assert den["injuries"] == [] and den["qb_flag"] is None  # every snapshot came after kickoff
    assert ctx["data_as_of"]["injuries"] is None
    assert den["situation"]["previous_opponent"] == "SEA"
    assert den["efficiency"]["off_epa_play"]["value"] == 0.104  # the wk2 stats were in by then


def test_preseason_games_do_not_count_and_a_season_opener_has_no_travel_from_last_year(
    tmp_path,
) -> None:
    ctx = _context(_client(tmp_path, preseason=True), espn_id="401900002")  # DEN vs KC, week 1
    den = ctx["home"]
    assert _tally(den["records"]["season"]["su"]) == (0, 0, 0)  # the 30-0 exhibition is ignored
    assert _tally(den["records"]["last_n"]["su"]) == (1, 0, 0)  # only the 2025 week-14 win
    assert den["qb_flag"] is None
    spot = den["situation"]
    assert spot["previous_opponent"] is None and spot["travel_miles"] is None
    assert spot["rest_days"] is None and spot["tz_crossed"] is None
    assert spot["short_week"] is False and spot["off_bye"] is False
