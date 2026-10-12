"""nflverse schedules (games.csv, CC-BY-4.0): NFL history with closing lines since 1999.

nflverse spread_line is positive when the HOME team is favored, so spread_home = -spread_line.
Its lines are an unverified close (no timestamp), so they are only attached to final games.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.ingest.records import GameRecord, LineRecord

GAMES_CSV_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
POST_WEEKS = {"WC": 1, "DIV": 2, "CON": 3, "SB": 5}  # matches ESPN's postseason week numbers
EASTERN = ZoneInfo("America/New_York")


def _blank(value: Optional[str]) -> bool:
    return value is None or value.strip() in {"", "NA"}


def _int(value: Optional[str]) -> Optional[int]:
    return None if _blank(value) else int(float(value))


def _float(value: Optional[str]) -> Optional[float]:
    return None if _blank(value) else float(value)


def parse_games_csv(text: str, min_season: int = 2015) -> list[GameRecord]:
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        season = int(row["season"])
        game_type = row["game_type"]
        week = int(row["week"]) if game_type == "REG" else POST_WEEKS.get(game_type)
        if season < min_season or _blank(row.get("espn")) or week is None:
            continue
        kickoff = datetime.strptime(
            f"{row['gameday']} {row['gametime'] or '13:00'}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=EASTERN)
        home_score, away_score = _int(row["home_score"]), _int(row["away_score"])
        final = home_score is not None and away_score is not None
        spread_line = _float(row["spread_line"])
        close = LineRecord(
            kind="close",
            spread_home=None if spread_line is None else -spread_line + 0.0,
            spread_home_price=_int(row["home_spread_odds"]),
            spread_away_price=_int(row["away_spread_odds"]),
            total=_float(row["total_line"]),
            over_price=_int(row["over_odds"]),
            under_price=_int(row["under_odds"]),
            ml_home=_int(row["home_moneyline"]),
            ml_away=_int(row["away_moneyline"]),
        )
        records.append(
            GameRecord(
                espn_id=row["espn"].strip(),
                season=season,
                season_type="REG" if game_type == "REG" else "POST",
                week=week,
                start_time=kickoff.astimezone(timezone.utc),
                status="final" if final else "scheduled",
                home_name=row["home_team"],
                away_name=row["away_team"],
                home_score=home_score,
                away_score=away_score,
                neutral=row["location"] == "Neutral",
                venue_name=row.get("stadium") or None,
                lines=(close,) if final and not close.is_empty() else (),
            )
        )
    return records
