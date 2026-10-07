import hmac
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.config import ProviderMode, Settings, load_settings
from app.domain.models import (
    ComparisonResponse,
    EventComparison,
    HistoryResponse,
    MarketType,
    ProviderDetails,
    Sport,
)
from app.error_log import recent_errors
from app.identity import canonicalize_comparison, scope_comparisons
from app.providers.base import OddsProvider
from app.providers.cache import CachedOddsProvider
from app.providers.errors import OddsProviderError
from app.providers.fixture import FixtureOddsProvider
from app.providers.stub import EmptyOddsProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.providers.the_odds_api.client import TheOddsApiClient
from app.storage.database import create_engine, create_session_factory
from app.storage.repository import OddsRepository
from app.throttle import SlidingWindowLimiter

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


def build_provider(settings: Optional[Settings] = None) -> OddsProvider:
    active_settings = settings or load_settings()
    if active_settings.the_odds_api_key:
        if active_settings.provider_mode is not ProviderMode.LIVE:
            raise RuntimeError("THE_ODDS_API_KEY requires PROVIDER_MODE=live")
        return TheOddsApiProvider(
            TheOddsApiClient(
                active_settings.the_odds_api_key,
                timeout=active_settings.provider_timeout_seconds,
                regions=active_settings.odds_regions,
                bookmakers=active_settings.odds_bookmakers,
            )
        )
    if active_settings.odds_provider == "fixture":
        if active_settings.provider_mode is ProviderMode.LIVE:
            raise RuntimeError("ODDS_PROVIDER=fixture requires PROVIDER_MODE=fixture")
        return FixtureOddsProvider()
    if not active_settings.the_odds_api_key:
        if active_settings.provider_mode is ProviderMode.LIVE:
            return EmptyOddsProvider()
        if active_settings.odds_provider == "fixture":
            return FixtureOddsProvider()
        return EmptyOddsProvider()

    return EmptyOddsProvider()


settings = load_settings()
provider: OddsProvider = CachedOddsProvider(
    build_provider(settings),
    ttl_seconds=settings.cache_ttl_seconds,
    min_quota_remaining=settings.provider_min_quota_remaining,
)
repository = None
if settings.persistence_enabled:
    repository = OddsRepository(create_session_factory(create_engine(settings)))


# Expensive refreshes (force_refresh / include_props) are admin-only and rate limited.
# Per client: a short burst guard. Global: a hard budget per window, independent of cache state.
_client_limiters: dict[str, SlidingWindowLimiter] = {}
_CLIENT_LIMIT = 2
_CLIENT_WINDOW_SECONDS = 600
_budget = SlidingWindowLimiter(
    settings.admin_refresh_budget, settings.admin_refresh_budget_window_seconds
)


def _is_admin(request: Request) -> bool:
    """True when the request carries the admin secret (constant-time compare).

    With no secret configured only fixture mode (which never spends) is treated as admin so
    local development keeps working; live mode fails closed.
    """
    secret = settings.admin_refresh_secret
    if not secret:
        return settings.provider_mode is ProviderMode.FIXTURE
    supplied = request.headers.get("x-admin-token")
    return bool(supplied) and hmac.compare_digest(supplied.encode(), secret.encode())


def _client_key(request: Request) -> str:
    fallback = request.client.host if request.client else "unknown"
    return request.headers.get("fly-client-ip") or fallback


def _allow_expensive_refresh(request: Request) -> bool:
    """Per-client + global throttle. Only meaningful (and enforced) when live calls can spend."""
    if settings.provider_mode is not ProviderMode.LIVE:
        return True
    limiter = _client_limiters.setdefault(
        _client_key(request), SlidingWindowLimiter(_CLIENT_LIMIT, _CLIENT_WINDOW_SECONDS)
    )
    return limiter.allow() and _budget.allow()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status")
async def status() -> dict[str, object]:
    provider_status = provider.status
    return {
        "status": "unconfigured" if provider_status == "not_configured" else "ok",
        "provider": provider.name,
        "provider_status": provider_status,
        "data_stale": provider_status == "stale",
        "generated_at": getattr(provider, "last_refreshed_at", None),
        "event_count": getattr(provider, "cached_event_count", 0),
        "coverage": {
            "regions": settings.odds_regions,
            "bookmakers": list(settings.odds_bookmakers),
        },
    }


@router.get("/health/metrics")
async def health_metrics(request: Request) -> dict[str, object]:
    metrics: dict[str, object] = {
        "last_activity_at": getattr(provider, "last_refreshed_at", None),
        "data_freshness_at": getattr(provider, "last_refreshed_at", None),
        "event_count": getattr(provider, "cached_event_count", 0),
        "cache_hit": getattr(provider, "cache_hit", False),
    }
    if _is_admin(request):  # provider quota is operational data, not for anonymous callers
        metrics["quota_remaining"] = getattr(provider, "quota_remaining", None)
    return metrics


@router.get("/health/errors")
async def health_errors(request: Request) -> dict[str, object]:
    # Fail closed: needs the admin secret even in fixture mode when one is configured, and is
    # unreachable in live mode without one.
    if not (settings.admin_refresh_secret and _is_admin(request)):
        raise HTTPException(status_code=403, detail="Forbidden")
    return {"errors": recent_errors()}


