import asyncio
from dataclasses import replace

import httpx
from app.api import owner_auth, routes
from app.api.slate import get_session_factory
from app.config import ProviderMode, load_settings
from app.main import app
from app.storage.database import create_engine, create_schema, create_session_factory

PASSWORD = "an-example-owner-password-2026"
ORIGIN = "https://private.example.test"


def test_owner_login_session_csrf_logout_and_revocation(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'owner.db'}")
        monkeypatch.setenv("OWNER_PASSWORD_HASH", owner_auth.hash_owner_password(PASSWORD))
        monkeypatch.setenv("OWNER_APP_ORIGIN", ORIGIN)
        settings = load_settings()
        monkeypatch.setattr(routes, "settings", replace(routes.settings,
                            provider_mode=ProviderMode.LIVE,
                            admin_refresh_secret="test-admin-only"))
        engine = create_engine(settings)
        await create_schema(engine)
        previous_override = app.dependency_overrides.get(get_session_factory)
        app.dependency_overrides[get_session_factory] = lambda: create_session_factory(engine)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 50000)),
                base_url=ORIGIN,
            ) as client:
                assert (await client.get("/api/v1/health")).status_code == 200
                assert (await client.get("/api/v1/bets")).status_code == 401
                assert (await client.get("/api/v1/nba/reports/2026-10-22")).status_code == 401
                redirect = await client.get("/nba")
                assert redirect.status_code == 303 and redirect.headers["location"] == "/login"
                assert (await client.get("/api/v1/auth/session")).json()["authenticated"] is False
                bad_origin = await client.post(
                    "/api/v1/auth/login", json={"password": PASSWORD},
                    headers={"Origin": "https://evil.test"},
                )
                assert bad_origin.status_code == 403
                assert (await client.post("/api/v1/auth/login", json={"password": "wrong"},
                                          headers={"Origin": ORIGIN})).status_code == 401
                ok = await client.post("/api/v1/auth/login", json={"password": PASSWORD},
                                       headers={"Origin": ORIGIN})
                assert ok.status_code == 200
                assert "httponly" in ok.headers["set-cookie"].lower()
                assert "secure" in ok.headers["set-cookie"].lower()
                assert "samesite=strict" in ok.headers["set-cookie"].lower()
                token = client.cookies.get(owner_auth.COOKIE_NAME)
                assert token
                assert (await client.get("/api/v1/auth/session")).json()["authenticated"] is True
                assert (await client.get("/api/v1/bets")).status_code == 200
                status = await client.get("/api/v1/status")
                assert status.json()["capabilities"]["public_expensive_refresh"] is True
                bad_refresh = await client.get(
                    "/api/v1/odds?sport=nba&force_refresh=true",
                    headers={"Sec-Fetch-Site": "same-site"},
                )
                assert bad_refresh.headers.get("X-Refresh-Ignored") == "unauthorized"
                authorized_refresh = await client.get(
                    "/api/v1/odds?sport=nba&force_refresh=true",
                    headers={"Sec-Fetch-Site": "same-origin", "X-Owner-Action": "refresh"},
                )
                assert authorized_refresh.headers.get("X-Refresh-Ignored") is None
                assert (await client.post("/api/v1/bets", json={})).status_code == 403
                assert (await client.post("/api/v1/bets", json={},
                                          headers={"Origin": ORIGIN})).status_code == 422
                monkeypatch.setenv("OWNER_PASSWORD_HASH", owner_auth.hash_owner_password(
                    "a-rotated-owner-password-2026"
                ))
                assert (await client.get("/api/v1/auth/session")).json()["authenticated"] is False
                assert (await client.get("/api/v1/bets")).status_code == 401
                monkeypatch.setenv("OWNER_PASSWORD_HASH", settings.owner_password_hash)
                out = await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
                assert out.status_code == 200
                assert (await client.get("/api/v1/bets")).status_code == 401
                client.cookies.set(owner_auth.COOKIE_NAME, token)
                assert (await client.get("/api/v1/bets")).status_code == 401
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_session_factory, None)
            else:
                app.dependency_overrides[get_session_factory] = previous_override
            await engine.dispose()
            await owner_auth._factory(settings).kw["bind"].dispose()
            owner_auth._factory.cache_clear()

    asyncio.run(scenario())


def test_password_hash_is_salted_and_malformed_verifiers_fail_closed() -> None:
    first = owner_auth.hash_owner_password(PASSWORD)
    second = owner_auth.hash_owner_password(PASSWORD)
    assert first != second
    assert owner_auth.verify_owner_password(PASSWORD, first)
    assert not owner_auth.verify_owner_password("wrong", first)
    assert not owner_auth.verify_owner_password(PASSWORD, "not-a-hash")
