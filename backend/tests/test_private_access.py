import asyncio
from dataclasses import replace

import httpx
import pytest
from app.api import routes
from app.main import app


@pytest.mark.parametrize("secret", [None, "fixture-owner"])
def test_remote_and_forwarded_requests_cannot_reach_private_data(monkeypatch, secret):
    monkeypatch.setattr(routes, "settings", replace(routes.settings, admin_refresh_secret=secret))

    async def check():
        for peer, host, headers in (
            ("203.0.113.1", "localhost", {}),
            ("127.0.0.1", "attacker.invalid", {}),
            ("127.0.0.1", "localhost", {"x-forwarded-for": ""}),
            ("127.0.0.1", "localhost", {"fly-client-ip": "127.0.0.1"}),
            ("127.0.0.1", "localhost", {"origin": "https://attacker.invalid"}),
            ("127.0.0.1", "localhost", {"sec-fetch-site": "cross-site"}),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(peer, 50000)),
                base_url=f"http://{host}", headers=headers,
            ) as client:
                for path in ("/api/v1/bets", "/api/v1/bets/summary", "/api/v1/slate", "/"):
                    assert (await client.get(path)).status_code == 403
                assert (await client.post("/api/v1/bets", json={})).status_code == 403
                assert (await client.patch("/api/v1/bets/1", json={})).status_code == 403
                assert (await client.get("/api/v1/health")).status_code == 200

    asyncio.run(check())
