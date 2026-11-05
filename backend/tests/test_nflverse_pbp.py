from app.ingest.nflverse_pbp import parse_pbp_halves

from tests.pbp_helpers import csv_text, den_sea_week6, play


def _parse(rows):
    return {r.team: r for r in parse_pbp_halves(csv_text(rows).splitlines(keepends=True))}


def test_halftime_score_comes_from_the_second_half_kickoff() -> None:
    den, sea = _parse(den_sea_week6())["DEN"], _parse(den_sea_week6())["SEA"]
    assert (den.points_for_1h, den.points_against_1h) == (3, 7)
    assert (sea.points_for_1h, sea.points_against_1h) == (7, 3)
    assert (den.season, den.season_type, den.week) == (2026, "REG", 6)
    assert (den.team, den.opponent, sea.team, sea.opponent) == ("DEN", "SEA", "SEA", "DEN")


def test_a_score_on_the_last_play_of_the_half_counts() -> None:
    # The start-of-play totals on the final Half1 row would miss it; the Half2 kickoff has it.
    rows = [r for r in den_sea_week6() if r["game_half"] == "Half1"][:3]
    rows.append(play(**{**rows[0], "game_half": "Half2", "total_home_score": 7,
                        "total_away_score": 0}))
    den = _parse(rows)["DEN"]
    assert (den.points_for_1h, den.points_against_1h) == (7, 0)


def test_who_scored_first_is_the_first_score_on_the_board() -> None:
    parsed = _parse(den_sea_week6())
    assert parsed["DEN"].scored_first is True and parsed["SEA"].scored_first is False


def test_scored_first_is_unknown_until_someone_scores() -> None:
    rows = den_sea_week6()
    for r in rows:
        r["total_home_score"] = r["total_away_score"] = 0
    assert _parse(rows)["DEN"].scored_first is None


def test_first_half_offense_counts_only_scrimmage_plays_with_epa() -> None:
    parsed = _parse(den_sea_week6())
    den, sea = parsed["DEN"].stats, parsed["SEA"].stats
    assert (den["plays"], den["success"], den["yards"]) == (3, 1.0, 12.0)
    assert den["epa"] == -2.7 and den["pass_plays"] == 2 and den["pass_epa"] == -2.5
    assert den["rush_plays"] == 1 and den["rush_epa"] == -0.2
    assert (den["turnovers"], den["sacks_taken"]) == (1.0, 0.0)
    assert (sea["plays"], sea["success"], sea["sacks_taken"]) == (3, 2.0, 1.0)
    assert sea["pass_plays"] == 2 and round(sea["pass_epa"], 3) == -0.7
    assert sea["turnovers"] == 1.0  # the fumble on the punt is not an offensive turnover


def test_second_half_plays_are_not_first_half_stats() -> None:
    assert _parse(den_sea_week6())["DEN"].stats["epa"] == -2.7  # not +9.0 or +1.0 on top


def test_a_game_that_has_not_reached_halftime_produces_nothing() -> None:
    first_half_only = [r for r in den_sea_week6() if r["game_half"] == "Half1"]
    assert _parse(first_half_only) == {}


def test_preseason_and_blank_games_are_skipped() -> None:
    rows = den_sea_week6()
    for r in rows:
        r["season_type"] = "PRE"
    assert _parse(rows) == {}
    assert _parse([play(season_type="REG")]) == {}  # no game id
