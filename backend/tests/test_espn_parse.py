from datetime import datetime, timezone

import pytest
from app.ingest.espn import parse_american, parse_line, parse_pickcenter, parse_scoreboard

from tests.matchup_helpers import load_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+102", 102), ("-110", -110), ("EVEN", 100), ("", None), (None, None), ("OFF", None)],
)
def test_parse_american(raw, expected) -> None:
    assert parse_american(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+1.5", 1.5), ("-8.5", -8.5), ("o42.5", 42.5), ("u42.5", 42.5), ("PK", 0.0), (None, None)],
)
def test_parse_line(raw, expected) -> None:
    assert parse_line(raw) == expected


def _by_id():
    return {g.espn_id: g for g in parse_scoreboard(load_json("espn/scoreboard_week6.json"))}


def test_scheduled_game_maps_teams_time_venue_and_season() -> None:
    game = _by_id()["401872995"]
    assert (game.home_name, game.away_name) == ("DEN", "SEA")
    assert game.start_time == datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
    assert (game.season, game.season_type, game.week) == (2026, "REG", 6)
    assert game.status == "scheduled"
    assert (game.home_score, game.away_score) == (None, None)
    assert (game.venue_espn_id, game.venue_indoor, game.neutral) == ("3937", False, False)


def test_pre_game_close_is_stored_as_current_not_close() -> None:
    lines = {line.kind: line for line in _by_id()["401872995"].lines}
    assert set(lines) == {"open", "current"}
    assert lines["open"].spread_home == 2.5
    assert (lines["open"].spread_home_price, lines["open"].spread_away_price) == (-115, -105)
    assert (lines["open"].total, lines["open"].ml_home, lines["open"].ml_away) == (43.5, 110, -130)
    assert (lines["current"].spread_home, lines["current"].total) == (1.5, 42.5)
    assert (lines["current"].ml_home, lines["current"].ml_away) == (102, -122)


def test_started_game_close_is_close() -> None:
    game = _by_id()["401872981"]
    assert game.status == "in_progress"
    assert (game.home_score, game.away_score) == (7, 3)
    assert game.neutral is True
    lines = {line.kind: line for line in game.lines}
    assert set(lines) == {"open", "close"}
    assert lines["close"].spread_home == -8.5


def test_final_game_without_odds_has_scores_and_no_lines() -> None:
    game = _by_id()["401872964"]
    assert game.status == "final"
    assert (game.home_score, game.away_score) == (27, 24)
    assert game.lines == ()


def test_postponed_game_is_not_final_and_has_no_scores() -> None:
    game = _by_id()["401872999"]
    assert game.status == "postponed"
    assert (game.home_score, game.away_score) == (None, None)


def test_pickcenter_gives_open_and_true_close() -> None:
    lines = {line.kind: line for line in parse_pickcenter(load_json("espn/summary_final.json"))}
    assert set(lines) == {"open", "close"}
    close = lines["close"]
    assert (close.spread_home, close.spread_home_price, close.spread_away_price) == (2.5, 100, -120)
    assert (close.total, close.over_price, close.under_price) == (38.5, -105, -115)
    assert (close.ml_home, close.ml_away) == (124, -148)
    assert (lines["open"].total, lines["open"].ml_home) == (40.5, 114)


def test_pickcenter_missing_returns_nothing() -> None:
    assert parse_pickcenter({"header": {}}) == ()
