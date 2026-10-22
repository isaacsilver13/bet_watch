"""Scheduled slots in Central time. zoneinfo handles DST, so trigger times never need to."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
TERMINAL_STATES = frozenset({"done", "empty", "missed", "skipped_quota", "failed"})


@dataclass(frozen=True)
class Slot:
    key: str
    window_start: datetime
    window_end: datetime


def hourly_slots(
    job: str, now: datetime, first_hour: int = 8, last_hour: int = 23, step: int = 1
) -> list[Slot]:
    day = now.astimezone(CENTRAL).date()
    slots = []
    for hour in range(first_hour, last_hour + 1, step):
        start = datetime.combine(day, time(hour), CENTRAL)
        if start > now:
            break
        window = timedelta(hours=step)
        slots.append(Slot(f"{job}:{day.isoformat()}T{hour:02d}", start, start + window))
    return slots


def daily_slot(
    job: str,
    now: datetime,
    start_hour: int = 8,
    weekday: Optional[int] = None,
    start_minute: int = 0,
) -> list[Slot]:
    day = now.astimezone(CENTRAL).date()
    if weekday is not None and day.weekday() != weekday:
        return []
    start = datetime.combine(day, time(start_hour, start_minute), CENTRAL)
    end = datetime.combine(day + timedelta(days=1), time(0), CENTRAL)
    return [Slot(f"{job}:{day.isoformat()}", start, end)] if now >= start else []
