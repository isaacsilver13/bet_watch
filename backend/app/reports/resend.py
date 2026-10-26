"""Resend request preparation and transport for frozen NBA digest deliveries."""

import logging
from datetime import date
from typing import Optional

import httpx

from app.config import Settings
from app.reports.nba_digest import render_nba_digest

logger = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


def build_digest_request(settings: Settings, report: dict,
                         report_url: Optional[str] = None) -> tuple[str, dict]:
    """Render exactly once. Persist the returned key and body before any HTTP call."""
    if not (settings.nba_newsletter_from_email and settings.nba_newsletter_to_email):
        raise ValueError("NBA digest sender and recipient must be configured")
    digest = render_nba_digest(report, report_url)
    slate = date.fromisoformat(report["slate_date"])
    return f"nba-digest/{slate.isoformat()}", {
        "from": settings.nba_newsletter_from_email,
        "to": [settings.nba_newsletter_to_email],
        "subject": digest.subject,
        "html": digest.html,
        "text": digest.text,
    }


async def post_frozen_digest(http: httpx.AsyncClient, api_key: str,
                             idempotency_key: str, body: dict) -> tuple[bool, Optional[str], str]:
    """Return acceptance, provider ID, and a failure type; ambiguity needs manual review."""
    try:
        response = await http.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Idempotency-Key": idempotency_key},
            json=body,
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        message_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            return False, None, "missing_message_id"
        return True, message_id, ""
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("NBA digest send needs review (%s)", type(error).__name__)
        return False, None, type(error).__name__
