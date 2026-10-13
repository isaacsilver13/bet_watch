from datetime import datetime, timedelta, timezone

from app.analytics.efficiency import MetricValue, TeamGame, season_efficiency

T0 = datetime(2026, 9, 6, 17, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=60)


def stats(**overrides):
    base = {
        "attempts": 30, "sacks_suffered": 2, "carries": 18, "passing_epa": 6.4,
        "rushing_epa": -1.2, "passing_yards": 250, "rushing_yards": 100,
        "passing_interceptions": 1, "rushing_fumbles_lost": 0, "sack_fumbles_lost": 1,
        "receiving_fumbles_lost": 0, "def_sacks": 3,
    }
    base.update(overrides)
    return base


def test_offense_defense_points_and_turnovers_for_one_game() -> None:
    # A: 50 plays, EPA 5.2 -> 0.104 a play. B: 50 plays, EPA -2.0 -> -0.04 a play.
    a = TeamGame(1, 10, T0, stats(), points_for=27, points_against=20)
    b = TeamGame(1, 20, T0, stats(passing_epa=-1.0, rushing_epa=-1.0, def_sacks=1,
                                   passing_interceptions=3), points_for=20, points_against=27)
    table = season_efficiency([a, b], LATER)
    assert table[10]["off_epa_play"].value == 0.104
    assert table[10]["def_epa_play"].value == -0.04  # B's offense against A
    assert table[20]["def_epa_play"].value == 0.104
    assert table[10]["yards_play"].value == 7.0  # 350 yards over 50 plays
    assert table[10]["points_for_pg"].value == 27.0
    assert table[10]["points_against_pg"].value == 20.0
    assert table[10]["turnovers_pg"].value == 2.0  # 1 interception + 1 sack fumble lost
    assert table[10]["takeaways_pg"].value == 4.0  # B gave it away 3 + 1 times
    assert table[10]["sacks_pg"].value == 3.0 and table[10]["sacks_taken_pg"].value == 2.0


def test_rank_direction_ties_and_only_games_before_the_cutoff() -> None:
    rows = [
        TeamGame(1, 10, T0, stats(passing_epa=10.0), 30, 10),
        TeamGame(1, 20, T0, stats(passing_epa=0.0), 10, 30),
        TeamGame(2, 30, T0, stats(passing_epa=0.0), 10, 10),
        TeamGame(2, 40, T0, stats(passing_epa=0.0), 10, 10),
        TeamGame(3, 10, LATER, stats(passing_epa=-500.0), 0, 0),  # after the cutoff: ignored
    ]
    table = season_efficiency(rows, T0 + timedelta(days=30))
    assert table[10]["off_epa_play"].rank == 1 and table[10]["off_epa_play"].of == 4
    assert table[20]["off_epa_play"].rank == table[30]["off_epa_play"].rank == 2  # ties share
    assert table[40]["off_epa_play"].rank == 2
    assert table[10]["points_against_pg"].rank == 1  # tied with teams 30 and 40 at 10
    assert table[20]["points_against_pg"].rank == 4  # allowing 30 is the worst
    assert isinstance(table[10]["off_epa_play"], MetricValue)


def test_missing_opponent_row_skips_defense_but_keeps_offense() -> None:
    table = season_efficiency([TeamGame(1, 10, T0, stats(), None, None)], LATER)
    assert "off_epa_play" in table[10] and "def_epa_play" not in table[10]
    assert "points_for_pg" not in table[10]
