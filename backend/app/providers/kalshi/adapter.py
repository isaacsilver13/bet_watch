import asyncio
import json
import re
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union

from app.domain.models import Event, MatchStatus, PredictionContract, Sport
from app.identity import canonical_event_id, canonical_team_name, normalize_name
from app.providers.errors import OddsProviderError, ProviderPayloadError
from app.providers.kalshi.client import SERIES, KalshiClient

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
_GAME_RULE = re.compile(r"^If .+ wins the .+ game", re.IGNORECASE)
_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MARKET_SLUG = {Sport.NFL: "nfl-game", Sport.NBA: "nba-game"}
_MONTHS = {
    m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)
}


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prob(value: Any) -> Optional[float]:
    number = _num(value)
    return number if number is not None and 0 <= number <= 1 else None


def _time(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ticker_date(event_ticker: str) -> Optional[date]:
    found = _TICKER_DATE.search(event_ticker)
    if not found or found.group(2) not in _MONTHS:
        return None
    return date(2000 + int(found.group(1)), _MONTHS[found.group(2)], int(found.group(3)))


def _label_matches(label: str, team: str) -> bool:
    """Kalshi labels teams by city ("Washington"); require a whole-word prefix of the team."""
    name, short = canonical_team_name(team), normalize_name(label)
    return bool(short) and (name == short or name.startswith(short + " "))


def _candidates(group: list, events: Iterable[Event]) -> list:
    """Events that agree on both teams and game date. Anything weaker is not a match."""
    game_day = _ticker_date(group[0].get("event_ticker", ""))
    first, second = (m.get("yes_sub_title", "") for m in group)
    found = []
    for event in events:
        if game_day is None:
            break
        # ponytail: Kalshi gives the local game date, not kickoff time; a late-evening US kickoff
        # is next-day UTC, so accept 0-1 days. Add a tz-aware kickoff match if collisions appear.
        days = (event.start_time.astimezone(timezone.utc).date() - game_day).days
        home, away = event.home_team, event.away_team
        sides_agree = (_label_matches(first, home) and _label_matches(second, away)) or (
            _label_matches(first, away) and _label_matches(second, home)
        )
        if days in (0, 1) and sides_agree:
            found.append(event)
    return found


def normalize_markets(
    payload: dict, sport: Sport, events: Sequence[Event], observed_at: datetime
) -> list:
    markets = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, list):
        raise ProviderPayloadError("Kalshi payload has no markets list")
    valid = [
        m for m in markets if isinstance(m, dict) and m.get("ticker") and m.get("event_ticker")
    ]
    groups: dict = defaultdict(list)
    for market in valid:
        groups[market["event_ticker"]].append(market)

    contracts = []
    for market in valid:
        group = groups[market["event_ticker"]]
        status, event_id, selection = MatchStatus.UNMATCHED, None, None
        is_game = _GAME_RULE.match(market.get("rules_primary", ""))
        if not is_game or market.get("market_type") != "binary":
            reason = "not a two-sided game-winner contract"
        elif len(group) != 2:
            reason = "game does not have exactly two outcome contracts"
        else:
            found = _candidates(group, events)
            if len(found) == 1:
                status = MatchStatus.MATCHED
                reason = "teams and game date agree with a sportsbook event"
                event_id = canonical_event_id(found[0])
                label = market.get("yes_sub_title", "")
                selection = next(
                    t for t in (found[0].home_team, found[0].away_team) if _label_matches(label, t)
                )
            elif found:
                status, reason = MatchStatus.AMBIGUOUS, "more than one sportsbook event fits"
            else:
                reason = "no sportsbook event with the same teams and date"
        bid, ask = _prob(market.get("yes_bid_dollars")), _prob(market.get("yes_ask_dollars"))
        last_price = _prob(market.get("last_price_dollars"))
        midpoint = (
            round((bid + ask) / 2, 4)
            if bid is not None and ask is not None and ask >= bid
            else None
        )
        rules_primary = str(market.get("rules_primary") or "")
        rules_secondary = str(market.get("rules_secondary") or "")
        contracts.append(
            PredictionContract(
                venue="kalshi",
                contract_id=market["ticker"],
                source_event_id=market["event_ticker"],
                title=market.get("title", market["ticker"]),
                outcome=market.get("yes_sub_title", ""),
                url=(
                    f"https://kalshi.com/markets/{SERIES[sport].lower()}/"
                    f"{_MARKET_SLUG[sport]}/{market['event_ticker'].lower()}"
                ),
                rules="\n\n".join(
                    filter(None, (rules_primary, rules_secondary))
                ),
                rules_primary=rules_primary,
                rules_secondary=rules_secondary,
                probability=last_price,
                last_price=last_price,
                best_bid=bid,
                best_ask=ask,
                midpoint=midpoint,
                spread=round(ask - bid, 4) if bid is not None and ask is not None else None,
                bid_size=_num(market.get("yes_bid_size_fp")),
                ask_size=_num(market.get("yes_ask_size_fp")),
                volume=_num(market.get("volume_fp")),
                open_interest=_num(market.get("open_interest_fp")),
                liquidity=_num(market.get("liquidity_dollars")),
                status=str(market.get("status", "unknown")),
                close_time=_time(market.get("close_time")),
                metadata_updated_at=_time(market.get("updated_time")),
                observed_at=observed_at,
                match_status=status,
                match_reason=reason,
                event_id=event_id,
                matched_selection=selection,
            )
        )
    return contracts


class KalshiFixtureClient:
    """Serves committed payloads so dev and tests never contact the live venue."""

    async def fetch_markets(self, sport: Sport) -> list:
        return json.loads((_FIXTURES / f"kalshi_{sport.value}.json").read_text())["markets"]


