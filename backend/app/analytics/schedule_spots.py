"""NBA rest and schedule spots: back-to-backs, 3-in-4, 4-in-6, travel. Pure: no I/O.

Days are Eastern calendar days, the league's own convention, so a 10pm tip and a 7pm tip the
next evening are consecutive days.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from app.analytics.situational import EASTERN, Stop, great_circle_miles, tz_hours_crossed


@dataclass(frozen=True)
class ScheduleSpot:
    rest_days: Optional[int]  # days between the last game and this one; a back-to-back is 1
    back_to_back: bool
    three_in_four: bool  # this is the third game in four days
    four_in_six: bool  # this is the fourth game in six days
    games_last_7: int  # games played in the seven days before this one
    travel_miles: Optional[int]
    tz_crossed: Optional[int]
    previous_opponent: Optional[str]


def _day(when: datetime) -> date:
    return when.astimezone(EASTERN).date()


def schedule_spot(
    previous: Sequence[Stop],
    kickoff: datetime,
    season: int,
    lat: Optional[float],
    lon: Optional[float],
    tz: Optional[str],
) -> ScheduleSpot:
    """`previous` holds the team's earlier games; lat/lon/tz are this game's arena."""
    today = _day(kickoff)
    played = sorted(
        (stop for stop in previous if stop.season == season and stop.kickoff < kickoff),
        key=lambda stop: stop.kickoff,
    )
    if not played:
        # No earlier game this season: last year's finale is an offseason, not rest or travel.
        return ScheduleSpot(None, False, False, False, 0, None, None, None)
    last = played[-1]
    days = [_day(stop.kickoff) for stop in played]

    def games_within(span: int) -> int:  # this game plus earlier ones in the last `span` days
        return 1 + sum(1 for d in days if today - d < timedelta(days=span))

    gap = (today - days[-1]).days
    miles = None
    if None not in (last.lat, last.lon, lat, lon):
        miles = round(great_circle_miles(last.lat, last.lon, lat, lon))
    return ScheduleSpot(
        rest_days=gap,
        back_to_back=gap == 1,
        three_in_four=games_within(4) >= 3,
        four_in_six=games_within(6) >= 4,
        games_last_7=sum(1 for d in days if today - d <= timedelta(days=7)),
        travel_miles=miles,
        tz_crossed=tz_hours_crossed(last.tz, tz, kickoff),
        previous_opponent=last.opponent,
    )