@router.get("/odds", response_model=ComparisonResponse)
async def get_odds(
    request: Request,
    response: Response,
    sport: Optional[Sport] = Query(default=None),
    event_id: Optional[str] = Query(default=None, min_length=1),
    market_type: Optional[MarketType] = Query(default=None),
    force_refresh: bool = Query(default=False),
    include_props: bool = Query(
        default=False,
        description=(
            "Also fetch player-prop offers. Costs one extra provider request "
            "per event, so only set this when the UI is actually showing a "
            "player-prop market."
        ),
    ),
) -> ComparisonResponse:
    started_at = datetime.now(timezone.utc)
    is_admin = _is_admin(request)
    if force_refresh or include_props:
        if not is_admin:
            reason = "unauthorized"
        elif not _allow_expensive_refresh(request):
            reason = "throttled"
        else:
            reason = None
            logger.warning(
                "admin expensive refresh sport=%s force_refresh=%s include_props=%s client=%s",
                sport.value if sport else "all",
                force_refresh,
                include_props,
                _client_key(request),
            )
        if reason is not None:
            # Serve the cached/saved snapshot instead of spending provider quota.
            force_refresh = include_props = False
            response.headers["X-Refresh-Ignored"] = reason
    quota_remaining = getattr(provider, "quota_remaining", None) if is_admin else None
    if not force_refresh and not include_props and repository is not None and sport is not None:
        # Daily gate: the in-memory cache dies whenever Fly stops the machine, so
        # the last successful refresh recorded in the database is what actually
        # limits how often we spend provider quota. Manual refresh bypasses it.
        last_success = await repository.last_success_at(provider.name, sport)
        if last_success is not None:
            last_success = _aware(last_success)
            if started_at - last_success < timedelta(seconds=settings.cache_ttl_seconds):
                saved = await repository.latest_comparisons(provider.name, sport)
                if saved:
                    logger.info(
                        "odds served from saved snapshot source=%s sport=%s games=%d age_s=%d",
                        provider.name,
                        sport.value,
                        len(saved),
                        (started_at - last_success).total_seconds(),
                    )
                    return ComparisonResponse(
                        generated_at=started_at,
                        provider_status={provider.name: "configured"},
                        provider_details={
                            provider.name: ProviderDetails(
                                status="configured",
                                cache_hit=True,
                                last_refreshed_at=last_success,
                                quota_remaining=quota_remaining,
                            )
                        },
                        events=scope_comparisons(
                            [canonicalize_comparison(c) for c in saved], event_id, market_type
                        ),
                    )
    provider_error: Optional[OddsProviderError] = None
    try:
        comparisons = await provider.get_comparisons(
            sport=sport,
            event_id=event_id,
            market_type=market_type,
            force_refresh=force_refresh,
            include_player_props=include_props,
        )
    except OddsProviderError as error:
        provider_error = error
        comparisons = []
    saved_snapshot_at: Optional[datetime] = None
    if provider_error is not None and repository is not None:
        # Keep serving the last good odds instead of an empty board -- the
        # in-memory cache is gone after a Fly machine autostops.
        saved = await repository.latest_comparisons(provider.name, sport)
        if saved:
            comparisons = saved
            saved_snapshot_at = _newest_observation(saved)
    comparisons = scope_comparisons(
        [canonicalize_comparison(comparison) for comparison in comparisons],
        event_id,
        market_type,
    )
    completed_at = datetime.now(timezone.utc)
    _log_fetch(sport, provider, comparisons, provider_error, saved_snapshot_at)
    if repository is not None:
        if provider_error is None:
            await repository.record_comparisons(
                provider=provider.name,
                comparisons=comparisons,
                started_at=started_at,
                completed_at=completed_at,
                sport=sport,
            )
        else:
            await repository.record_refresh_failure(
                provider=provider.name,
                started_at=started_at,
                completed_at=completed_at,
                error_type=provider_error.error_type,
                error_message=str(provider_error),
                sport=sport,
                quota_remaining=getattr(provider, "quota_remaining", None),
            )
        await repository.prune_expired(settings.observation_retention_days, completed_at)
    if provider_error is None:
        status = provider.status
    else:
        status = "stale" if saved_snapshot_at else "error"
    return ComparisonResponse(
        generated_at=completed_at,
        provider_status={provider.name: status},
        provider_details={
            provider.name: ProviderDetails(
                status=status,
                cache_hit=bool(getattr(provider, "cache_hit", False)),
                stale=provider.status == "stale" or saved_snapshot_at is not None,
                last_refreshed_at=saved_snapshot_at
                or getattr(provider, "last_refreshed_at", None),
                error_type=(
                    provider_error.error_type
                    if provider_error
                    else getattr(provider, "last_error_type", None)
                ),
                quota_remaining=quota_remaining,
            )
        },
        events=list(comparisons),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _newest_observation(comparisons: list[EventComparison]) -> Optional[datetime]:
    observed = [offer.observed_at for c in comparisons for offer in c.offers]
    if not observed:
        return None
    return _aware(max(observed))


def _log_fetch(
    sport: Optional[Sport],
    provider: OddsProvider,
    comparisons: Sequence[EventComparison],
    error: Optional[OddsProviderError],
    saved_snapshot_at: Optional[datetime],
) -> None:
    """One line per odds request: source, sport, outcome, games, offers, quota."""
    fields = (
        f"source={provider.name} sport={sport.value if sport else 'all'} "
        f"games={len(comparisons)} offers={sum(len(c.offers) for c in comparisons)} "
        f"cache_hit={bool(getattr(provider, 'cache_hit', False))} "
        f"quota_remaining={getattr(provider, 'quota_remaining', None)}"
    )
    if error is None:
        logger.info("odds fetch ok %s", fields)
    else:
        logger.error(
            "odds fetch failed error_type=%s served_saved_snapshot=%s %s detail=%s",
            error.error_type,
            saved_snapshot_at is not None,
            fields,
            error,
        )


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    event_id: str = Query(min_length=1),
    market_type: Optional[MarketType] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> HistoryResponse:
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistence is disabled")
    offers = await repository.get_history(
        event_id=event_id,
        market_type=market_type,
        limit=limit,
    )
    return HistoryResponse(event_id=event_id, offers=offers)
