from datetime import datetime, timedelta, timezone

from app.analytics.efficiency import MetricValue
from app.analytics.ratings import RatedGame, season_ratings

T0 = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)


def row(game_id, team_id, when, off, defense, poss, pace, efg, tov, oreb, ftr, o_efg, o_tov, o_ftr,
        o_oreb, minutes=48.0):
    return RatedGame(game_id, team_id, when, {
        "OFF_RATING": off, "DEF_RATING": defense, "POSS": poss, "PACE": pace, "MIN": minutes,
        "EFG_PCT": efg, "TM_TOV_PCT": tov, "OREB_PCT": oreb, "FTA_RATE": ftr,
        "OPP_EFG_PCT": o_efg, "OPP_TOV_PCT": o_tov, "OPP_FTA_RATE": o_ftr, "OPP_OREB_PCT": o_oreb,
    })


# Game 1 (T0): A 120 - B 110 over 100 possessions. Game 2 (two days later): A vs C over 90.
GAMES = [
    row(1, 1, T0, 120, 110, 100, 100, .55, .12, .25, .30, .50, .14, .25, .22),
    row(1, 2, T0, 110, 120, 100, 100, .50, .14, .22, .25, .55, .12, .30, .25),
    row(2, 1, T0 + timedelta(days=2), 100, 105, 90, 90, .45, .10, .20, .20, .52, .11, .24, .26),
    row(2, 3, T0 + timedelta(days=2), 105, 100, 90, 90, .52, .11, .26, .24, .45, .10, .20, .20),
]
LATER = T0 + timedelta(days=30)


def test_ratings_weight_each_game_by_its_possessions() -> None:
    table = season_ratings(GAMES, LATER)
    a = table[1]
    # Offense (120 x 100 + 100 x 90) / 190 = 110.53; defense (110 x 100 + 105 x 90) / 190 = 107.63.
    assert a["off_rtg"].value == 110.53
    assert a["def_rtg"].value == 107.63
    assert a["net_rtg"].value == 2.89  # from the unrounded 110.526 - 107.632
    assert a["pace"].value == 95.0  # (100 x 48 + 90 x 48) / 96 minutes
    assert a["efg"].value == 0.503  # (.55 x 100 + .45 x 90) / 190
    assert a["tov_pct"].value == 0.111
    assert a["oreb_pct"].value == 0.226
    assert a["ftr"].value == 0.253
    assert a["opp_efg"].value == 0.509  # (.50 x 100 + .52 x 90) / 190
    assert table[2]["net_rtg"].value == -10.0 and table[3]["net_rtg"].value == 5.0


def test_ranks_run_the_right_way_for_each_metric() -> None:
    table = season_ratings(GAMES, LATER)
    # net rating and offense: higher is better; defense: lower is better; pace: faster is first
    assert [table[t]["net_rtg"].rank for t in (3, 1, 2)] == [1, 2, 3]
    assert [table[t]["off_rtg"].rank for t in (1, 2, 3)] == [1, 2, 3]
    assert [table[t]["def_rtg"].rank for t in (3, 1, 2)] == [1, 2, 3]
    assert [table[t]["pace"].rank for t in (2, 1, 3)] == [1, 2, 3]
    assert table[1]["net_rtg"] == MetricValue(2.89, 2, 3)


def test_only_games_before_the_cutoff_count() -> None:
    table = season_ratings(GAMES, T0 + timedelta(days=1))
    assert set(table) == {1, 2}  # C has not played yet
    assert table[1]["off_rtg"].value == 120.0
    assert table[1]["off_rtg"].of == 2
    assert season_ratings(GAMES, T0) == {}  # nothing before the first tip


def test_a_missing_stat_is_left_out_not_zeroed() -> None:
    thin = [RatedGame(1, 1, T0, {"OFF_RATING": 100.0, "POSS": 90.0, "MIN": 48.0})]
    table = season_ratings(thin, LATER)
    assert set(table[1]) == {"off_rtg"}
