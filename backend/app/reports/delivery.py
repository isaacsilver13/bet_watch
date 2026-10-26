"""One-shot local outbox for owner-only NBA emails; no scheduled caller."""

from datetime import date, datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.reports.resend import build_digest_request, post_frozen_digest
from app.storage.models import NbaDigestDelivery, NbaReport


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def stage_digest(factory: async_sessionmaker[AsyncSession], slate: date,
                       settings: Settings, report_url: Optional[str] = None
                       ) -> NbaDigestDelivery:
    """Freeze report, sender, recipient and URL; an existing stage never changes."""
    async with factory() as session, session.begin():
        existing = await session.get(NbaDigestDelivery, slate)
        if existing is not None:
            return existing
        report = await session.get(NbaReport, slate)
        if report is None:
            raise ValueError("no saved NBA report for slate")
        if report.payload.get("slate_date") != slate.isoformat():
            raise ValueError("saved NBA report slate date mismatch")
        key, body = build_digest_request(settings, report.payload, report_url)
        delivery = NbaDigestDelivery(
            slate_date=slate, prepared_at=_now(), request_payload=body,
            idempotency_key=key, state="staged",
        )
        session.add(delivery)
        return delivery


async def send_staged_digest(factory: async_sessionmaker[AsyncSession],
                             http: httpx.AsyncClient, settings: Settings,
                             slate: date) -> str:
    """Attempt once; any uncertain result stays in review until reconciled.

    The state claim commits before HTTP, so a crash cannot silently retry a
    possibly accepted email. This is an explicit send control, not a scheduler.
    """
    if not settings.resend_api_key:
        raise ValueError("RESEND_API_KEY is not configured")
    async with factory() as session, session.begin():
        claimed = await session.execute(
            update(NbaDigestDelivery)
            .where(NbaDigestDelivery.slate_date == slate,
                   NbaDigestDelivery.state == "staged")
            .values(state="sending", attempted_at=_now())
        )
        if claimed.rowcount != 1:
            raise ValueError("digest is missing or already attempted")
        delivery = await session.get(NbaDigestDelivery, slate)
        key, body = delivery.idempotency_key, delivery.request_payload
    accepted, message_id, failure_type = await post_frozen_digest(
        http, settings.resend_api_key, key, body
    )
    async with factory() as session, session.begin():
        delivery = await session.get(NbaDigestDelivery, slate)
        if delivery.state != "sending":
            raise RuntimeError("NBA digest delivery state changed during send")
        delivery.state = "accepted" if accepted else "review"
        delivery.accepted_at = _now() if accepted else None
        delivery.provider_message_id = message_id
        delivery.failure_type = failure_type or None
    return "accepted" if accepted else "review"
