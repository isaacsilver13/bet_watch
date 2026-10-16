"""nflverse injury reports (injuries_<season>.csv, CC-BY-4.0): one row per team, week and player.

Verified 2026-10-13 on the 2025 file (6,068 rows): `report_status` is the game-day designation
(Out, Doubtful, Questionable, or blank); `practice_status` is Full, Limited or Did Not
Participate, and its injury column says "Not injury related - resting player" for a rested
veteran. The file holds the whole season, so the week being played is the newest regular-season
week. Team codes are nflverse's (Rams LA, Raiders LV), resolved through team_aliases.
"""

import csv
import io
from typing import Optional

from app.ingest.records import NamedInjury

INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/injuries/"
    "injuries_{season}.csv"
)
GAME_STATUSES = {"out": "Out", "doubtful": "Doubtful", "questionable": "Questionable"}
PRACTICE_TAGS = {
    "did not participate in practice": "DNP",
    "limited participation in practice": "Limited",
}
NOT_AN_INJURY = "not injury related"


def _injury(*parts: str) -> Optional[str]:
    kept = [
        part.strip() for part in parts
        if part.strip() and not part.strip().lower().startswith(NOT_AN_INJURY)
    ]
    return ", ".join(kept) or None


def _record(row: dict[str, str]) -> Optional[NamedInjury]:
    status = GAME_STATUSES.get(row["report_status"].strip().lower())
    practice = PRACTICE_TAGS.get(row["practice_status"].strip().lower())
    reported = _injury(row["report_primary_injury"], row["report_secondary_injury"])
    practiced = _injury(row["practice_primary_injury"], row["practice_secondary_injury"])
    if status is None:
        if practice is None or practiced is None:
            return None  # healthy, resting, or a full practice
        status = "Questionable"
    detail = reported or practiced
    if practice:
        detail = f"{detail} · {practice} in practice" if detail else f"{practice} in practice"
    return NamedInjury(
        team=row["team"],
        player=row["full_name"],
        position=row["position"] or None,
        status=status,
        detail=detail,
    )


def parse_injuries_csv(text: str) -> tuple[int, list[NamedInjury]]:
    rows = [row for row in csv.DictReader(io.StringIO(text)) if row["game_type"] == "REG"]
    if not rows:
        return 0, []
    week = max(int(row["week"]) for row in rows)
    records = []
    for row in rows:
        if int(row["week"]) == week:
            record = _record(row)
            if record is not None:
                records.append(record)
    return week, records
