"""The week board: one read-only call with every game's same-line best prices and context flags."""

import json

from app.api.slate import get_now
from app.main import app

from tests import test_lines_api as lines
from tests import test_matchup_api as matchup

WEEK = {"season": 2026, "season_type": "REG", "week": 6}
FIRST, SECOND, NOW = lines.FIRST, lines.SECOND, lines.NOW


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _board(client, **params) -> dict:
    response = client.get("/api/v1/board", params={**WEEK, **params})
    assert response.status_code == 200, response.text
    return response.json()


def _den(body: dict) -> dict:
    return next(g for g in body["games"] if g["game"]["espn_id"] == "401872995")


def _other_number(book: str, shift: float = 1.0, price: int = 300) -> str:
    """The standard payload with one book hanging a different spread number at a better price."""
    payload = json.loads(lines._body())
    for entry in payload[0]["bookmakers"]:
        if entry["title"] != book:
            continue
        for market in entry["markets"]:
            if market["key"] == "spreads":
                for outcome in market["outcomes"]:
                    outcome["point"] += shift
                    outcome["price"] = price
    return json.dumps(payload)


def test_board_lists_the_week_with_per_book_quotes_best_prices_and_snapshot_time(tmp_path) -> None:
    client = lines._client(tmp_path, [(FIRST, lines._body()), (SECOND, lines._body(1.0))])
    body = _board(client)
    assert (body["season"], body["week"], body["snapshot_at"][:13]) == (2026, 6, "2026-10-15T15")
    den = _den(body)
    market = den["market"]
    assert market["snapshot_at"].startswith("2026-10-15T15:00:00")
    assert [b["book"] for b in market["books"]] == [
        "BetMGM", "BetRivers", "DraftKings", "FanDuel", "theScore Bet"
    ]
    assert market["consensus"]["spread_home"] == 2.5
    assert market["best"]["spread_home"]["venue"] == "betrivers"
    assert market["coverage"]["spread"] == {"quoting": 5, "at_line": 4}  # BetMGM hangs 3.0
    # Book consensus (first snapshot 1.5 -> latest 2.5) is kept apart from the ESPN reference.
    assert (market["movement"]["spread_home"], market["movement"]["snapshots"]) == (1.5, 2)
    assert den["game"]["lines"]["open"]["source"] == "espn"
    assert {b["venue"] for b in body["books_available"]} == {
        "betmgm", "betrivers", "draftkings", "fanduel", "espnbet"
    }


def test_a_better_price_at_a_different_number_never_wins(tmp_path) -> None:
    client = lines._client(tmp_path, [(FIRST, _other_number("FanDuel"))])
    market = _den(_board(client))["market"]
    fanduel = next(b for b in market["books"] if b["book"] == "FanDuel")
    assert fanduel["spread_home"] == 2.5 and fanduel["spread_home_price"] == 300
    assert market["best"]["spread_home"]["book"] != "FanDuel"
    assert market["best"]["spread_home"]["line"] == 1.5 == market["consensus"]["spread_home"]
    assert market["coverage"]["spread"] == {"quoting": 5, "at_line": 3}  # "3 of 5 books at 1.5"


def test_sparse_coverage_keeps_the_game_and_counts_the_quoting_books(tmp_path) -> None:
    payload = json.loads(lines._body())
    payload[0]["bookmakers"][1]["markets"] = [
        m for m in payload[0]["bookmakers"][1]["markets"] if m["key"] != "totals"
    ]
    client = lines._client(tmp_path, [(FIRST, json.dumps(payload))])
    market = _den(_board(client))["market"]
    assert market["coverage"]["total"] == {"quoting": 4, "at_line": 3}  # BetMGM hangs 43
    assert market["coverage"]["spread"]["quoting"] == 5
    gap = next(b for b in market["books"] if b["total"] is None)
    assert gap["over_price"] is None and gap["under_price"] is None


def test_book_filter_scopes_consensus_and_best_but_reports_what_it_hid(tmp_path) -> None:
    client = lines._client(tmp_path, [(FIRST, lines._body())])
    body = _board(client, books="draftkings,fanduel")
    market = _den(body)["market"]
    assert [b["venue"] for b in market["books"]] == ["draftkings", "fanduel"]
    assert market["consensus"]["books"] == 2
    assert len(market["snapshot_books"]) == 5  # the filter never hides that other books quote
    assert body["books_selected"] == ["draftkings", "fanduel"] and len(body["books_available"]) == 5
    assert client.get("/api/v1/board", params={**WEEK, "books": "bovada"}).status_code == 422


def test_a_game_with_no_odds_still_shows_its_schedule_and_reference_lines(tmp_path) -> None:
    body = _board(lines._client(tmp_path))
    den = _den(body)
    assert body["snapshot_at"] is None
    empty = den["market"]
    assert (empty["books"], empty["consensus"], empty["best"]) == ([], None, {})
    assert den["market"]["movement"] is None
    assert den["game"]["lines"]["open"]["spread_home"] == 2.5


def test_after_kickoff_the_board_keeps_the_pregame_snapshot(tmp_path) -> None:
    from datetime import timedelta
    kickoff = lines.datetime(2026, 10, 16, 0, 15, tzinfo=lines.timezone.utc)
    client = lines._client(
        tmp_path,
        [(kickoff - timedelta(minutes=20), lines._body()),
         (kickoff + timedelta(minutes=40), lines._body(spread_shift=9.0))],
        now=kickoff + timedelta(hours=2),
    )
    market = _den(_board(client))["market"]
    assert market["snapshot_at"].startswith("2026-10-15T23:55:00")
    assert market["consensus"]["spread_home"] == 1.5 and market["movement"]["snapshots"] == 1


def test_reading_the_board_never_calls_the_odds_provider(tmp_path) -> None:
    client = lines._client(tmp_path, [(FIRST, lines._body())])
    for params in ({}, {"books": "fanduel"}, {"books": "betmgm,draftkings"}):
        _board(client, **params)
    assert lines.ODDS_CALLS == []
    assert _board(client)["can_refresh"] is True  # allowed to refresh, but only by asking to


def test_board_gets_the_same_numbers_as_the_game_page(tmp_path) -> None:
    client = lines._client(tmp_path, [(FIRST, lines._body()), (SECOND, lines._body(1.0))])
    market = _den(_board(client))["market"]
    page = client.get(f"/api/v1/games/{lines._den_id(tmp_path)}/lines").json()
    assert market["consensus"] == page["consensus"] and market["best"] == page["best"]
    assert market["books"] == page["books"]


def test_flags_name_the_starting_qb_on_the_report_and_the_short_week(tmp_path) -> None:
    client = matchup._client(tmp_path)  # NOW = Wed 2026-10-14, SEA @ DEN on Thursday night
    app.dependency_overrides[get_now] = lambda: matchup.NOW
    den = _den(_board(client))
    flags = {(f["kind"], f["team"]): f for f in den["flags"]}
    assert flags[("qb", "DEN")]["text"] == "DEN QB Bo Nix listed Questionable"
    assert flags[("qb", "DEN")]["as_of"].startswith("2026-10-14T20:00")
    assert ("short_week", "SEA") in flags and "4 days rest" in flags[("short_week", "SEA")]["text"]
    assert ("qb", "SEA") not in flags
