"""Hand-worked first-half profile. Kickoffs are Sundays; the viewed game is on Oct 11.

AAA (home) vs BBB:  wk1  AAA 24-20 BBB   half 7-10   AAA trailed and won
                    wk2  AAA 17-27 CCC   half 10-14  AAA trailed and lost
                    wk3  AAA 23-23 DDD   half 10-10  tied at the half, tied at the end
                    wk4  AAA 30-10 BBB   half 17-3   AAA led and won
AAA scored first in wk3 and wk4 only; in wk2 nobody was recorded as first on the board.
Opponents' first-half offense is what AAA's defense faced, game by game.
"""

from datetime import datetime, timezone

from app.analytics.first_half import HalfGame, Outcome, season_first_half

AAA, BBB, CCC, DDD = 1, 2, 3, 4
KICKOFF = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


def day(month: int, d: int) -> datetime:
    return datetime(2026, month, d, 17, tzinfo=timezone.utc)


def offense(plays, epa, pass_plays, pass_epa, turnovers=0.0) -> dict:
    return {"plays": plays, "epa": epa, "pass_plays": pass_plays, "pass_epa": pass_epa,
            "rush_plays": plays - pass_plays, "rush_epa": epa - pass_epa, "turnovers": turnovers}


def pair(game_id, start, a_id, b_id, a_half, b_half, a_final, b_final, a_off, b_off, first=None):
    return [
        HalfGame(game_id, a_id, start, a_half[0], a_half[1], a_final, b_final,
                 None if first is None else first == a_id, a_off),
        HalfGame(game_id, b_id, start, a_half[1], a_half[0], b_final, a_final,
                 None if first is None else first == b_id, b_off),
    ]


ROWS = [
    *pair(1, day(9, 13), AAA, BBB, (7, 10), (10, 7), 24, 20,
          offense(20, 1.0, 12, 0.8), offense(22, 4.4, 14, 4.0, turnovers=1.0), first=BBB),
    *pair(2, day(9, 20), AAA, CCC, (10, 14), (14, 10), 17, 27,
          offense(25, 2.5, 15, 2.5, turnovers=1.0), offense(20, 4.0, 12, 3.0), first=None),
    *pair(3, day(9, 27), AAA, DDD, (10, 10), (10, 10), 23, 23,
          offense(24, 3.6, 14, 3.0), offense(20, -1.0, 12, -0.6, turnovers=2.0), first=AAA),
    *pair(4, day(10, 4), AAA, BBB, (17, 3), (3, 17), 30, 10,
          offense(18, 3.6, 10, 3.0), offense(16, -0.8, 9, -0.4), first=AAA),
]


def aaa(rows=ROWS, before=KICKOFF, last_n=5):
    return season_first_half(rows, before, last_n)[AAA]


def test_points_margins_and_halftime_to_final_splits() -> None:
    fh = aaa()
    assert fh.games == 4
    assert fh.metrics["h1_points_for_pg"].value == 11.0  # (7 + 10 + 10 + 17) / 4
    assert fh.metrics["h1_points_against_pg"].value == 9.25  # (10 + 14 + 10 + 3) / 4
    assert fh.metrics["h1_margin_pg"].value == 1.75
    assert fh.second_half_margin_pg == 1.75  # final margins sum to +14, 1H margins to +7
    assert fh.led == Outcome(win=1) and fh.tied == Outcome(push=1)
    assert fh.trailed == Outcome(win=1, loss=1)


def test_first_half_efficiency_and_defense_is_the_opponents_offense() -> None:
    m = aaa().metrics
    assert m["h1_off_epa_play"].value == round(10.7 / 87, 3)  # 1.0 + 2.5 + 3.6 + 3.6 over 87 plays
    assert m["h1_def_epa_play"].value == round(6.6 / 78, 3)  # faced 4.4 + 4.0 - 1.0 - 0.8, 78 plays
    assert m["h1_off_pass_epa_play"].value == round(9.3 / 51, 3)
    assert m["h1_turnovers_pg"].value == 0.25 and m["h1_takeaways_pg"].value == 0.75


def test_scored_first_counts_only_games_where_it_is_known() -> None:
    fh = aaa()
    assert (fh.scored_first_games, fh.scored_first_known) == (2, 3)
    assert fh.scored_first_record == Outcome(win=1, push=1)  # beat BBB; tied DDD


def test_recent_margin_covers_only_the_last_n_games() -> None:
    assert aaa(last_n=2).recent_margin_pg == 7.0  # wk3 0 and wk4 +14


def test_only_final_games_before_kickoff_count() -> None:
    open_game = HalfGame(9, AAA, day(10, 8), 9, 0, None, None, True, offense(20, 5.0, 10, 4.0))
    assert aaa(rows=[*ROWS, open_game]).games == 4  # not final: no halftime-to-final result yet
    assert aaa(before=day(9, 27)).games == 2  # wk3 kicked off exactly then, so it is excluded


def test_ranks_run_one_to_best_and_direction_follows_the_metric() -> None:
    result = season_first_half(ROWS, KICKOFF)
    margin = {t: result[t].metrics["h1_margin_pg"] for t in result}
    assert margin[AAA].of == 4 and margin[CCC].value == 4.0  # CCC led its one game by 4
    assert {t: margin[t].rank for t in margin} == {CCC: 1, AAA: 2, DDD: 3, BBB: 4}
    # lower is better for points allowed, and a tie shares a rank: AAA allowed 9.25, CCC and DDD
    # 10.0 each, BBB (7 and 17) 12.0
    allowed = {t: result[t].metrics["h1_points_against_pg"] for t in result}
    assert {t: (allowed[t].value, allowed[t].rank) for t in allowed} == {
        AAA: (9.25, 1), CCC: (10.0, 2), DDD: (10.0, 2), BBB: (12.0, 4),
    }


def test_a_team_with_no_final_games_is_absent() -> None:
    assert season_first_half([], KICKOFF) == {}