class KalshiProvider:
    """Caches raw markets per sport (TTL + stale fallback); matching is redone per request."""

    name = "kalshi"

    def __init__(
        self,
        client: Union[KalshiClient, KalshiFixtureClient],
        ttl_seconds: int = 300,
        retry_after_seconds: int = 60,
    ):
        self.client = client
        self.ttl = timedelta(seconds=ttl_seconds)
        self._cache: dict = {}  # sport -> (fetched_at, markets)
        self._lock = asyncio.Lock()
        self.retry_after = timedelta(seconds=retry_after_seconds)
        self._retry_at: dict = {}  # sport -> no upstream call before this time
        self._errors: dict = {}  # sport -> last error_type
        self._cache_hits: dict = {}
        self._counts: dict = {}
        self._last_contracts: dict = {}  # sport -> (observed_at, fully normalized contracts)
        self._metrics: dict = defaultdict(
            lambda: {
                "request_count": 0,
                "success_count": 0,
                "error_count": 0,
                "last_duration_ms": None,
            }
        )

    def last_error_type(self, sport: Sport) -> Optional[str]:
        return self._errors.get(sport)

    def status(self, sport: Sport) -> str:
        if self._errors.get(sport):
            return "stale" if sport in self._cache else "error"
        return "configured"

    def last_refreshed_at(self, sport: Sport) -> Optional[datetime]:
        cached = self._cache.get(sport)
        return cached[0] if cached else None

    def cache_hit_for(self, sport: Sport) -> bool:
        return bool(self._cache_hits.get(sport, False))

    def cache_age_seconds(self, sport: Sport) -> Optional[float]:
        refreshed = self.last_refreshed_at(sport)
        if refreshed is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - refreshed).total_seconds())

    def contract_counts(self, sport: Sport) -> dict[str, int]:
        return dict(
            self._counts.get(
                sport,
                {"total": 0, "matched": 0, "unmatched": 0, "ambiguous": 0, "rejected": 0},
            )
        )

    def metrics(self, sport: Sport) -> dict:
        return {
            **dict(self._metrics[sport]),
            "cache_age_seconds": self.cache_age_seconds(sport),
            "cache_hit": self.cache_hit_for(sport),
            "contract_counts": self.contract_counts(sport),
            "status": self.status(sport),
            "last_refreshed_at": self.last_refreshed_at(sport),
            "last_error_type": self.last_error_type(sport),
        }

    async def _markets(
        self, sport: Sport, events: Sequence[Event]
    ) -> tuple[list, datetime]:
        async with self._lock:  # single-flight: concurrent requests share one upstream call
            cached = self._cache.get(sport)
            now = datetime.now(timezone.utc)
            self._cache_hits[sport] = bool(cached and now - cached[0] < self.ttl)
            if self._cache_hits[sport]:
                return cached[1], cached[0]
            if self._errors.get(sport) and now < self._retry_at.get(sport, now):
                # Backing off after a failure: don't hammer upstream while it is down.
                if cached:
                    return cached[1], cached[0]
                raise OddsProviderError(f"Kalshi unavailable ({self._errors[sport]})")
            started = time.perf_counter()
            try:
                self._metrics[sport]["request_count"] += 1
                markets = await self.client.fetch_markets(sport)
                # Validate the complete upstream snapshot before marking the refresh successful
                # or replacing the last known good cache. Matching is repeated below because the
                # sportsbook event set can change independently of the Kalshi TTL.
                try:
                    normalize_markets({"markets": markets}, sport, events, now)
                except ProviderPayloadError:
                    raise
                except Exception as error:
                    raise ProviderPayloadError(
                        "Kalshi markets could not be normalized"
                    ) from error
            except Exception as error:  # any failure falls back to stale data, like the odds cache
                self._metrics[sport]["error_count"] += 1
                self._metrics[sport]["last_duration_ms"] = round(
                    (time.perf_counter() - started) * 1000, 2
                )
                self._errors[sport] = getattr(error, "error_type", type(error).__name__)
                self._retry_at[sport] = now + self.retry_after
                if cached:
                    return cached[1], cached[0]
                raise
            self._errors.pop(sport, None)
            self._metrics[sport]["success_count"] += 1
            self._metrics[sport]["last_duration_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
            self._cache[sport] = (now, markets)
            return markets, now

    async def get_contracts(self, sport: Sport, events: Sequence[Event]) -> list:
        markets, observed_at = await self._markets(sport, events)
        try:
            contracts = normalize_markets(
                {"markets": markets}, sport, events, observed_at
            )
        except Exception as error:
            typed_error = (
                error
                if isinstance(error, ProviderPayloadError)
                else ProviderPayloadError("Kalshi markets could not be normalized")
            )
            self._errors[sport] = typed_error.error_type
            self._retry_at[sport] = datetime.now(timezone.utc) + self.retry_after
            previous = self._last_contracts.get(sport)
            if previous:
                return previous[1]
            if typed_error is error:
                raise
            raise typed_error from error
        self._last_contracts[sport] = (observed_at, contracts)
        counts = {
            "total": len(contracts),
            "matched": sum(c.match_status is MatchStatus.MATCHED for c in contracts),
            "unmatched": sum(c.match_status is MatchStatus.UNMATCHED for c in contracts),
            "ambiguous": sum(c.match_status is MatchStatus.AMBIGUOUS for c in contracts),
            "rejected": max(0, len(markets) - len(contracts)),
        }
        self._counts[sport] = counts
        return contracts
