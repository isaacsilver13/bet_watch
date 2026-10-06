import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from app import error_log
from app.config import ProviderMode, load_settings
from app.domain.models import Sport
from app.main import app
from app.providers.cache import CachedOddsProvider
from app.providers.fixture import FixtureOddsProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.throttle import SlidingWindowLimiter
from fastapi.testclient import TestClient

client = TestClient(app)
SECRET = "test-admin-secret"
ADMIN = {"X-Admin-Token": SECRET}
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "the_odds_api_nfl.json"


class CountingProvider:
    """Fixture odds that count upstream calls and report a quota figure."""

    name = "fixture"
    status = "configured"
    quota_remaining = 321

    def __init__(self) -> None:
        self.calls = []
        self._inner = FixtureOddsProvider()

    async def get_comparisons(self, **kwargs):
        self.calls.append(kwargs)
        return await self._inner.get_comparisons(**kwargs)


@pytest.fixture()
def live(monkeypatch):
    """Live-mode settings with an admin secret; the provider never touches the network."""
    from app.api import routes

    settings = replace(
        load_settings(),
        provider_mode=ProviderMode.LIVE,
        admin_refresh_secret=SECRET,
        admin_refresh_budget=3,
    )
    counting = CountingProvider()
    monkeypatch.setattr(routes, "settings", settings)
    monkeypatch.setattr(routes, "provider", CachedOddsProvider(counting, ttl_seconds=86400))
    monkeypatch.setattr(routes, "repository", None)
    monkeypatch.setattr(routes, "_budget", SlidingWindowLimiter(3, 86400))
    monkeypatch.setattr(routes, "_client_limiters", {})
    return counting


def test_anonymous_force_refresh_and_props_do_not_reach_the_provider(live):
    client.get("/api/v1/odds?sport=nfl")  # warm cache: 1 call
    assert len(live.calls) == 1
    for qs in ("force_refresh=true", "include_props=true", "force_refresh=true&include_props=true"):
        r = client.get(f"/api/v1/odds?sport=nfl&{qs}")
        assert r.status_code == 200
        assert r.headers["X-Refresh-Ignored"] == "unauthorized"
    assert len(live.calls) == 1


def test_wrong_admin_token_is_treated_as_anonymous(live):
    client.get("/api/v1/odds?sport=nfl")
    r = client.get("/api/v1/odds?sport=nfl&force_refresh=true", headers={"X-Admin-Token": "nope"})
    assert r.headers["X-Refresh-Ignored"] == "unauthorized"
    assert len(live.calls) == 1


def test_varying_query_params_does_not_mint_new_upstream_calls(live):
    client.get("/api/v1/odds?sport=nfl")
    for market in ("moneyline", "spread", "total"):
        client.get(f"/api/v1/odds?sport=nfl&market_type={market}")
    client.get("/api/v1/odds?sport=nfl&event_id=event:doesnotexist")
    assert len(live.calls) == 1


def test_admin_force_refresh_is_throttled_per_client(live):
    r1 = client.get("/api/v1/odds?sport=nfl&force_refresh=true", headers=ADMIN)
    r2 = client.get("/api/v1/odds?sport=nfl&force_refresh=true", headers=ADMIN)
    r3 = client.get("/api/v1/odds?sport=nfl&force_refresh=true", headers=ADMIN)
    assert "X-Refresh-Ignored" not in r1.headers and "X-Refresh-Ignored" not in r2.headers
    assert r3.headers["X-Refresh-Ignored"] == "throttled"
    assert r3.json()["events"]  # still serves the cached board
    assert len(live.calls) == 2


def test_global_budget_caps_refreshes_across_clients(live):
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"):
        client.get(
            "/api/v1/odds?sport=nfl&force_refresh=true",
            headers={**ADMIN, "Fly-Client-IP": ip},
        )
    assert len(live.calls) == 3  # budget=3; the fourth is served from cache


def test_live_mode_without_a_secret_fails_closed(live, monkeypatch):
    from app.api import routes

    monkeypatch.setattr(routes, "settings", replace(routes.settings, admin_refresh_secret=None))
    client.get("/api/v1/odds?sport=nfl")
    r = client.get("/api/v1/odds?sport=nfl&force_refresh=true", headers=ADMIN)
    assert r.headers["X-Refresh-Ignored"] == "unauthorized"
    assert len(live.calls) == 1


def test_quota_hidden_from_anonymous_visible_to_admin(live):
    anon = client.get("/api/v1/odds?sport=nfl").json()
    assert anon["provider_details"]["fixture"]["quota_remaining"] is None
    assert "quota_remaining" not in client.get("/api/v1/health/metrics").json()
    assert "321" not in client.get("/api/v1/status").text
    admin = client.get("/api/v1/health/metrics", headers=ADMIN).json()
    assert admin["quota_remaining"] == 321


def test_health_and_cached_reads_stay_public(live):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/status").status_code == 200
    r = client.get("/api/v1/odds?sport=nfl")
    assert r.status_code == 200 and r.json()["events"]


def test_health_errors_requires_admin_and_redacts(live):
    error_log._recent_errors.clear()
    handler = error_log.RingBufferHandler(level=logging.ERROR)
    log = logging.getLogger("t-sec")
    log.addHandler(handler)
    log.error("GET https://api.example.com/v4/odds?apiKey=REALKEY123&regions=us failed")
    log.removeHandler(handler)
    assert client.get("/api/v1/health/errors").status_code == 403
    assert client.get("/api/v1/health/errors", headers={"X-Admin-Token": "x"}).status_code == 403
    ok = client.get("/api/v1/health/errors", headers=ADMIN)
    assert ok.status_code == 200 and "REALKEY123" not in ok.text
    error_log._recent_errors.clear()


def test_event_scoped_props_only_fetch_the_requested_event():
    payload = json.loads(FIXTURE_PATH.read_text())
    from tests.test_the_odds_api import FakeTheOddsApiClient

    # duplicate the event so the sport has two
    second = json.loads(json.dumps(payload[0]))
    second["id"] = "event-nfl-2"
    second["home_team"], second["away_team"] = "Other Home", "Other Away"
    client_ = FakeTheOddsApiClient([payload[0], second])
    provider = TheOddsApiProvider(client_)

    both = asyncio.run(provider.get_comparisons(Sport.NFL, include_player_props=True))
    assert sorted(client_.requested_prop_event_ids) == ["event-nfl-1", "event-nfl-2"]
    assert len(both) == 2

    client_.requested_prop_event_ids.clear()
    target = both[1].event.id
    only = asyncio.run(
        provider.get_comparisons(Sport.NFL, event_id=target, include_player_props=True)
    )
    assert client_.requested_prop_event_ids == ["event-nfl-2"]
    assert len(only) == 1
