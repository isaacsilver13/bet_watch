from datetime import datetime, timezone

from app.analytics.situational import (
    Stop,
    great_circle_miles,
    is_primetime,
    rest_days,
    situation,
    tz_hours_crossed,
)

SUN_1PM = datetime(2026, 10, 4, 17, tzinfo=timezone.utc)      # Sunday 1pm EDT
THU_8PM = datetime(2026, 10, 8, 20, 15, tzinfo=timezone.utc)  # Thursday 4:15pm EDT


def test_kansas_city_to_buffalo_is_about_858_miles() -> None:
    miles = great_circle_miles(39.0489, -94.4839, 42.7738, -78.7870)
    assert 856 <= miles <= 859  # ~858 by flight-distance tables


def test_thursday_after_sunday_is_a_short_week() -> None:
    assert rest_days(SUN_1PM, THU_8PM) == 4
    prev = Stop(SUN_1PM, 2026, 39.0, -94.5, "America/Chicago", "LV")
    s = situation(prev, THU_8PM, 2026, 42.77, -78.79, "America/New_York")
    assert s.short_week and not s.off_bye and s.rest_days == 4
    assert s.tz_crossed == 1 and s.previous_opponent == "LV"
    assert 855 <= s.travel_miles <= 862


def test_bye_week_is_a_fourteen_day_gap_in_the_same_season() -> None:
    sunday_after_bye = datetime(2026, 10, 18, 17, tzinfo=timezone.utc)
    prev = Stop(SUN_1PM, 2026, 39.0, -94.5, "America/Chicago")
    s = situation(prev, sunday_after_bye, 2026, 39.0, -94.5, "America/Chicago")
    assert s.rest_days == 14 and s.off_bye and not s.short_week
    assert s.travel_miles == 0 and s.tz_crossed == 0


def test_season_opener_after_last_years_finale_is_not_a_bye() -> None:
    last_year = Stop(datetime(2026, 1, 4, 18, tzinfo=timezone.utc), 2025, None, None, None)
    opener = datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc)
    s = situation(last_year, opener, 2026, 1.0, 1.0, None)
    assert not s.off_bye and not s.short_week
    assert s.travel_miles is None and s.tz_crossed is None  # unknown venue data stays unknown


def test_no_previous_game_gives_empty_situation() -> None:
    s = situation(None, THU_8PM, 2026, 1.0, 1.0, "America/Chicago")
    assert s.rest_days is None and not s.short_week and s.travel_miles is None


def test_arizona_has_no_dst_so_the_gap_to_pacific_changes() -> None:
    summer = datetime(2026, 9, 13, 20, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 13, 20, tzinfo=timezone.utc)
    assert tz_hours_crossed("America/Phoenix", "America/Los_Angeles", summer) == 0
    assert tz_hours_crossed("America/Phoenix", "America/Los_Angeles", winter) == 1


def test_primetime_is_seven_pm_eastern_or_later() -> None:
    assert is_primetime(datetime(2026, 10, 12, 0, 15, tzinfo=timezone.utc))      # Sun 8:15pm ET
    assert not is_primetime(datetime(2026, 10, 11, 20, 25, tzinfo=timezone.utc))  # 4:25pm ET


def test_a_game_from_last_season_gives_no_rest_travel_or_opponent() -> None:
    finale = Stop(datetime(2026, 1, 4, 18, tzinfo=timezone.utc), 2025, 39.0, -94.5,
                  "America/Chicago", "LV")
    opener = datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc)
    s = situation(finale, opener, 2026, 42.77, -78.79, "America/New_York")
    assert s == situation(None, opener, 2026, 42.77, -78.79, "America/New_York")
    assert s.rest_days is None and s.travel_miles is None and s.previous_opponent is None
