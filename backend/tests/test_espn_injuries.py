from datetime import datetime, timezone

import pytest
from app.ingest.espn import parse_injuries

from tests.matchup_helpers import load_json


def _by_player():
    return {r.player: r for r in parse_injuries(load_json("espn/injuries_league.json"))}


def test_availability_entries_are_kept_with_team_position_status_and_detail() -> None:
    records = _by_player()
    lamar = records["Lamar Jackson"]
    assert (lamar.team_espn_id, lamar.position, lamar.status) == ("33", "QB", "Out")
    assert lamar.detail == "Ankle, Sprain"
    assert lamar.reported_at is not None and lamar.reported_at.tzinfo is timezone.utc
    assert records["Michael Penix Jr."].detail == "Ribs"  # "Not Specified" is dropped
    assert records["Isaiah Adams"].status == "Injured Reserve"
    assert records["Dre Greenlaw"].status == "Suspension"


def test_depth_chart_news_and_retirements_are_not_injuries() -> None:
    records = _by_player()
    assert "Andrew Billings" not in records  # status Active
    assert "Lane Johnson" not in records  # Reserve-Ret


def test_report_time_is_parsed_as_utc_and_a_bad_one_is_none() -> None:
    payload = {
        "injuries": [
            {
                "id": "1",
                "injuries": [
                    {
                        "status": "Out",
                        "date": "2026-10-12T18:55Z",
                        "athlete": {"displayName": "A B"},
                    },
                    {"status": "Out", "date": "yesterday", "athlete": {"displayName": "C D"}},
                ],
            }
        ]
    }
    a, c = parse_injuries(payload)
    assert a.reported_at == datetime(2026, 10, 12, 18, 55, tzinfo=timezone.utc)
    assert c.reported_at is None and c.position is None and c.detail is None


@pytest.mark.parametrize("payload", [{}, {"error": "upstream failed"}, {"injuries": {}}])
def test_malformed_payload_cannot_be_mistaken_for_an_all_clear(payload) -> None:
    with pytest.raises(ValueError, match="injuries list"):
        parse_injuries(payload)


def test_an_explicit_empty_report_is_the_only_zero_row_all_clear() -> None:
    assert parse_injuries({"injuries": []}) == []
