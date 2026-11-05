"""A small hand-worked nflverse play-by-play file for the first-half tests."""

import csv
import io

from app.ingest.nflverse_pbp import COLUMNS


def play(**values: object) -> dict:
    row = {name: "" for name in COLUMNS}
    row.update(values)
    return row


def csv_text(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[*COLUMNS, "desc"])
    writer.writeheader()
    for row in rows:
        writer.writerow({"desc": "a play, with a comma", **row})
    return buffer.getvalue()


def den_sea_week6(game_id: str = "2026_06_SEA_DEN") -> list[dict]:
    """DEN (home) kicks a field goal first, SEA scores a touchdown, so DEN 3 - SEA 7 at the half.

    DEN first-half offense: 3 plays, EPA -2.7, 1 success, 12 yards, pass 2 (EPA -2.5), rush 1
    (EPA -0.2), 1 turnover (the interception), no sacks taken.
    SEA first-half offense: 3 plays, EPA -0.3, 2 successes, pass 2 (EPA -0.7), rush 1 (EPA 0.4),
    1 turnover (a fumble lost on the sack) and 1 sack taken. A SEA fumble on a punt is not an
    offensive turnover, a play with no EPA is not a play, and nothing after the half counts.
    """
    base = dict(game_id=game_id, season=2026, season_type="REG", week=6,
                home_team="DEN", away_team="SEA")

    def row(half, posteam, defteam, home=0, away=0, **more):
        return play(**base, game_half=half, posteam=posteam, defteam=defteam,
                    total_home_score=home, total_away_score=away, **more)

    return [
        row("Half1", "SEA", "DEN"),  # opening kickoff
        row("Half1", "DEN", "SEA", **{"pass": 1, "epa": 0.5, "success": 1, "yards_gained": 10}),
        row("Half1", "DEN", "SEA", rush=1, epa=-0.2, success=0, yards_gained=2),
        row("Half1", "DEN", "SEA", **{"pass": 1, "epa": -3.0, "success": 0, "interception": 1}),
        row("Half1", "SEA", "DEN", **{"pass": 1, "epa": -1.0, "success": 0, "sack": 1,
                                       "fumble_lost": 1}),
        row("Half1", "SEA", "DEN", rush=1, epa=0.4, success=1, yards_gained=5),
        row("Half1", "SEA", "DEN", **{"pass": 1, "epa": "NA", "success": "NA"}),  # no EPA: skipped
        row("Half1", "SEA", "DEN", fumble_lost=1),  # a fumble on a punt: not offense
        row("Half1", "DEN", "SEA", epa=2.0),  # the field goal itself: not a pass or a run
        row("Half1", "SEA", "DEN", home=3, **{"pass": 1, "epa": 0.3, "success": 1}),  # FG on board
        row("Half1", "DEN", "SEA", home=3, away=7),  # SEA's touchdown is on the board
        row("Half2", "SEA", "DEN", home=3, away=7),  # second-half kickoff: the halftime score
        row("Half2", "DEN", "SEA", home=3, away=7, **{"pass": 1, "epa": 9.0, "success": 1}),
        row("Half2", "DEN", "SEA", home=10, away=7, **{"pass": 1, "epa": 1.0, "success": 1}),
    ]
