import asyncio
from datetime import datetime, timedelta, timezone

from app.config import ProviderMode, Settings
from app.domain.models import MarketType
from app.providers.fixture import FixtureOddsProvider
from app.storage.database import create_engine, create_schema, create_session_factory
from app.storage.repository import OddsRepository
from sqlalchemy.ext.asyncio import create_async_engine


def _settings(database_url: str) -> Settings:
    return Settings(
        app_env="test",
        provider_mode=ProviderMode.FIXTURE,
        odds_provider="fixture",
        the_odds_api_key=None,
        database_url=database_url,
        persistence_enabled=True,
        observation_retention_days=90,
        cache_ttl_seconds=300,
        provider_min_quota_remaining=5,
        provider_timeout_seconds=15.0,
        provider_max_retries=2,
        provider_backoff_seconds=0.5,
    )


def test_create_engine_disables_statement_cache_for_asyncpg() -> None:
    engine = create_engine(_settings("postgresql+asyncpg://user:pass@localhost/db"))
    assert engine.dialect.name == "postgresql"


def test_create_engine_leaves_sqlite_connect_args_untouched() -> None:
    engine = create_engine(_settings("sqlite+aiosqlite:///:memory:"))
    assert engine.dialect.name == "sqlite"


def test_fixture_comparisons_persist_as_immutable_observations() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await create_schema(engine)
        repository = OddsRepository(create_session_factory(engine))
        provider = FixtureOddsProvider()
        comparisons = await provider.get_comparisons()
        started_at = datetime.now(timezone.utc)

        await repository.record_comparisons(
            provider=provider.name,
            comparisons=comparisons,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
        await repository.record_comparisons(
            provider=provider.name,
            comparisons=comparisons,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        assert await repository.count_refresh_runs("fixture") == 2
        assert await repository.count_observations(comparisons[0].event.id) == 16
        history = await repository.get_history(comparisons[0].event.id, MarketType.MONEYLINE)
        assert len(history) == 6
        assert all(offer.market_type is MarketType.MONEYLINE for offer in history)

        old_comparisons = [
            comparison.model_copy(
                update={
                    "offers": [
                        offer.model_copy(
                            update={"observed_at": started_at - timedelta(days=91)}
                        )
                        for offer in comparison.offers
                    ]
                }
            )
            for comparison in comparisons
        ]
        await repository.record_comparisons(
            provider=provider.name,
            comparisons=old_comparisons,
            started_at=started_at - timedelta(days=91),
            completed_at=started_at - timedelta(days=91),
        )

        removed = await repository.prune_expired(90, datetime.now(timezone.utc))

        assert removed == 8
        assert await repository.count_refresh_runs("fixture") == 2
        assert await repository.count_observations(comparisons[0].event.id) == 16
        await engine.dispose()

    asyncio.run(scenario())

def test_latest_comparisons_returns_only_the_newest_snapshot_per_event() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await create_schema(engine)
        repository = OddsRepository(create_session_factory(engine))
        provider = FixtureOddsProvider()
        comparisons = await provider.get_comparisons()
        older = datetime.now(timezone.utc) - timedelta(hours=2)
        newer = datetime.now(timezone.utc) - timedelta(hours=1)

        def restamp(observed_at: datetime):
            return [
                comparison.model_copy(
                    update={
                        "offers": [
                            offer.model_copy(update={"observed_at": observed_at})
                            for offer in comparison.offers
                        ]
                    }
                )
                for comparison in comparisons
            ]

        for observed_at in (older, newer):
            await repository.record_comparisons(
                provider=provider.name,
                comparisons=restamp(observed_at),
                started_at=observed_at,
                completed_at=observed_at,
            )

        latest = await repository.latest_comparisons(provider.name, sport=None)

        assert {c.event.id for c in latest} == {c.event.id for c in comparisons}
        for comparison in latest:
            expected = next(c for c in comparisons if c.event.id == comparison.event.id)
            assert len(comparison.offers) == len(expected.offers)
            # SQLite drops tzinfo; Postgres keeps it.
            assert {
                offer.observed_at.replace(tzinfo=timezone.utc) for offer in comparison.offers
            } == {newer}

    asyncio.run(scenario())


def test_latest_comparisons_is_empty_when_nothing_was_ever_stored() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await create_schema(engine)
        repository = OddsRepository(create_session_factory(engine))

        assert await repository.latest_comparisons("the_odds_api", sport=None) == []

    asyncio.run(scenario())
