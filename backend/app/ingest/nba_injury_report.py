"""The NBA's official injury report (a PDF published every 15 minutes), read by text position.

pypdf's layout mode prints the page bottom-up and splits a wrapped reason across lines, so the
parser works from each word's coordinates instead: the status column anchors a player's row,
wrapped reason lines attach to the nearest row, and the team column (printed once per group)
carries forward down the page and over to the next one. Only the first page prints the column
header, so the column positions come from that page.

Verified 2026-10-13: reports exist every 15 minutes, round the clock, at
`Injury-Report_YYYY-MM-DD_HH_MMAM.pdf` (Eastern time); a time with no report answers 403.
"""

import io
import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from pypdf import PdfReader

from app.ingest.records import NamedInjury

EASTERN = ZoneInfo("America/New_York")
REPORT_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{stamp}.pdf"
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
HEADERS = ("Matchup", "Team", "Player", "Current", "Reason")  # the header row's single words
TITLE_Y = 75.0  # every page prints its "Injury Report: <date>" title above this line
ROW_REACH = 12.0  # a wrapped reason sits about 7 points above or below its player's line
NAME_REACH = 3.0  # a player's name shares a line with the status
QUARTERS_BACK = 3  # the newest report may not be published yet: try three earlier ones

Word = tuple  # (y, x, text); y grows downward


class ReportFormatError(ValueError):
    """The PDF is not laid out the way this parser expects."""


def report_moments(now: datetime) -> list[datetime]:
    """The current Eastern quarter hour and the three before it, newest first."""
    utc = now.astimezone(timezone.utc)
    newest = utc.replace(minute=utc.minute // 15 * 15, second=0, microsecond=0)
    return [
        (newest - timedelta(minutes=15 * back)).astimezone(EASTERN)
        for back in range(QUARTERS_BACK + 1)
    ]


def report_url(moment: datetime) -> str:
    return REPORT_URL.format(stamp=moment.strftime("%Y-%m-%d_%I_%M%p"))


async def fetch_report(
    http: httpx.AsyncClient, now: datetime
) -> Optional[tuple[bytes, datetime]]:
    """The newest published report and its time, or None when none of the last four answers."""
    for moment in report_moments(now):
        response = await http.get(report_url(moment))
        if response.status_code == 200:
            return response.content, moment
        if response.status_code not in (403, 404):
            response.raise_for_status()
    return None


def _pages(pdf: bytes) -> list[list[Word]]:
    pages = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        words: list[Word] = []

        def visit(text, cm, tm, font_dict, font_size, words=words):
            if text.strip():
                words.append((round(tm[5], 1), round(tm[4], 1), text.strip()))

        page.extract_text(visitor_text=visit)
        pages.append(words)
    return pages


def _line(words: Sequence[Word]) -> str:
    """Words left to right; a word ending in a hyphen ("Two-", "Way") glues to the next."""
    text, previous = "", ""
    for _, _, word in sorted(words, key=lambda w: w[1]):
        glue = text and previous.endswith("-") and len(previous) > 1
        text = f"{text}{word}" if glue else f"{text} {word}".strip()
        previous = word
    return text


def _column_starts(pages: Sequence[Sequence[Word]]) -> tuple[list[tuple[float, str]], float]:
    """Left edge of each column and the header row's y, read from the header on the first page.
    Only the header row counts: a reason such as "Not With Team" repeats a header word."""
    for words in pages:
        for y in sorted({y for y, _, text in words if text == "Matchup"}):
            row = [(x, text) for line_y, x, text in words if line_y == y]
            left = {text: x for x, text in row if text in HEADERS}
            game = sorted(x for x, text in row if text == "Game")
            if len(left) == len(HEADERS) and len(game) >= 2:
                return sorted([
                    (game[0], "date"), (game[1], "time"), (left["Matchup"], "matchup"),
                    (left["Team"], "team"), (left["Player"], "player"),
                    (left["Current"], "status"), (left["Reason"], "reason"),
                ]), y
    raise ReportFormatError("no column header row found")


def _column(starts: Sequence[tuple[float, str]], x: float) -> str:
    name = starts[0][1]
    for left, label in starts:
        if x >= left - 4:
            name = label
    return name


def _by_row(words: Sequence[Word]) -> dict[float, list[Word]]:
    rows: dict[float, list[Word]] = {}
    for word in words:
        rows.setdefault(word[0], []).append(word)
    return rows


def _nearest(anchors: Sequence[float], y: float, reach: float) -> Optional[float]:
    if not anchors:
        return None
    best = min(anchors, key=lambda anchor: abs(anchor - y))
    return best if abs(best - y) <= reach else None


def _full_name(printed: str) -> str:
    last, _, first = printed.partition(",")
    return f"{first.strip()} {last.strip()}".strip()


def _detail(reason: str) -> Optional[str]:
    """'Injury/Illness - Left Ankle; Sprain' -> 'Left Ankle; Sprain'."""
    text = reason.partition(" - ")[2] or reason
    return re.sub(r"[;\s.-]+$", "", text) or None


def parse_report(pdf: bytes) -> list[NamedInjury]:
    pages = _pages(pdf)
    starts, header_y = _column_starts(pages)
    team = ""
    injuries: list[NamedInjury] = []
    for words in pages:
        footer_y = min((y for y, _, text in words if text == "Page"), default=float("inf"))
        columns: dict[str, list[Word]] = {}
        for word in words:
            if TITLE_Y < word[0] < footer_y and word[0] != header_y:
                columns.setdefault(_column(starts, word[1]), []).append(word)
        labels = sorted((y, _line(row)) for y, row in _by_row(columns.get("team", [])).items())
        status = {y: text for y, _, text in columns.get("status", []) if text in STATUSES}
        anchors = sorted(status)
        names: dict[float, list[Word]] = {y: [] for y in anchors}
        for word in columns.get("player", []):
            anchor = _nearest(anchors, word[0], NAME_REACH)
            if anchor is not None:
                names[anchor].append(word)
        reasons: dict[float, list[Word]] = {y: [] for y in anchors}
        for word in columns.get("reason", []):
            anchor = _nearest(anchors, word[0], ROW_REACH)
            if anchor is not None:
                reasons[anchor].append(word)
        seen = 0
        for y in anchors:
            while seen < len(labels) and labels[seen][0] <= y + 0.5:
                team = labels[seen][1]
                seen += 1
            if status[y] == "Available" or not names[y]:
                continue
            reason = " ".join(_line(row) for _, row in sorted(_by_row(reasons[y]).items()))
            if reason.startswith("G League"):
                continue  # two-way and on assignment: not an injury
            injuries.append(
                NamedInjury(team, _full_name(_line(names[y])), None, status[y], _detail(reason))
            )
    return injuries
