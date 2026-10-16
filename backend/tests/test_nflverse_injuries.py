from app.ingest.nflverse_injuries import INJURIES_URL, parse_injuries_csv

from tests.matchup_helpers import FIXTURES

TEXT = (FIXTURES / "nflverse" / "injuries_sample.csv").read_text(encoding="utf-8")


def _records():
    week, records = parse_injuries_csv(TEXT)
    return week, {record.player: record for record in records}


def test_only_the_newest_regular_season_week_is_read() -> None:
    week, players = _records()
    assert week == 18  # week 17 and the postseason row are ignored
    assert set(players) == {
        "Kevin Dotson", "Josh Sweat", "Dontayvion Wicks", "Marvin Harrison Jr.", "Josh Allen",
    }


def test_game_designations_pass_through_with_the_practice_note() -> None:
    _, players = _records()
    dotson = players["Kevin Dotson"]
    assert (dotson.team, dotson.position, dotson.status, dotson.detail) == (
        "LA", "G", "Out", "Ankle · DNP in practice")
    assert dotson.reported_at is None
    assert players["Josh Sweat"].status == "Questionable"
    assert players["Josh Sweat"].detail == "Ankle, Knee · Limited in practice"
    assert (players["Dontayvion Wicks"].status, players["Dontayvion Wicks"].detail) == (
        "Doubtful", "Concussion · DNP in practice")


def test_a_practice_only_injury_is_a_questionable() -> None:
    _, players = _records()
    assert (players["Marvin Harrison Jr."].status, players["Marvin Harrison Jr."].detail) == (
        "Questionable", "Foot, Heel · DNP in practice")
    assert (players["Josh Allen"].status, players["Josh Allen"].detail) == (
        "Questionable", "Foot · Limited in practice")


def test_resting_players_and_full_practice_are_not_injuries() -> None:
    _, players = _records()
    assert "Calais Campbell" not in players and "Kelvin Beachum" not in players


def test_a_file_with_no_regular_season_rows_has_no_week() -> None:
    header = TEXT.splitlines()[0]
    assert parse_injuries_csv(header + "\n") == (0, [])
    postseason_only = header + "\n" + TEXT.splitlines()[-1] + "\n"
    assert parse_injuries_csv(postseason_only) == (0, [])


def test_the_url_names_the_season() -> None:
    assert INJURIES_URL.format(season=2026).endswith("/injuries/injuries_2026.csv")
