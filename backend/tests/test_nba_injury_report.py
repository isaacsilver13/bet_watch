import asyncio
import io
from datetime import datetime, timezone

import httpx
import pytest
from app.ingest.nba_injury_report import (
    ReportFormatError,
    _detail,
    _full_name,
    _line,
    fetch_report,
    parse_report,
    report_moments,
    report_url,
)
from pypdf import PdfWriter

from tests.matchup_helpers import FIXTURES

PDF = FIXTURES / "nba_injury" / "report_2026-02-28_0500PM_p1-3.pdf"


def _players():
    return {record.player: record for record in parse_report(PDF.read_bytes())}


def test_names_are_first_last_and_keep_suffixes() -> None:
    assert _full_name("Smith Jr., Jabari") == "Jabari Smith Jr."
    assert _full_name("Hall, PJ") == "PJ Hall"
    assert _full_name("Tate, Jae'Sean") == "Jae'Sean Tate"


def test_a_reason_is_reduced_to_its_detail() -> None:
    assert _detail("Injury/Illness - Left Ankle; Sprain") == "Left Ankle; Sprain"
    assert _detail("Injury/Illness - Illness; .") == "Illness"
    assert _detail("Injury/Illness - Illness; -") == "Illness"
    assert _detail("Not With Team") == "Not With Team"
    assert _detail("") is None


def test_words_on_a_line_join_left_to_right_and_a_trailing_hyphen_glues() -> None:
    assert _line([(0, 20, "Way"), (0, 10, "Two-")]) == "Two-Way"
    words = [(0, 10, "Injury/Illness"), (0, 25, "-"), (0, 30, "Left")]
    assert _line(words) == "Injury/Illness - Left"


def test_the_real_report_parses_into_injuries_with_carried_forward_teams() -> None:
    records = parse_report(PDF.read_bytes())
    players = _players()
    assert len(records) == 22
    smith = players["Jabari Smith Jr."]
    assert (smith.team, smith.status, smith.detail) == (
        "Houston Rockets", "Out", "Right Ankle; Sprain")
    assert smith.position is None and smith.reported_at is None
    # a reason that wraps onto a second line is joined back to its player
    assert players["Deni Avdija"].detail == "Low Back; Injury Management"
    assert players["Damian Lillard"].detail == "Left Achilles Tendon; Injury Management"
    # a team's group that runs over a page break keeps its team on the next page
    assert players["Alex Sarr"].team == "Washington Wizards"
    assert players["Trae Young"].team == "Washington Wizards"
    assert players["Rui Hachimura"].team == "Los Angeles Lakers"
    assert players["Draymond Green"].status == "Probable"
    assert players["Collin Murray-Boyles"].status == "Doubtful"
    assert players["Rui Hachimura"].detail == "Illness"


def test_available_players_and_g_league_assignments_are_not_injuries() -> None:
    players = _players()
    assert "Donovan Clingan" not in players  # Available
    assert "Caleb Love" not in players  # Out, G League - Two-Way
    assert {r.status for r in players.values()} <= {"Out", "Doubtful", "Questionable", "Probable"}


def test_a_pdf_without_the_column_header_is_rejected_rather_than_read_as_empty() -> None:
    blank = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(blank)
    with pytest.raises(ReportFormatError):
        parse_report(blank.getvalue())


def test_report_times_are_the_current_eastern_quarter_hour_and_three_before() -> None:
    now = datetime(2026, 10, 13, 19, 37, tzinfo=timezone.utc)  # 3:37pm Eastern
    moments = report_moments(now)
    assert [m.strftime("%H:%M") for m in moments] == ["15:30", "15:15", "15:00", "14:45"]
    assert report_url(moments[0]) == (
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-13_03_30PM.pdf")


def test_just_after_midnight_the_older_reports_belong_to_the_previous_eastern_day() -> None:
    now = datetime(2026, 10, 14, 4, 5, tzinfo=timezone.utc)  # 12:05am Eastern
    urls = [report_url(m) for m in report_moments(now)]
    assert urls[0].endswith("Injury-Report_2026-10-14_12_00AM.pdf")
    assert urls[-1].endswith("Injury-Report_2026-10-13_11_15PM.pdf")


def _fetch(handler, now):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch_report(http, now)

    return asyncio.run(scenario())


def test_fetch_takes_the_newest_report_that_answers() -> None:
    now = datetime(2026, 10, 13, 19, 37, tzinfo=timezone.utc)
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        # the 3:30 and 3:15 reports are not published yet (the host answers 403)
        return httpx.Response(200, content=b"%PDF-1.4") if "03_00PM" in request.url.path \
            else httpx.Response(403)

    body, moment = _fetch(handler, now)
    assert body == b"%PDF-1.4" and moment.strftime("%H:%M") == "15:00"
    assert len(asked) == 3


def test_fetch_returns_none_when_no_report_answers_and_raises_on_a_server_error() -> None:
    now = datetime(2026, 10, 13, 19, 37, tzinfo=timezone.utc)
    assert _fetch(lambda request: httpx.Response(403), now) is None
    with pytest.raises(httpx.HTTPStatusError):
        _fetch(lambda request: httpx.Response(500), now)
