import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from app.api.routes import build_provider
from app.config import ProviderMode, load_settings
from app.main import app
from app.providers.cache import CachedOddsProvider
from app.providers.errors import ProviderTimeoutError
from app.providers.fixture import FixtureOddsProvider
from app.providers.stub import EmptyOddsProvider
from app.providers.the_odds_api import TheOddsApiProvider

from tests.local_client import TestClient

client = TestClient(app)


class SpyRepository:
    def __init__(self) -> None:
        self.calls = []

    async def record_comparisons(self, **kwargs):
        self.calls.append(kwargs)
        return 1

    async def record_refresh_failure(self, **kwargs):
        self.calls.append({"failure": kwargs})
        return 1

    async def prune_expired(self, *args, **kwargs):
        return 0

    async def latest_comparisons(self, provider, sport):
        return []

    async def last_success_at(self, provider, sport):
        return None


class FailingProvider:
    name = "failing"
    status = "configured"

    async def get_comparisons(
        self,
        sport=None,
        event_id=None,
        market_type=None,
        force_refresh=False,
        include_player_props=False,
    ):
        raise ProviderTimeoutError("provider timed out")


def test_health() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_reports_unconfigured_provider_without_fetching_odds(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", EmptyOddsProvider())

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "unconfigured",
        "provider": "unconfigured",
        "provider_status": "not_configured",
        "data_stale": False,
        "generated_at": None,
        "event_count": 0,
        "coverage": {"regions": "us", "bookmakers": []},
        "capabilities": {"public_expensive_refresh": True},
        "prediction_markets": {"kalshi_enabled": False},
    }


def test_status_reports_fixture_cache_after_odds_refresh(monkeypatch) -> None:
    fixture = FixtureOddsProvider()
    cached = CachedOddsProvider(fixture, ttl_seconds=60)
    monkeypatch.setattr("app.api.routes.provider", cached)

    odds_response = client.get("/api/v1/odds", params={"sport": "nfl"})
    status_response = client.get("/api/v1/status")

    assert odds_response.status_code == 200
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "ok"
    assert payload["provider"] == "fixture"
    assert payload["provider_status"] == "configured"
    assert payload["event_count"] == 1
    assert payload["generated_at"] is not None


