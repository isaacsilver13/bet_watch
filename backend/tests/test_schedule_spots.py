from datetime import datetime, timezone

from app.analytics.schedule_spots import ScheduleSpot, schedule_spot
from app.analytics.situational import Stop

SEASON = 2026
# 7:30pm Eastern on Thursday 2026-01-15, in UTC.
TIP = datetime(2026, 1, 16, 0, 30, tzinfo=timezone.utc)
LAL = dict(lat=34.0430, lon=-118.2673, tz="America/Los_Angeles")
GSW = dict(lat=37.7680, lon=-122.3877, tz="America/Los_Angeles")
BOS = dict(lat=42.3663, lon=-71.0622, tz="America/New_York")


def stop(month, day, hour=0, minute=30, opponent="X", season=SEASON, **where):
    # Tips are written in UTC; 00:30Z is 7:30pm Eastern the evening before.
    when = datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)
    return Stop(when, season, where.get("lat"), where.get("lon"), where.get("tz"), opponent)


def test_a_back_to_back_is_the_second_game_on_consecutive_eastern_days() -> None:
    previous = [stop(1, 15, opponent="DEN", **LAL)]  # tipped 7:30pm ET on the 14th
    spot = schedule_spot(previous, TIP, SEASON, **GSW)
    assert (spot.rest_days, spot.back_to_back, spot.previous_opponent) == (1, True, "DEN")
    assert spot.travel_miles == 345  # Los Angeles to San Francisco, as the crow flies
    assert spot.tz_crossed == 0
    assert (spot.three_in_four, spot.four_in_six, spot.games_last_7) == (False, False, 1)


def test_three_in_four_and_four_in_six_count_this_game() -> None:
    # Games on Jan 11, 12 and 14 (Eastern), and this one on the 15th.
    previous = [stop(1, 12, opponent="A"), stop(1, 13, opponent="B"), stop(1, 15, opponent="C")]
    spot = schedule_spot(previous, TIP, SEASON, **GSW)
    assert spot.back_to_back is True
    assert spot.three_in_four is True  # the 12th, 14th and 15th
    assert spot.four_in_six is True  # the 11th, 12th, 14th and 15th
    assert spot.games_last_7 == 3


def test_rest_is_days_between_games_and_a_long_gap_clears_every_flag() -> None:
    spot = schedule_spot([stop(1, 13, opponent="BOS", **BOS)], TIP, SEASON, **GSW)
    assert (spot.rest_days, spot.back_to_back) == (3, False)  # the 12th to the 15th
    assert spot.travel_miles == 2691 and spot.tz_crossed == 3  # Boston to San Francisco
    assert (spot.three_in_four, spot.four_in_six, spot.games_last_7) == (False, False, 1)


def test_the_first_game_of_a_season_has_no_rest_or_travel() -> None:
    assert schedule_spot([], TIP, SEASON, **GSW) == ScheduleSpot(
        None, False, False, False, 0, None, None, None)
    last_year = [stop(1, 15, season=SEASON - 1, **LAL)]
    assert schedule_spot(last_year, TIP, SEASON, **GSW).rest_days is None


def test_a_venue_without_coordinates_still_gets_the_rest_flags() -> None:
    spot = schedule_spot([stop(1, 15, opponent="MEM")], TIP, SEASON, None, None, None)
    assert spot.back_to_back is True
    assert (spot.travel_miles, spot.tz_crossed) == (None, None)
