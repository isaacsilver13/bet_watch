import asyncio
from datetime import date, datetime, timezone

import httpx
from app.ingest.espn import (
    fetch_scoreboard_day,
    parse_injuries,
    parse_pickcenter,
    parse_scoreboard,
)

from tests.matchup_helpers import load_json


def _games():
    return {g.espn_id: g for g in parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))}


def test_an_nba_scoreboard_has_no_weeks_and_keeps_neutral_site_games() -> None:
    games = _games()
    assert set(games) == {"401810433", "401810434", "401810435", "401810439"}
    berlin = games["401810433"]
    assert (berlin.home_name, berlin.away_name) == ("ORL", "MEM")
    assert (berlin.home_score, berlin.away_score, berlin.status) == (118, 111, "final")
    assert berlin.neutral is True and berlin.venue_espn_id == "11593"
    assert (berlin.season, berlin.season_type, berlin.week) == (2026, "REG", None)
    assert berlin.start_time == datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)
    assert berlin.lines == ()  # the scoreboard drops odds once a game is final


def test_a_scheduled_nba_game_carries_its_open_and_current_lines() -> None:
    game = _games()["401810439"]
    assert (game.status, game.home_name, game.away_name) == ("scheduled", "GS", "NY")
    assert game.home_score is None and game.away_score is None
    lines = {line.kind: line for line in game.lines}
    assert set(lines) == {"open", "current"}  # not "close": the game has not started
    assert (lines["open"].spread_home, lines["open"].total, lines["open"].ml_home) == (
        -6.5, 226.5, -260)
    assert (lines["current"].spread_home, lines["current"].spread_home_price) == (-7.5, -108)
    assert (lines["current"].total, lines["current"].ml_away) == (228.5, 250)


def test_the_summary_pickcenter_gives_the_final_games_open_and_close() -> None:
    lines = {line.kind: line for line in parse_pickcenter(load_json("espn_nba/summary_final.json"))}
    assert (lines["open"].spread_home, lines["open"].total, lines["open"].ml_home) == (
        -4.5, 230.5, -180)
    assert (lines["close"].spread_home, lines["close"].spread_away_price) == (-5.5, -112)
    assert (lines["close"].total, lines["close"].over_price, lines["close"].ml_away) == (
        227.5, -108, 190)


def test_the_scoreboard_is_fetched_one_day_at_a_time() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=load_json("espn_nba/scoreboard_day.json"))

    async def scenario() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_scoreboard_day(http, "nba", date(2026, 1, 15))

    payload = asyncio.run(scenario())
    assert len(payload["events"]) == 4
    assert seen[0].path.endswith("/basketball/nba/scoreboard")
    assert seen[0].params["dates"] == "20260115"


def test_nba_injuries_keep_day_to_day_and_out_with_the_team_id() -> None:
    records = parse_injuries(load_json("espn_nba/injuries_league.json"))
    assert len(records) == 10
    assert {r.status for r in records} <= {"Out", "Day-To-Day"}
    sample = next(r for r in records if r.player == "Sample Guard")
    assert (sample.team_espn_id, sample.position, sample.status) == ("1", "PG", "Out")
    assert sample.detail == "Knee, Sprain"
