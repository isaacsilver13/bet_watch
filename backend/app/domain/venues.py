"""Where a bet can be placed. Only the free-tier Illinois books are tracked."""

from typing import Optional

from app.identity import normalize_name

VENUES = ("draftkings", "fanduel", "betmgm", "betrivers", "espnbet", "other")

# The Odds API's bookmaker titles, normalized, to a venue. theScore Bet is the old ESPN BET.
_BY_TITLE = {
    "draftkings": "draftkings",
    "fanduel": "fanduel",
    "betmgm": "betmgm",
    "betrivers": "betrivers",
    "espn bet": "espnbet",
    "espnbet": "espnbet",
    "thescore bet": "espnbet",
    "thescore": "espnbet",
}


def venue_for_book(title: str) -> Optional[str]:
    return _BY_TITLE.get(normalize_name(title))
