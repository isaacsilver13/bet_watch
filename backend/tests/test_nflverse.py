from datetime import datetime, timezone

from app.ingest.nflverse import parse_games_csv

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,"
    "stadium_id,stadium"
)


def _row(**overrides) -> str:
    base = {
        "game_id": "2025_01_SEA_DEN", "season": "2025", "game_type": "REG", "week": "1",
        "gameday": "2025-09-07", "weekday": "Sunday", "gametime": "16:25", "away_team": "SEA",
        "away_score": "20", "home_team": "DEN", "home_score": "17", "location": "Home",
        "result": "-3", "total": "37", "overtime": "0", "old_game_id": "", "gsis": "",
        "nfl_detail_id": "", "pfr": "", "pff": "", "espn": "401700001", "ftn": "",
        "away_rest": "7", "home_rest": "7", "away_moneyline": "-130", "home_moneyline": "110",
        "spread_line": "-2.5", "away_spread_odds": "-105", "home_spread_odds": "-115",
        "total_line": "43.5", "under_odds": "-110", "over_odds": "-110", "div_game": "0",
        "roof": "outdoors", "surface": "grass", "temp": "", "wind": "", "away_qb_id": "",
        "home_qb_id": "", "away_qb_name": "", "home_qb_name": "", "away_coach": "",
        "home_coach": "", "referee": "", "stadium_id": "DEN00", "stadium": "Empower Field",
    }
    base.update(overrides)
    return ",".join(base[c] for c in HEADER.split(","))


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_final_regular_season_game_converts_time_spread_and_prices() -> None:
    [game] = parse_games_csv(_csv(_row()))
    assert game.espn_id == "401700001"
    assert (game.season, game.season_type, game.week) == (2025, "REG", 1)
    assert game.start_time == datetime(2025, 9, 7, 20, 25, tzinfo=timezone.utc)  # 16:25 EDT
    assert (game.home_name, game.away_name) == ("DEN", "SEA")
    assert (game.home_score, game.away_score) == (17, 20)
    assert game.status == "final" and game.neutral is False
    [close] = game.lines
    assert close.kind == "close"
    assert close.spread_home == 2.5  # nflverse -2.5 = home dog by 2.5
    assert (close.spread_home_price, close.spread_away_price) == (-115, -105)
    assert (close.total, close.over_price, close.under_price) == (43.5, -110, -110)
    assert (close.ml_home, close.ml_away) == (110, -130)


def test_postseason_week_mapping_and_neutral_super_bowl() -> None:
    [sb] = parse_games_csv(_csv(_row(game_type="SB", week="22", location="Neutral")))
    assert (sb.season_type, sb.week, sb.neutral) == ("POST", 5, True)


def test_future_game_has_no_scores_or_lines() -> None:
    [game] = parse_games_csv(_csv(_row(away_score="", home_score="", result="", total="")))
    assert game.status == "scheduled"
    assert (game.home_score, game.away_score, game.lines) == (None, None, ())


def test_rows_without_espn_id_or_before_min_season_are_skipped() -> None:
    rows = _csv(_row(espn=""), _row(espn="NA"), _row(season="2014", espn="1"), _row(espn="7"))
    assert [g.espn_id for g in parse_games_csv(rows)] == ["7"]


def test_missing_gametime_defaults_to_1pm_eastern() -> None:
    [game] = parse_games_csv(_csv(_row(gametime="")))
    assert game.start_time == datetime(2025, 9, 7, 17, 0, tzinfo=timezone.utc)


def test_starting_quarterbacks_are_kept_when_present() -> None:
    [game] = parse_games_csv(_csv(_row(home_qb_name="Bo Nix", away_qb_name="Sam Darnold")))
    assert (game.home_qb, game.away_qb) == ("Bo Nix", "Sam Darnold")
    [blank] = parse_games_csv(_csv(_row()))
    assert (blank.home_qb, blank.away_qb) == (None, None)
