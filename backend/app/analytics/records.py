"""SU / ATS / over-under records from final games. Pure: no I/O, and only games before `before`."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class GameResult:
    game_id: int
    season: int
    start_time: datetime
    home_id: int
    away_id: int
    home_score: int
    away_score: int
    spread_home: Optional[float]  # closing line; negative = home favored
    total: Optional[float]


@dataclass(frozen=True)
class Tally:
    """Wins/losses/pushes. For over-under, win = over and loss = under."""

    win: int = 0
    loss: int = 0
    push: int = 0

    @property
    def games(self) -> int:
        return self.win + self.loss + self.push


@dataclass(frozen=True)
class RecordSet:
    su: Tally
    ats: Tally
    ou: Tally


def prior(results: Sequence[GameResult], before: datetime) -> list[GameResult]:
    return sorted((r for r in results if r.start_time < before), key=lambda r: r.start_time)


def team_line(result: GameResult, team_id: int) -> Optional[float]:
    """The team's own spread: negative = this team was favored."""
    if result.spread_home is None:
        return None
    return result.spread_home if team_id == result.home_id else -result.spread_home


def _sign(value: float) -> str:
    return "W" if value > 0 else "L" if value < 0 else "P"


def su_result(result: GameResult, team_id: int) -> str:
    margin = result.home_score - result.away_score
    return _sign(margin if team_id == result.home_id else -margin)


def ats_result(result: GameResult, team_id: int) -> Optional[str]:
    line = team_line(result, team_id)
    if line is None:
        return None
    margin = result.home_score - result.away_score
    return _sign((margin if team_id == result.home_id else -margin) + line)


def ou_result(result: GameResult) -> Optional[str]:
    """W = over, L = under, P = push."""
    if result.total is None:
        return None
    return _sign(result.home_score + result.away_score - result.total)


def _tally(codes: Sequence[Optional[str]]) -> Tally:
    return Tally(codes.count("W"), codes.count("L"), codes.count("P"))


def summarize(results: Sequence[GameResult], team_id: int) -> RecordSet:
    return RecordSet(
        su=_tally([su_result(r, team_id) for r in results]),
        ats=_tally([ats_result(r, team_id) for r in results]),
        ou=_tally([ou_result(r) for r in results]),
    )


def build_records(
    results: Sequence[GameResult], team_id: int, season: int, before: datetime, last_n: int
) -> dict[str, RecordSet]:
    games = [r for r in prior(results, before) if team_id in (r.home_id, r.away_id)]
    this_season = [r for r in games if r.season == season]

    def favored(r: GameResult) -> Optional[bool]:
        line = team_line(r, team_id)
        return None if line is None or line == 0 else line < 0

    groups = {
        "season": this_season,
        "last_n": games[-last_n:] if last_n > 0 else [],  # may cross into last season
        "home": [r for r in this_season if r.home_id == team_id],
        "away": [r for r in this_season if r.away_id == team_id],
        "favorite": [r for r in this_season if favored(r) is True],
        "underdog": [r for r in this_season if favored(r) is False],
    }
    return {name: summarize(group, team_id) for name, group in groups.items()}


def head_to_head(
    results: Sequence[GameResult], team_id: int, opponent_id: int, before: datetime, n: int
) -> list[GameResult]:
    meetings = [
        r for r in prior(results, before) if {r.home_id, r.away_id} == {team_id, opponent_id}
    ]
    return meetings[-n:] if n > 0 else []
