"""Starting-QB injury flag: is the quarterback who started the last game on the injury report?"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
AT_RISK = {"Out", "Doubtful", "Questionable", "Injured Reserve", "Suspension"}


@dataclass(frozen=True)
class InjuredPlayer:
    player: str
    position: Optional[str]
    status: str


@dataclass(frozen=True)
class QbFlag:
    player: str
    status: str


def name_key(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]", " ", ascii_name.casefold()).split()
    return " ".join(word for word in words if word not in SUFFIXES)


def qb_flag(last_starter: Optional[str], injured: Sequence[InjuredPlayer]) -> Optional[QbFlag]:
    if not last_starter:
        return None
    wanted = name_key(last_starter)
    for item in injured:
        if item.position == "QB" and item.status in AT_RISK and name_key(item.player) == wanted:
            return QbFlag(item.player, item.status)
    return None
