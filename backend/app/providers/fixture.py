from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Optional

from app.domain.models import Event, EventComparison, MarketType, Offer, Sport


class FixtureOddsProvider:
    name = "fixture"
    status = "configured"

    async def get_comparisons(
        self,
        sport: Optional[Sport] = None,
        event_id: Optional[str] = None,
        market_type: Optional[MarketType] = None,
        force_refresh: bool = False,
        include_player_props: bool = False,
    ) -> Sequence[EventComparison]:
        requested_sport = sport or Sport.NFL
        observed_at = datetime.now(timezone.utc)
        event_id = f"{self.name}:{requested_sport.value}:sample"
        if requested_sport is Sport.NFL:
            home_team, away_team = "San Francisco 49ers", "Washington Commanders"
            start_time = datetime(2026, 10, 20, 3, 15, tzinfo=timezone.utc)
        else:
            home_team, away_team = "Sacramento Kings", "Portland Trail Blazers"
            start_time = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)
        event = Event(
            id=event_id,
            sport=requested_sport,
            league="NFL" if requested_sport is Sport.NFL else "NBA",
            home_team=home_team,
            away_team=away_team,
            start_time=start_time,
        )
        offers = [
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.MONEYLINE,
                selection=event.home_team,
                price_american=-120,
                price_decimal=1.8333,
                observed_at=observed_at,
                deep_link="https://sportsbook.draftkings.com/",
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.SPREAD,
                selection=event.home_team,
                line=-2.5,
                price_american=-110,
                price_decimal=1.9091,
                observed_at=observed_at,
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.ALTERNATE_SPREAD,
                selection=event.home_team,
                line=-1.5,
                price_american=-155,
                price_decimal=1.6452,
                observed_at=observed_at,
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.ALTERNATE_SPREAD,
                selection=event.away_team,
                line=1.5,
                price_american=135,
                price_decimal=2.35,
                observed_at=observed_at,
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.TOTAL,
                selection="Over",
                line=44.5,
                price_american=-110,
                price_decimal=1.9091,
                observed_at=observed_at,
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=MarketType.TOTAL,
                selection="Under",
                line=44.5,
                price_american=-110,
                price_decimal=1.9091,
                observed_at=observed_at,
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="FanDuel",
                market_type=MarketType.MONEYLINE,
                selection=event.home_team,
                price_american=-115,
                price_decimal=1.8696,
                observed_at=observed_at,
                deep_link="https://sportsbook.fanduel.com/",
            ),
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="FanDuel",
                market_type=MarketType.MONEYLINE,
                selection=event.away_team,
                price_american=105,
                price_decimal=2.05,
                observed_at=observed_at,
                deep_link="https://sportsbook.fanduel.com/",
            ),
        ]
        if include_player_props:
            offers.extend(self._player_prop_offers(requested_sport, event_id, observed_at))
        return [EventComparison(event=event, offers=offers)]

    def _player_prop_offers(
        self, sport: Sport, event_id: str, observed_at: datetime
    ) -> list[Offer]:
        if sport is Sport.NBA:
            props: list[tuple[MarketType, str, Optional[float], int]] = [
                (MarketType.PLAYER_POINTS, "Star Guard", 24.5, -115),
                (MarketType.PLAYER_REBOUNDS, "Star Guard", 5.5, -110),
                (MarketType.PLAYER_ASSISTS, "Star Guard", 7.5, -120),
                (MarketType.PLAYER_THREES, "Star Guard", 2.5, +100),
                (MarketType.PLAYER_POINTS_REBOUNDS_ASSISTS, "Star Guard", 37.5, -110),
            ]
        else:
            props = [
                (MarketType.PLAYER_PASSING_YARDS, "Starting QB", 245.5, -115),
                (MarketType.PLAYER_RUSHING_YARDS, "Starting QB", 22.5, -110),
                (MarketType.PLAYER_PASSING_RUSHING_YARDS, "Starting QB", 268.5, -110),
                (MarketType.PLAYER_RECEIVING_YARDS, "Lead Receiver", 68.5, -115),
                (MarketType.PLAYER_ANYTIME_TOUCHDOWN, "Lead Receiver", None, +150),
            ]
        return [
            Offer(
                event_id=event_id,
                provider=self.name,
                bookmaker="DraftKings",
                market_type=market_type,
                selection=player_name,
                player_name=player_name,
                line=line,
                price_american=price,
                price_decimal=(1 + price / 100) if price >= 100 else (1 + 100 / abs(price)),
                observed_at=observed_at,
            )
            for market_type, player_name, line, price in props
        ]