def test_odds_contract_allows_empty_provider_result(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", EmptyOddsProvider())
    monkeypatch.setattr("app.api.routes.repository", None)

    response = client.get("/api/v1/odds", params={"sport": "nba"})

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["provider_status"] == {"unconfigured": "not_configured"}


def test_provider_selection_defaults_to_empty_without_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_PROVIDER", raising=False)
    monkeypatch.setattr("app.config._DOTENV_PATH", tmp_path / ".env")

    assert isinstance(build_provider(), EmptyOddsProvider)


def test_provider_selection_can_use_fixture_without_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.setenv("ODDS_PROVIDER", "fixture")
    monkeypatch.setattr("app.config._DOTENV_PATH", tmp_path / ".env")

    assert isinstance(build_provider(), FixtureOddsProvider)


def test_live_provider_requires_explicit_live_mode(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_MODE", "fixture")

    with pytest.raises(RuntimeError, match="PROVIDER_MODE=live"):
        build_provider()


def test_settings_load_provider_controls(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_MODE", "live")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("PROVIDER_MIN_QUOTA_REMAINING", "7")

    settings = load_settings()

    assert settings.provider_mode is ProviderMode.LIVE
    assert settings.cache_ttl_seconds == 120
    assert settings.provider_min_quota_remaining == 7


def test_settings_loads_validated_kalshi_base_url(monkeypatch) -> None:
    monkeypatch.setenv("KALSHI_BASE_URL", "https://example.test/trade-api/v2/")

    assert load_settings().kalshi_base_url == "https://example.test/trade-api/v2"

    monkeypatch.setenv("KALSHI_BASE_URL", "http://example.test/trade-api/v2")
    with pytest.raises(ValueError, match="HTTPS URL"):
        load_settings()


def test_settings_load_dotenv_for_local_live_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PROVIDER_MODE", raising=False)
    monkeypatch.delenv("ODDS_PROVIDER", raising=False)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "PROVIDER_MODE=live\n"
        "ODDS_PROVIDER=the_odds_api\n"
        "THE_ODDS_API_KEY=dotenv-test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.config._DOTENV_PATH", dotenv_path)

    settings = load_settings()

    assert settings.provider_mode is ProviderMode.LIVE
    assert settings.odds_provider == "the_odds_api"
    assert settings.the_odds_api_key == "dotenv-test-key"


def test_fixture_provider_reports_configured_status(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())

    response = client.get("/api/v1/odds", params={"sport": "nfl"})

    assert response.status_code == 200
    assert response.json()["provider_status"] == {"fixture": "configured"}
    assert response.json()["events"][0]["offers"]
    assert response.json()["events"][0]["event"]["id"].startswith("event:")


def test_odds_route_scopes_force_refresh_to_event_market(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())
    monkeypatch.setattr("app.api.routes.repository", None)

    initial = client.get("/api/v1/odds", params={"sport": "nfl"}).json()
    event_id = initial["events"][0]["event"]["id"]
    response = client.get(
        "/api/v1/odds",
        params={
            "sport": "nfl",
            "event_id": event_id,
            "market_type": "moneyline",
            "force_refresh": "true",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event"]["id"] == event_id
    assert {offer["market_type"] for offer in payload["events"][0]["offers"]} == {"moneyline"}


def test_odds_route_scopes_alternate_spreads(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())
    monkeypatch.setattr("app.api.routes.repository", None)

    response = client.get(
        "/api/v1/odds",
        params={"sport": "nfl", "market_type": "alternate_spread"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"]
    assert {offer["market_type"] for offer in payload["events"][0]["offers"]} == {
        "alternate_spread"
    }


def test_odds_route_persists_fixture_comparison(monkeypatch) -> None:
    repository = SpyRepository()
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())
    monkeypatch.setattr("app.api.routes.repository", repository)

    response = client.get("/api/v1/odds", params={"sport": "nba"})

    assert response.status_code == 200
    assert response.json()["events"]
    assert len(repository.calls) == 1
    assert repository.calls[0]["provider"] == "fixture"


def test_odds_route_returns_typed_provider_error(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FailingProvider())
    monkeypatch.setattr("app.api.routes.repository", None)

    response = client.get("/api/v1/odds", params={"sport": "nfl"})

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["provider_status"] == {"failing": "error"}
    assert response.json()["provider_details"]["failing"]["error_type"] == "timeout"


def test_odds_route_records_failed_refresh_separately(monkeypatch) -> None:
    repository = SpyRepository()
    monkeypatch.setattr("app.api.routes.provider", FailingProvider())
    monkeypatch.setattr("app.api.routes.repository", repository)

    response = client.get("/api/v1/odds", params={"sport": "nfl"})

    assert response.status_code == 200
    assert len(repository.calls) == 1
    assert "failure" in repository.calls[0]
    assert repository.calls[0]["failure"]["error_type"] == "timeout"


def test_odds_route_include_props_returns_player_prop_offers(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())
    monkeypatch.setattr("app.api.routes.repository", None)

    without_props = client.get("/api/v1/odds", params={"sport": "nba"}).json()
    with_props = client.get(
        "/api/v1/odds", params={"sport": "nba", "include_props": "true"}
    ).json()

    assert not any(
        offer["market_type"] == "player_points" for offer in without_props["events"][0]["offers"]
    )
    assert any(
        offer["market_type"] == "player_points" for offer in with_props["events"][0]["offers"]
    )


def test_health_metrics_reports_provider_state(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.provider", FixtureOddsProvider())

    client.get("/api/v1/odds", params={"sport": "nfl"})
    response = client.get("/api/v1/health/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "last_activity_at" in payload
    assert "quota_remaining" in payload


def test_health_errors_is_closed_without_admin_secret() -> None:
    assert client.get("/api/v1/health/errors").status_code == 403


def test_provider_selection_uses_the_odds_api_key(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_MODE", "live")

    assert isinstance(build_provider(), TheOddsApiProvider)


def test_odds_route_serves_last_saved_snapshot_when_provider_fails(monkeypatch) -> None:
    saved_at = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    saved = asyncio.run(FixtureOddsProvider().get_comparisons())
    saved = [
        c.model_copy(
            update={"offers": [o.model_copy(update={"observed_at": saved_at}) for o in c.offers]}
        )
        for c in saved
    ]

    class SavedSnapshotRepository(SpyRepository):
        async def latest_comparisons(self, provider, sport):
            return saved

    monkeypatch.setattr("app.api.routes.provider", FailingProvider())
    monkeypatch.setattr("app.api.routes.repository", SavedSnapshotRepository())

    response = client.get("/api/v1/odds")

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == len(saved)
    details = body["provider_details"]["failing"]
    assert details["stale"] is True
    assert details["error_type"] == "timeout"
    assert details["last_refreshed_at"].startswith("2026-09-28T12:00:00")
    assert body["provider_status"]["failing"] == "stale"


def test_odds_route_reports_error_when_provider_fails_and_nothing_was_saved(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.api.routes.provider", FailingProvider())
    monkeypatch.setattr("app.api.routes.repository", SpyRepository())

    body = client.get("/api/v1/odds").json()

    assert body["events"] == []
    assert body["provider_status"]["failing"] == "error"
    assert body["provider_details"]["failing"]["stale"] is False


class RecordingProvider(FailingProvider):
    """Fails loudly if the live provider is contacted."""

    name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    async def get_comparisons(self, **kwargs):
        self.calls += 1
        return await super().get_comparisons(**kwargs)


def _saved_snapshot(observed_at):
    saved = asyncio.run(FixtureOddsProvider().get_comparisons())
    return [
        c.model_copy(
            update={"offers": [o.model_copy(update={"observed_at": observed_at}) for o in c.offers]}
        )
        for c in saved
    ]


class DailyGateRepository(SpyRepository):
    def __init__(self, last_success, saved) -> None:
        super().__init__()
        self.last_success = last_success
        self.saved = saved

    async def last_success_at(self, provider, sport):
        return self.last_success

    async def latest_comparisons(self, provider, sport):
        return self.saved


def _gate(monkeypatch, age: timedelta, ttl_seconds: int = 86400):
    now = datetime.now(timezone.utc)
    provider = RecordingProvider()
    repository = DailyGateRepository(now - age, _saved_snapshot(now - age))
    monkeypatch.setattr("app.api.routes.provider", provider)
    monkeypatch.setattr("app.api.routes.repository", repository)
    monkeypatch.setattr(
        "app.api.routes.settings",
        replace(load_settings(), cache_ttl_seconds=ttl_seconds),
    )
    return provider, repository


def test_recent_saved_refresh_is_served_without_calling_the_provider(monkeypatch) -> None:
    provider, repository = _gate(monkeypatch, age=timedelta(hours=3))

    body = client.get("/api/v1/odds?sport=nfl").json()

    assert provider.calls == 0  # survives a machine restart: gate is the database
    assert len(body["events"]) > 0
    details = body["provider_details"]["failing"]
    assert details["stale"] is False
    assert details["cache_hit"] is True
    assert body["provider_status"]["failing"] == "configured"
    assert repository.calls == []  # nothing new to record


def test_saved_refresh_older_than_the_ttl_triggers_a_live_refresh(monkeypatch) -> None:
    provider, _ = _gate(monkeypatch, age=timedelta(hours=25))

    client.get("/api/v1/odds?sport=nfl")

    assert provider.calls == 1


def test_manual_force_refresh_bypasses_the_daily_gate(monkeypatch) -> None:
    provider, _ = _gate(monkeypatch, age=timedelta(minutes=5))

    client.get("/api/v1/odds?sport=nfl&force_refresh=true")

    assert provider.calls == 1
