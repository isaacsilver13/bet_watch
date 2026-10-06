import logging
import re
from collections import deque
from datetime import datetime, timezone

_MAX_ENTRIES = 50
_recent_errors: deque[dict] = deque(maxlen=_MAX_ENTRIES)


_MAX_MESSAGE_LENGTH = 300
_REDACTIONS = [
    # scheme://user:pass@host (database URLs etc.)
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@]*@\S*", re.I), "[redacted-url]"),
    # key=value / key: value credential pairs (api_key, apiKey, token, password, ...)
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie)"
            r"\w*\s*[=:]\s*(?:bearer\s+)?[^\s,;&]+"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"), "Bearer [redacted]"),
    # query strings may carry secrets (the provider key travels as ?apiKey=)
    (re.compile(r"\?\S+"), "?[redacted]"),
    # long opaque tokens
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "[redacted]"),
]


def sanitize_message(message: str) -> str:
    """Keep the first line only (no tracebacks), redact credential-looking content, cap length."""
    message = message.split("\n", 1)[0]
    for pattern, replacement in _REDACTIONS:
        message = pattern.sub(replacement, message)
    return message[:_MAX_MESSAGE_LENGTH]


class RingBufferHandler(logging.Handler):
    """Keeps the last N error/critical log records in memory for /health/errors.

    Intentionally shallow -- this is a peek for a dashboard, not a log store.
    No stack traces or request bodies, just level/timestamp/message.
    """

    def emit(self, record: logging.LogRecord) -> None:
        _recent_errors.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "message": sanitize_message(record.getMessage()),
            }
        )


def install() -> None:
    handler = RingBufferHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)


def recent_errors() -> list[dict]:
    return list(_recent_errors)
