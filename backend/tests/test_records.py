from datetime import datetime, timedelta, timezone

from app.analytics.records import (
    GameResult,
    Tally,
    ats_result,
    build_records,
    head_to_head,
    ou_result,
    su_result,
)

T0 = datetime(2026, 9, 6, 17, tzinfo=timezone.utc)
A, B, C = 1, 2, 3


def game(n, home, away, hs, as_, spread=None, total=None, season=2026):
    return GameResult(game_id=n, season=season, start_time=T0 + timedelta(days=7 * n),
                      home_id=home, away_id=away, home_score=hs, away_score=as_,
                      spread_home=spread, total=total)


def test_ats_push_when_home_wins_by_the_spread() -> None:
    push = game(1, A, B, 24, 21, spread=-3.0)  # home favored by 3, wins by 3
    assert ats_result(push, A) == "P" and ats_result(push, B) == "P"
    assert su_result(push, A) == "W" and su_result(push, B) == "L"


def test_ats_cover_and_miss_for_each_side() -> None:
    g = game(1, A, B, 27, 21, spread=-3.5)  # home -3.5 wins by 6: home covers
    assert (ats_result(g, A), ats_result(g, B)) == ("W", "L")
    dog = game(2, A, B, 20, 21, spread=2.5)  # home +2.5 loses by 1: home covers
    assert (ats_result(dog, A), ats_result(dog, B)) == ("W", "L")


def test_missing_line_counts_for_su_only() -> None:
    g = game(1, A, B, 20, 10)
    assert ats_result(g, A) is None and ou_result(g) is None
    records = build_records([g], A, 2026, T0 + timedelta(days=30), 5)
    assert (records["season"].su, records["season"].ats) == (Tally(1, 0, 0), Tally())


def test_over_under_push() -> None:
    assert ou_result(game(1, A, B, 24, 20, total=44.0)) == "P"
    assert ou_result(game(1, A, B, 24, 21, total=44.0)) == "W"  # 45 > 44 = over
    assert ou_result(game(1, A, B, 20, 21, total=44.0)) == "L"


def test_only_games_before_the_viewed_one_are_used() -> None:
    results = [game(1, A, B, 20, 10, -1), game(2, C, A, 10, 17, 3), game(3, A, B, 3, 30, -7)]
    viewed = results[2].start_time  # week 3 itself and later must be excluded
    season = build_records(results, A, 2026, viewed, 5)["season"]
    assert season.su == Tally(2, 0, 0)


def test_splits_home_away_favorite_underdog_and_last_n_across_seasons() -> None:
    results = [
        game(-5, A, B, 10, 20, -3, season=2025),      # last season: only counts for last_n
        game(1, A, B, 20, 10, -3),                    # home favorite, covers
        game(2, C, A, 10, 14, 1),                     # away (A is -1 favorite): covers
        game(3, A, C, 10, 13, 4),                     # home underdog +4, loses by 3: covers
        game(4, B, A, 17, 17, 0),                     # pick'em tie: neither favorite nor dog
    ]
    before = T0 + timedelta(days=100)
    r = build_records(results, A, 2026, before, 3)
    assert r["season"].su == Tally(2, 1, 1)               # W, W, L (game 3), tie
    assert (r["home"].su, r["away"].su) == (Tally(1, 1, 0), Tally(1, 0, 1))
    assert r["favorite"].ats == Tally(2, 0, 0)            # games 1 and 2
    assert r["underdog"].ats == Tally(1, 0, 0)            # game 3; the pick'em is in neither
    assert r["last_n"].su == Tally(1, 1, 1)               # games 2, 3, 4
    assert build_records(results, A, 2026, before, 5)["last_n"].su == Tally(2, 2, 1)


def test_head_to_head_is_the_latest_n_meetings_either_venue() -> None:
    results = [game(1, A, B, 1, 0), game(2, B, A, 1, 0), game(3, A, C, 1, 0), game(4, B, A, 1, 0)]
    before = T0 + timedelta(days=100)
    assert [g.game_id for g in head_to_head(results, A, B, before, 2)] == [2, 4]
    assert head_to_head(results, A, B, before, 0) == []
