import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timezone

import httpx
import pytest
from app.config import load_settings
from app.reports.delivery import send_staged_digest, stage_digest
from app.storage.database import create_schema, create_session_factory
from app.storage.models import NbaDigestDelivery, NbaReport
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_nba_digest import REPORT

SLATE = date(2026, 10, 22)


async def _store_report(tmp_path, report=REPORT):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'digest.db'}")
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        session.add(NbaReport(slate_date=SLATE,
                              created_at=datetime.now(timezone.utc), payload=report))
    return engine, factory


def _settings():
    return replace(load_settings(), resend_api_key="test-key",
                   nba_newsletter_from_email="AIsaac <sender@example.test>",
                   nba_newsletter_to_email="owner@example.test")


def test_digest_outbox_freezes_request_and_sends_once(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _store_report(tmp_path)
        try:
            settings = _settings()
            first = await stage_digest(factory, SLATE, settings,
                                       "https://private.example/nba/2026-10-22")
            frozen = first.request_payload.copy()
            changed = replace(settings, nba_newsletter_to_email="someone-else@example.test")
            second = await stage_digest(factory, SLATE, changed,
                                        "https://different.example/nba/2026-10-22")
            assert second.request_payload == frozen
            assert second.idempotency_key == "nba-digest/2026-10-22"
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(200, json={"id": "test-message-id"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                assert await send_staged_digest(factory, client, settings, SLATE) == "accepted"
                with pytest.raises(ValueError, match="already attempted"):
                    await send_staged_digest(factory, client, settings, SLATE)
            assert len(requests) == 1
            request = requests[0]
            assert str(request.url) == "https://api.resend.com/emails"
            assert request.headers["Idempotency-Key"] == "nba-digest/2026-10-22"
            assert json.loads(request.content) == frozen
            assert frozen["to"] == ["owner@example.test"]
            assert "64.0%" in frozen["html"] and "64.0%" in frozen["text"]
            async with factory() as session:
                saved = await session.get(NbaDigestDelivery, SLATE)
                assert saved.state == "accepted"
                assert saved.provider_message_id == "test-message-id"
                assert saved.accepted_at is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_or_ambiguous_acceptance_requires_review(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _store_report(tmp_path)
        try:
            settings = _settings()
            await stage_digest(factory, SLATE, settings)
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(503)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with pytest.raises(ValueError, match="RESEND_API_KEY"):
                    await send_staged_digest(factory, client,
                                             replace(settings, resend_api_key=None), SLATE)
                assert requests == []
                assert await send_staged_digest(factory, client, settings, SLATE) == "review"
                with pytest.raises(ValueError, match="already attempted"):
                    await send_staged_digest(factory, client, settings, SLATE)
            assert len(requests) == 1
            async with factory() as session:
                saved = await session.get(NbaDigestDelivery, SLATE)
                assert saved.state == "review"
                assert saved.failure_type == "HTTPStatusError"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_no_game_day_cannot_be_staged(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await _store_report(tmp_path, {**REPORT, "games": []})
        try:
            with pytest.raises(ValueError, match="no NBA games"):
                await stage_digest(factory, SLATE, _settings())
            async with factory() as session:
                assert await session.get(NbaDigestDelivery, SLATE) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
