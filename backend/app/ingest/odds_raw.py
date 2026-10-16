"""Paid responses are saved to disk before parsing, so a crash never costs a second call."""

import gzip
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_STAMP = "%Y%m%dT%H%M%SZ"


def write_raw(
    raw_dir: Path, provider: str, slot_key: str, observed_at: datetime, body: str
) -> Path:
    """`<raw_dir>/<provider>/<date>/<stamp>_<slot>.json.gz`, renamed into place when complete."""
    utc = observed_at.astimezone(timezone.utc)
    slot = re.sub(r"[^A-Za-z0-9_.-]+", "_", slot_key)
    path = raw_dir / provider / utc.strftime("%Y-%m-%d") / f"{utc.strftime(_STAMP)}_{slot}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(gzip.compress(body.encode("utf-8")))
    os.replace(temporary, path)
    return path


def read_raw(path: Path) -> str:
    return gzip.decompress(path.read_bytes()).decode("utf-8")


def observed_at_from_path(path: Path) -> datetime:
    """The snapshot time is part of the file name, so a re-parse stamps rows exactly as before."""
    return datetime.strptime(path.name.split("_", 1)[0], _STAMP).replace(tzinfo=timezone.utc)
