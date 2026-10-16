"""stats.nba.com team game logs: one row per team per game, advanced plus four factors (free).

Fetched with plain httpx and browser-like headers (the site rejects bare clients), not the
nba_api package. Joined on game and team into one stats bag per team game.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

TEAM_GAME_LOGS_URL = "https://stats.nba.com/stats/teamgamelogs"
SEASON_TYPES = ("Regular Season", "PlayIn", "Playoffs")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}
STAT_FIELDS = (
    # Advanced
    "MIN", "OFF_RATING", "DEF_RATING", "NET_RATING", "PACE", "POSS", "EFG_PCT", "TS_PCT",
    "TM_TOV_PCT", "OREB_PCT", "DREB_PCT",
    # Four factors; the team's own eFG%, turnover rate and rebounding are already above
    "FTA_RATE", "OPP_EFG_PCT", "OPP_FTA_RATE", "OPP_TOV_PCT", "OPP_OREB_PCT",
)


@dataclass(frozen=True)
class GameLogRecord:
    season: int  # ESPN's convention: the year the season ends
    game_date: date  # the US Eastern date, as stats.nba.com writes it
    team: str
    opponent: str
    stats: Mapping[str, float]


def season_label(season: int) -> str:
    """2026 -> '2025-26'."""
    return f"{season - 1}-{season % 100:02d}"


def _rows(payload: Mapping) -> list[dict[str, Any]]:
    result = (payload.get("resultSets") or [{}])[0]
    headers = result.get("headers") or []
    return [dict(zip(headers, row)) for row in result.get("rowSet") or []]


def parse_team_game_logs(advanced: Mapping, four_factors: Mapping) -> list[GameLogRecord]:
    """Advanced rows are the spine; a game missing from the four factors keeps its advanced
    numbers."""
    factors = {(r["GAME_ID"], r["TEAM_ID"]): r for r in _rows(four_factors)}
    records = []
    for row in _rows(advanced):
        merged = {**factors.get((row["GAME_ID"], row["TEAM_ID"]), {}), **row}
        stats = {
            field: float(merged[field]) for field in STAT_FIELDS if merged.get(field) is not None
        }
        opponent = row["MATCHUP"].replace(" vs. ", " @ ").split(" @ ")[1]
        records.append(
            GameLogRecord(
                season=int(row["SEASON_YEAR"][:4]) + 1,
                game_date=date.fromisoformat(row["GAME_DATE"][:10]),
                team=row["TEAM_ABBREVIATION"],
                opponent=opponent,
                stats=stats,
            )
        )
    return records


async def fetch_team_game_logs(
    http: httpx.AsyncClient, season: int, season_type: str, measure: str
) -> dict:
    response = await http.get(
        TEAM_GAME_LOGS_URL,
        params={
            "MeasureType": measure,
            "PerMode": "Totals",
            "Season": season_label(season),
            "SeasonType": season_type,
            "LeagueID": "00",
        },
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


PLAYER_STATS_URL = "https://stats.nba.com/stats/leaguedashplayerstats"


@dataclass(frozen=True)
class PlayerStatRecord:
    team: str  # the abbreviation stats.nba.com writes, resolved through team_aliases
    player: str
    games: int
    minutes_pg: float


def parse_player_stats(payload: Mapping) -> list[PlayerStatRecord]:
    """One row per player on his current team; a row without a team or minutes is skipped."""
    return [
        PlayerStatRecord(
            row["TEAM_ABBREVIATION"], row["PLAYER_NAME"], int(row["GP"]), float(row["MIN"])
        )
        for row in _rows(payload)
        if row.get("TEAM_ABBREVIATION") and row.get("GP") and row.get("MIN") is not None
    ]


async def fetch_player_stats(http: httpx.AsyncClient, season: int) -> dict:
    """Per-game numbers for the regular season (verified 2026-10-13: ~580 rows, 180 KB)."""
    response = await http.get(
        PLAYER_STATS_URL,
        params={
            "MeasureType": "Base",
            "PerMode": "PerGame",
            "Season": season_label(season),
            "SeasonType": "Regular Season",
            "LeagueID": "00",
            "LastNGames": 0,
            "Month": 0,
            "OpponentTeamID": 0,
            "PaceAdjust": "N",
            "Period": 0,
            "PlusMinus": "N",
            "Rank": "N",
        },
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()
