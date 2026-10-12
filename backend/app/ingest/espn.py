"""ESPN public JSON: schedule, scores, and one book's (DraftKings) open/current/close lines.

Verified 2026-10-11:
- The scoreboard's odds "close" is the live line until kickoff, so it is stored as "current"
  while a game is "pre".
- The scoreboard drops odds once a game is final; the game summary's pickcenter keeps open and
  close in the same shape.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.ingest.records import GameRecord, InjuryRecord, LineRecord

SITE_API = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUE_PATHS = {"nfl": "football/nfl"}
SEASON_TYPES = {1: "PRE", 2: "REG", 3: "POST"}
_STATES = {"pre": "scheduled", "in": "in_progress", "post": "final"}
_SPECIAL_STATUSES = {"STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "canceled"}
# League injuries also lists depth-chart news as "Active" and retirements as "Reserve-Ret".
NOT_AVAILABILITY = {"Active", "Reserve-Ret"}


def parse_american(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"EVEN", "EV"}:
        return 100
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_line(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().lower().lstrip("ou")
    if text in {"pk", "even"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def _quote(market: Mapping, side: str, kind: str) -> Mapping:
    return ((market or {}).get(side) or {}).get(kind) or {}


def _line(odds: Mapping, espn_kind: str, kind: str) -> Optional[LineRecord]:
    spread = odds.get("pointSpread") or {}
    total = odds.get("total") or {}
    money = odds.get("moneyline") or {}
    record = LineRecord(
        kind=kind,
        spread_home=parse_line(_quote(spread, "home", espn_kind).get("line")),
        spread_home_price=parse_american(_quote(spread, "home", espn_kind).get("odds")),
        spread_away_price=parse_american(_quote(spread, "away", espn_kind).get("odds")),
        total=parse_line(_quote(total, "over", espn_kind).get("line")),
        over_price=parse_american(_quote(total, "over", espn_kind).get("odds")),
        under_price=parse_american(_quote(total, "under", espn_kind).get("odds")),
        ml_home=parse_american(_quote(money, "home", espn_kind).get("odds")),
        ml_away=parse_american(_quote(money, "away", espn_kind).get("odds")),
    )
    return None if record.is_empty() else record


def parse_odds(odds: Mapping, game_started: bool) -> tuple[LineRecord, ...]:
    lines = (
        _line(odds, "open", "open"),
        _line(odds, "close", "close" if game_started else "current"),
    )
    return tuple(line for line in lines if line is not None)


def _status(status_type: Mapping) -> str:
    name = status_type.get("name", "")
    if name in _SPECIAL_STATUSES:
        return _SPECIAL_STATUSES[name]
    return _STATES.get(status_type.get("state", ""), "scheduled")


def _score(value: Any, started: bool) -> Optional[int]:
    if not started or value in (None, ""):
        return None
    return int(float(value))


def parse_start_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_scoreboard(payload: Mapping) -> list[GameRecord]:
    games = []
    for event in payload.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        status_type = (competition.get("status") or event.get("status") or {}).get("type") or {}
        status = _status(status_type)
        started = status in {"in_progress", "final"}
        sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        season = event.get("season") or {}
        venue = competition.get("venue") or {}
        odds = (competition.get("odds") or [None])[0]
        games.append(
            GameRecord(
                espn_id=str(event["id"]),
                season=int(season["year"]),
                season_type=SEASON_TYPES.get(season.get("type"), "REG"),
                week=(event.get("week") or {}).get("number"),
                start_time=parse_start_time(event["date"]),
                status=status,
                home_name=home["team"]["abbreviation"],
                away_name=away["team"]["abbreviation"],
                home_score=_score(home.get("score"), started),
                away_score=_score(away.get("score"), started),
                neutral=bool(competition.get("neutralSite")),
                venue_espn_id=str(venue["id"]) if venue.get("id") else None,
                venue_name=venue.get("fullName"),
                venue_indoor=venue.get("indoor"),
                # Once a game starts, the scoreboard's "close" can be a live in-game price; the true
                # close comes only from the summary's pickcenter (espn_closes).
                lines=tuple(
                    line
                    for line in (parse_odds(odds, started) if odds else ())
                    if line.kind != "close"
                ),
            )
        )
    return games


def parse_pickcenter(summary: Mapping) -> tuple[LineRecord, ...]:
    picks = summary.get("pickcenter") or []
    return parse_odds(picks[0], game_started=True) if picks else ()


async def fetch_scoreboard(
    http: httpx.AsyncClient,
    sport: str,
    season: Optional[int] = None,
    season_type: Optional[int] = None,
    week: Optional[int] = None,
) -> dict:
    params = {}
    if season is not None:
        params["dates"] = str(season)
    if season_type is not None:
        params["seasontype"] = str(season_type)
    if week is not None:
        params["week"] = str(week)
    response = await http.get(f"{SITE_API}/{LEAGUE_PATHS[sport]}/scoreboard", params=params)
    response.raise_for_status()
    return response.json()


async def fetch_summary(http: httpx.AsyncClient, sport: str, espn_id: str) -> dict:
    response = await http.get(
        f"{SITE_API}/{LEAGUE_PATHS[sport]}/summary", params={"event": espn_id}
    )
    response.raise_for_status()
    return response.json()


def _injury_detail(details: Mapping) -> Optional[str]:
    parts = [p for p in (details.get("type"), details.get("detail")) if p and p != "Not Specified"]
    return ", ".join(parts) or None


def _report_time(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(value or "", "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_injuries(payload: Mapping) -> list[InjuryRecord]:
    records = []
    for team in payload.get("injuries") or []:
        for entry in team.get("injuries") or []:
            athlete = entry.get("athlete") or {}
            status = entry.get("status")
            if not status or status in NOT_AVAILABILITY or not athlete.get("displayName"):
                continue
            records.append(
                InjuryRecord(
                    team_espn_id=str(team["id"]),
                    player=athlete["displayName"],
                    position=(athlete.get("position") or {}).get("abbreviation"),
                    status=status,
                    detail=_injury_detail(entry.get("details") or {}),
                    reported_at=_report_time(entry.get("date")),
                )
            )
    return records


async def fetch_injuries(http: httpx.AsyncClient, sport: str) -> dict:
    response = await http.get(f"{SITE_API}/{LEAGUE_PATHS[sport]}/injuries")
    response.raise_for_status()
    return response.json()
