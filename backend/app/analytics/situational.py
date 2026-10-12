"""Rest, travel and schedule-spot flags. Pure: no I/O."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
EARTH_RADIUS_MILES = 3958.8
SHORT_WEEK_DAYS = 5  # Thursday after a Sunday is 4; a normal week is 7
BYE_DAYS = 13  # a bye is a 14-day gap; 13 absorbs a Thursday/Monday mismatch
PRIMETIME_HOUR = 19  # Eastern kickoff at or after 7pm


@dataclass(frozen=True)
class Stop:
    """Where and when a team last played."""

    kickoff: datetime
    season: int
    lat: Optional[float]
    lon: Optional[float]
    tz: Optional[str]
    opponent: Optional[str] = None


@dataclass(frozen=True)
class Situation:
    rest_days: Optional[int]
    short_week: bool
    off_bye: bool
    travel_miles: Optional[int]
    tz_crossed: Optional[int]
    previous_opponent: Optional[str]


def great_circle_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat, d_lon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def rest_days(previous: datetime, kickoff: datetime) -> int:
    """Calendar days between kickoffs, in Eastern time (nflverse's own convention)."""
    return (kickoff.astimezone(EASTERN).date() - previous.astimezone(EASTERN).date()).days


def tz_hours_crossed(from_tz: Optional[str], to_tz: Optional[str], at: datetime) -> Optional[int]:
    if not from_tz or not to_tz:
        return None
    start = at.astimezone(ZoneInfo(from_tz)).utcoffset()
    end = at.astimezone(ZoneInfo(to_tz)).utcoffset()
    return int(abs((end - start).total_seconds()) // 3600)


def is_primetime(kickoff: datetime) -> bool:
    return kickoff.astimezone(EASTERN).hour >= PRIMETIME_HOUR


def situation(
    previous: Optional[Stop],
    kickoff: datetime,
    season: int,
    lat: Optional[float],
    lon: Optional[float],
    tz: Optional[str],
) -> Situation:
    """`previous` is the team's last game before `kickoff`; lat/lon/tz are this game's venue."""
    if previous is None or previous.season != season:
        # No earlier game this season: last year's finale is an offseason, not rest or travel.
        return Situation(None, False, False, None, None, None)
    days = rest_days(previous.kickoff, kickoff)
    miles = None
    if None not in (previous.lat, previous.lon, lat, lon):
        miles = round(great_circle_miles(previous.lat, previous.lon, lat, lon))
    return Situation(
        rest_days=days,
        short_week=days <= SHORT_WEEK_DAYS,
        off_bye=days >= BYE_DAYS,
        travel_miles=miles,
        tz_crossed=tz_hours_crossed(previous.tz, tz, kickoff),
        previous_opponent=previous.opponent,
    )
