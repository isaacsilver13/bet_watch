import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone

import httpx
from app.api.slate import get_session_factory
from app.main import app
from app.storage.models import NbaReport

from tests.matchup_helpers import new_session_factory
from tests.test_nba_digest import REPORT


def test_saved_report_is_read_from_immutable_payload(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        previous_override = app.dependency_overrides.get(get_session_factory)
        try:
            async with factory() as session, session.begin():
                session.add(NbaReport(slate_date=date(2026, 10, 22),
                                      created_at=datetime.now(timezone.utc), payload=REPORT))
                paper_report = deepcopy(REPORT)
                paper_report["slate_date"] = "2026-10-23"
                paper_report["forecast_sha256"] = "a" * 64
                paper_report["picks"][0].update(
                    game_id=7, market="moneyline", side="home",
                    quote_observation_id=42, paper_win_prob=0.64,
                )
                session.add(NbaReport(slate_date=date(2026, 10, 23),
                                      created_at=datetime.now(timezone.utc),
                                      payload=paper_report))
                session.add(NbaReport(slate_date=date(2026, 10, 24),
                                      created_at=datetime.now(timezone.utc),
                                      payload=paper_report))
            app.dependency_overrides[get_session_factory] = lambda: factory
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
                base_url="http://localhost",
            ) as client:
                response = await client.get("/api/v1/nba/reports/2026-10-22")
                assert response.status_code == 200
                assert response.json() == REPORT
                status = await client.get("/api/v1/nba/paper-ledger/status")
                assert status.status_code == 200
                assert len(status.json()["profiles"]) == 11
                assert all(row["cash"] == 1000 for row in status.json()["profiles"])
                assert (await client.get(
                    "/api/v1/nba/paper-ledger/slates/2026-10-22"
                )).status_code == 404
                preview = await client.get("/api/v1/nba/reports/2026-10-22/digest-preview")
                assert preview.status_code == 200
                assert preview.headers["content-type"].startswith("text/html")
                assert "NBA outlook" in preview.text
                assert preview.headers["cache-control"] == "private, no-store"
                assert (await client.get(
                    "/api/v1/nba/reports/2026-10-22/paper-preview"
                )).status_code == 422
                paper = await client.get("/api/v1/nba/reports/2026-10-23/paper-preview")
                assert paper.status_code == 200
                assert paper.json()["status"] == "preview_not_ledger"
                assert paper.json()["candidate_count"] == 1
                assert len(paper.json()["allocations"]) == 11
                assert paper.json()["allocations"][0]["profile"] == "all"
                assert paper.json()["allocations"][0]["opening_bankroll"] == 1000.0
                assert len(paper.json()["allocations"][0]["bets"]) == 1
                assert (await client.get(
                    "/api/v1/nba/reports/2026-10-24/paper-preview"
                )).status_code == 422
                assert (await client.get("/api/v1/nba/reports/2026-10-25")).status_code == 404
                assert (await client.get(
                    "/api/v1/nba/reports/2026-10-25/digest-preview"
                )).status_code == 404
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_session_factory, None)
            else:
                app.dependency_overrides[get_session_factory] = previous_override
            await factory.kw["bind"].dispose()

    asyncio.run(scenario())
