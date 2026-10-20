"""ESPN public JSON: schedule, scores, and one book's (DraftKings) open/current/close lines.

Verified 2026-10-11:
- The scoreboard's odds "close" is the live line until kickoff, so it is stored as "current"
  while a game is "pre".
- The scoreboard drops odds once a game is final; the game summary's pickcenter keeps open and
  close in the same shape.
"""

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.ingest.records import (
    GameRecord,
    InjuryRecord,
    LineRecord,
    NbaPlayerBoxScoreRecord,
    NbaProviderGame,
    NbaTeamBoxScoreRecord,
)

SITE_API = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUE_PATHS = {"nfl": "football/nfl", "nba": "basketball/nba"}
SEASON_TYPES = {1: "PRE", 2: "REG", 3: "POST"}
_STATES = {"pre": "scheduled", "in": "in_progress", "post": "final"}
_SPECIAL_STATUSES = {"STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "canceled"}
# League injuries also lists depth-chart news as "Active" and retirements as "Reserve-Ret".
NOT_AVAILABILITY = {"Active", "Reserve-Ret"}
EASTERN = ZoneInfo("America/New_York")


class EspnNbaBoxScoreError(ValueError):
    pass


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


def parse_nba_completed_games(payload: Mapping) -> list[NbaProviderGame]:
    """Completed regular/postseason games from one ESPN scoreboard day."""
    events = payload.get("events")
    if not isinstance(events, list):
        raise EspnNbaBoxScoreError("ESPN scoreboard has no events list")
    games = []
    for event in events:
        if not isinstance(event, Mapping):
            raise EspnNbaBoxScoreError("ESPN scoreboard contains an invalid event")
        competition = (event.get("competitions") or [{}])[0]
        status_type = (competition.get("status") or event.get("status") or {}).get("type") or {}
        if _status(status_type) != "final":
            continue
        raw_season_type = (event.get("season") or {}).get("type")
        if raw_season_type not in SEASON_TYPES:
            raise EspnNbaBoxScoreError("completed ESPN game has unknown season type")
        season_type = SEASON_TYPES[raw_season_type]
        if season_type not in {"REG", "POST"}:
            continue
        sides = {row.get("homeAway"): row for row in competition.get("competitors") or []}
        home, away = sides.get("home") or {}, sides.get("away") or {}
        home_team, away_team = home.get("team") or {}, away.get("team") or {}
        if not all(
            (
                event.get("id"),
                event.get("date"),
                home_team.get("id"),
                home_team.get("abbreviation"),
                away_team.get("id"),
                away_team.get("abbreviation"),
            )
        ):
            raise EspnNbaBoxScoreError("completed ESPN game is missing date or team identity")
        games.append(
            NbaProviderGame(
                provider_game_id=str(event["id"]),
                game_date=parse_start_time(event["date"]).astimezone(EASTERN).date(),
                period=int(competition.get("status", {}).get("period") or 0),
                home_team=home_team["abbreviation"],
                away_team=away_team["abbreviation"],
                home_provider_team_id=str(home_team["id"]),
                away_provider_team_id=str(away_team["id"]),
            )
        )
    return games


def _nba_number(value: Any, field: str) -> float:
    if value in (None, "", "--"):
        raise EspnNbaBoxScoreError(f"completed ESPN box score is missing {field}")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise EspnNbaBoxScoreError(f"invalid ESPN {field}: {value!r}") from error


def _nba_minutes(value: Any) -> float:
    if value in (None, "", "--"):
        return 0.0
    text = str(value)
    if ":" not in text:
        return _nba_number(text, "minutes")
    minutes, seconds = text.split(":", 1)
    return round(_nba_number(minutes, "minutes") + _nba_number(seconds, "seconds") / 60, 3)


def _made_attempted(value: Any, field: str) -> tuple[float, float]:
    parts = str(value or "").split("-", 1)
    if len(parts) != 2:
        raise EspnNbaBoxScoreError(f"invalid ESPN {field}: {value!r}")
    return _nba_number(parts[0], field), _nba_number(parts[1], field)


def _espn_team_stats(row: Mapping) -> dict[str, Any]:
    stats = row.get("statistics")
    if not isinstance(stats, list):
        raise EspnNbaBoxScoreError("ESPN team box score has no statistics list")
    return {
        str(stat.get("name")): stat.get("displayValue", stat.get("value"))
        for stat in stats
        if isinstance(stat, Mapping) and stat.get("name")
    }


def parse_nba_box_score(
    game: NbaProviderGame, payload: Mapping
) -> tuple[list[NbaTeamBoxScoreRecord], list[NbaPlayerBoxScoreRecord]]:
    """Normalize one completed ESPN summary into append-only team/player facts."""
    boxscore = payload.get("boxscore") or {}
    team_sections, player_sections = boxscore.get("teams"), boxscore.get("players")
    if not isinstance(team_sections, list) or not isinstance(player_sections, list):
        raise EspnNbaBoxScoreError("ESPN summary has no team/player box score")

    expected = {game.home_provider_team_id, game.away_provider_team_id}
    opponent = {
        game.home_provider_team_id: game.away_team,
        game.away_provider_team_id: game.home_team,
    }
    team_names = {
        game.home_provider_team_id: game.home_team,
        game.away_provider_team_id: game.away_team,
    }

    competitions = (payload.get("header") or {}).get("competitions") or []
    competition = competitions[0] if competitions else {}
    scores = {}
    for side in competition.get("competitors") or []:
        team_id = str((side.get("team") or {}).get("id") or "")
        if team_id:
            scores[team_id] = _nba_number(side.get("score"), "score")
    if game.period < 4:
        raise EspnNbaBoxScoreError("completed ESPN game ended before four periods")
    game_minutes = 48.0 + (game.period - 4) * 5.0

    players = []
    player_team_ids = set()
    for section in player_sections:
        team = section.get("team") or {}
        team_id = str(team.get("id") or "")
        groups = section.get("statistics") or []
        if team_id not in expected or not groups:
            continue
        group = groups[0]
        labels = [str(label).upper() for label in group.get("labels") or []]
        if "MIN" not in labels or "PTS" not in labels:
            raise EspnNbaBoxScoreError("ESPN player box score is missing MIN or PTS")
        player_team_ids.add(team_id)
        for row in group.get("athletes") or []:
            athlete = row.get("athlete") or {}
            player_id = str(athlete.get("id") or "")
            player = str(athlete.get("displayName") or "").strip()
            if not player_id or not player:
                raise EspnNbaBoxScoreError("ESPN player row is missing identity")
            values = row.get("stats") or []
            by_label = dict(zip(labels, values))
            did_not_play = bool(row.get("didNotPlay")) or not values
            minutes = 0.0 if did_not_play else _nba_minutes(by_label.get("MIN"))
            points = 0.0 if did_not_play else _nba_number(by_label.get("PTS"), "player points")
            players.append(
                NbaPlayerBoxScoreRecord(
                    game.provider_game_id,
                    game.game_date,
                    str(team.get("abbreviation") or team_names[team_id]),
                    opponent[team_id],
                    team_id,
                    player_id,
                    player,
                    minutes,
                    {"PTS": points, "MIN": minutes},
                )
            )
    if player_team_ids != expected:
        raise EspnNbaBoxScoreError("ESPN player response does not contain both teams")

    raw = {}
    names = {}
    for row in team_sections:
        team = row.get("team") or {}
        team_id = str(team.get("id") or "")
        if team_id not in expected:
            continue
        stats = _espn_team_stats(row)
        fgm, fga = _made_attempted(stats.get("fieldGoalsMade-fieldGoalsAttempted"), "field goals")
        fg3m, _ = _made_attempted(
            stats.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
            "three-pointers",
        )
        ftm, fta = _made_attempted(stats.get("freeThrowsMade-freeThrowsAttempted"), "free throws")
        raw[team_id] = {
            "PTS": scores.get(team_id),
            "FGM": fgm,
            "FGA": fga,
            "FTM": ftm,
            "FTA": fta,
            "FG3M": fg3m,
            "OREB": _nba_number(stats.get("offensiveRebounds"), "offensive rebounds"),
            "DREB": _nba_number(stats.get("defensiveRebounds"), "defensive rebounds"),
            "TOV": _nba_number(stats.get("totalTurnovers", stats.get("turnovers")), "turnovers"),
        }
        if raw[team_id]["PTS"] is None:
            raise EspnNbaBoxScoreError("ESPN header does not contain both final scores")
        names[team_id] = str(team.get("abbreviation") or team_names[team_id])
    if set(raw) != expected:
        raise EspnNbaBoxScoreError("ESPN team response does not contain both teams")

    teams = []
    for team_id, opponent_id in (
        (game.home_provider_team_id, game.away_provider_team_id),
        (game.away_provider_team_id, game.home_provider_team_id),
    ):
        own, opp = raw[team_id], raw[opponent_id]
        possessions = own["FGA"] - own["OREB"] + own["TOV"] + 0.44 * own["FTA"]
        opp_possessions = opp["FGA"] - opp["OREB"] + opp["TOV"] + 0.44 * opp["FTA"]
        if possessions <= 0 or opp_possessions <= 0 or own["FGA"] <= 0:
            raise EspnNbaBoxScoreError("completed ESPN game has insufficient team inputs")
        own_oreb_chances = own["OREB"] + opp["DREB"]
        opp_oreb_chances = opp["OREB"] + own["DREB"]
        stats = {
            **own,
            "MIN": game_minutes,
            "POSS": possessions,
            "OFF_RATING": own["PTS"] / possessions * 100,
            "DEF_RATING": opp["PTS"] / opp_possessions * 100,
            "PACE": (possessions + opp_possessions) / 2 * 48 / game_minutes,
            "EFG_PCT": (own["FGM"] + 0.5 * own["FG3M"]) / own["FGA"],
            "TM_TOV_PCT": own["TOV"] / possessions,
            "OREB_PCT": own["OREB"] / own_oreb_chances if own_oreb_chances else 0.0,
            "FTA_RATE": own["FTA"] / own["FGA"],
            "OPP_EFG_PCT": (opp["FGM"] + 0.5 * opp["FG3M"]) / opp["FGA"],
            "OPP_TOV_PCT": opp["TOV"] / opp_possessions,
            "OPP_OREB_PCT": opp["OREB"] / opp_oreb_chances if opp_oreb_chances else 0.0,
            "OPP_FTA_RATE": opp["FTA"] / opp["FGA"],
        }
        stats["NET_RATING"] = stats["OFF_RATING"] - stats["DEF_RATING"]
        teams.append(
            NbaTeamBoxScoreRecord(
                game.provider_game_id,
                game.game_date,
                names[team_id],
                names[opponent_id],
                team_id,
                {key: round(value, 6) for key, value in stats.items()},
            )
        )
    return teams, players


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


async def fetch_scoreboard_day(http: httpx.AsyncClient, sport: str, day: date) -> dict:
    """One day's games (ESPN's day is the US Eastern date), for sports with no weeks."""
    response = await http.get(
        f"{SITE_API}/{LEAGUE_PATHS[sport]}/scoreboard", params={"dates": day.strftime("%Y%m%d")}
    )
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
    teams = payload.get("injuries")
    if not isinstance(teams, list):
        raise ValueError("ESPN injury payload has no injuries list")
    records = []
    for team in teams:
        if not isinstance(team, Mapping) or not team.get("id"):
            raise ValueError("ESPN injury payload contains a team without identity")
        entries = team.get("injuries")
        if not isinstance(entries, list):
            raise ValueError("ESPN injury payload contains a team without an injuries list")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValueError("ESPN injury payload contains a malformed injury row")
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
