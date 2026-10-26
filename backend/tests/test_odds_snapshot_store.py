import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.config import ProviderMode, load_settings
from app.domain.models import Sport
from app.ingest.odds_raw import observed_at_from_path, read_raw, write_raw
from app.providers.errors import ProviderPayloadError, ProviderQuotaError
from app.providers.the_odds_api.adapter import TheOddsApiProvider
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.games_repository import aware
from app.storage.repository import OddsRepository

from tests.matchup_helpers import FIXTURES, new_session_factory

NOW = datetime(2026, 10, 14, 15, 0, 5, tzinfo=timezone.utc)
BODY = (FIXTURES / "the_odds_api" / "nfl_week6.json").read_text(encoding="utf-8")


def test_fetch_odds_raw_returns_the_exact_body_and_reads_the_quota() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, text=BODY, headers={"x-requests-remaining": "471"})

    client = TheOddsApiClient(
        "key", transport=httpx.MockTransport(handler), bookmakers=("draftkings", "fanduel")
    )
    text = asyncio.run(client.fetch_odds_raw(Sport.NFL))
    assert text == BODY
    assert client.quota_remaining == 471
    assert len(seen) == 2 and seen[0].path == "/v4/sports/"
    assert seen[1].params["markets"] == "h2h,spreads,totals"
    assert seen[1].params["bookmakers"] == "draftkings,fanduel"


@pytest.mark.parametrize("header", [None, "unknown"])
def test_quota_preflight_fails_closed_without_a_numeric_header(header) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        headers = {"x-requests-remaining": header} if header is not None else {}
        return httpx.Response(200, json=[], headers=headers)

    client = TheOddsApiClient("key", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderPayloadError, match="remaining-credits"):
        asyncio.run(client.fetch_quota_remaining())
    assert len(seen) == 1 and seen[0].path == "/v4/sports/"
    assert "markets" not in seen[0].params


@pytest.mark.parametrize("method", ["bulk", "raw", "props"])
def test_every_paid_odds_method_stops_at_exhausted_quota(method) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=[], headers={"x-requests-remaining": "-4"})

    client = TheOddsApiClient("key", transport=httpx.MockTransport(handler))
    call = {
        "bulk": lambda: client.fetch_odds(Sport.NBA),
        "raw": lambda: client.fetch_odds_raw(Sport.NBA),
        "props": lambda: client.fetch_event_player_props(Sport.NBA, "event-1"),
    }[method]
    with pytest.raises(ProviderQuotaError):
        asyncio.run(call())
    assert seen == ["/v4/sports/"]


def test_events_are_stamped_with_the_snapshot_time_not_the_parse_time() -> None:
    payload = json.loads(BODY)
    comparisons = TheOddsApiProvider.normalize_events(payload, Sport.NFL, NOW)
    assert len(comparisons) == 1
    assert comparisons[0].event.home_team == "Denver Broncos"
    assert {offer.observed_at for offer in comparisons[0].offers} == {NOW}
    assert len(comparisons[0].offers) == 5 * (2 + 2 + 2)  # five books, three markets, two sides


def test_raw_files_round_trip_and_carry_their_snapshot_time(tmp_path) -> None:
    path = write_raw(tmp_path, "the_odds_api", "odds_snapshot:nfl:2026-10-14:daily", NOW, BODY)
    assert path.parent == tmp_path / "the_odds_api" / "2026-10-14"
    assert path.name.endswith(".json.gz") and ":" not in path.name
    assert read_raw(path) == BODY
    assert observed_at_from_path(path) == NOW.replace(microsecond=0)
    assert not list(tmp_path.rglob("*.tmp"))  # written to a temp name, then renamed


def test_a_snapshot_is_saved_once_however_often_it_is_parsed(tmp_path) -> None:
    async def scenario() -> None:
        repo = OddsRepository(await new_session_factory(tmp_path))
        comparisons = TheOddsApiProvider.normalize_events(json.loads(BODY), Sport.NFL, NOW)
        assert await repo.record_snapshot("the_odds_api", Sport.NFL, comparisons, NOW, 471) is True
        saved = await repo.count_observations()
        assert saved == 30
        # A crash after the insert but before the slot was updated parses the same file again.
        assert await repo.record_snapshot("the_odds_api", Sport.NFL, comparisons, NOW, 471) is False
        assert await repo.count_observations() == saved
        later = NOW + timedelta(hours=1)
        again = TheOddsApiProvider.normalize_events(json.loads(BODY), Sport.NFL, later)
        assert await repo.record_snapshot("the_odds_api", Sport.NFL, again, later, 468) is True
        assert await repo.count_observations() == saved * 2
        assert await repo.latest_quota("the_odds_api") == 468
        assert aware(await repo.latest_snapshot_at("the_odds_api")) == later
        assert await repo.latest_snapshot_at("kalshi") is None
        assert await repo.latest_quota("kalshi") is None

    asyncio.run(scenario())


def test_the_paid_client_only_exists_in_live_mode_with_a_key() -> None:
    fixture = load_settings()  # the default: fixture mode, no key
    assert TheOddsApiClient.from_settings(fixture) is None
    live = replace(
        fixture, provider_mode=ProviderMode.LIVE, the_odds_api_key="k", odds_bookmakers=("fanduel",)
    )
    client = TheOddsApiClient.from_settings(live)
    assert client is not None and client.coverage == {"bookmakers": "fanduel"}
    assert TheOddsApiClient.from_settings(replace(live, the_odds_api_key=None)) is None
    assert TheOddsApiClient.from_settings(replace(live, provider_mode=ProviderMode.FIXTURE)) is None


def test_a_snapshot_taken_in_the_same_second_by_another_sport_is_still_stored(tmp_path) -> None:
    """NFL and NBA snapshots in one run-due pass can share a timestamp; each must be kept."""

    async def scenario() -> None:
        repo = OddsRepository(await new_session_factory(tmp_path))
        nfl = TheOddsApiProvider.normalize_events(json.loads(BODY), Sport.NFL, NOW)
        nba_payload = json.loads(BODY)
        nba_payload[0]["id"] = "other-sport-event"
        nba = TheOddsApiProvider.normalize_events(nba_payload, Sport.NBA, NOW)
        assert await repo.record_snapshot("the_odds_api", Sport.NFL, nfl, NOW, 450) is True
        assert await repo.record_snapshot("the_odds_api", Sport.NBA, nba, NOW, 447) is True
        assert await repo.count_observations() == 60
        assert await repo.record_snapshot("the_odds_api", Sport.NBA, nba, NOW, 447) is False
        later = NOW + timedelta(minutes=5)
        nba_later = TheOddsApiProvider.normalize_events(nba_payload, Sport.NBA, later)
        await repo.record_snapshot("the_odds_api", Sport.NBA, nba_later, later, 444)
        assert aware(await repo.latest_snapshot_at("the_odds_api", Sport.NFL)) == NOW
        assert aware(await repo.latest_snapshot_at("the_odds_api", Sport.NBA)) == later
        assert aware(await repo.latest_snapshot_at("the_odds_api")) == later

    asyncio.run(scenario())
