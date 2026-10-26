"""Immutable daily forecast intake and lookup."""

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.domain.forecasts import Predictions
from app.storage.games_repository import aware
from app.storage.models import Game, NbaForecast, Team


class ForecastConflict(Exception):
    pass


EASTERN = ZoneInfo("America/New_York")


async def slate_matchups(
    factory: async_sessionmaker[AsyncSession], day: date
) -> dict[tuple[str, str], datetime]:
    """Scheduled regular-season NBA matchups on an Eastern slate date."""
    start = datetime.combine(day, time(0), EASTERN).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), EASTERN).astimezone(timezone.utc)
    home, away = aliased(Team), aliased(Team)
    async with factory() as session:
        rows = await session.execute(
            select(Game, home.abbr, away.abbr)
            .join(home, Game.home_team_id == home.id)
            .join(away, Game.away_team_id == away.id)
            .where(Game.sport == "nba", Game.season_type == "REG",
                   Game.status == "scheduled", Game.start_time >= start,
                   Game.start_time < end)
        )
        return {(h, a): aware(game.start_time) for game, h, a in rows.all()}


def validate_forecast_for_slate(
    preds: Predictions, matchups: dict[tuple[str, str], datetime], now: datetime
) -> None:
    """Reject stale, future, partial, or post-tip artifacts before storing/pricing."""
    actual = {(game.home, game.away) for game in preds.games}
    if not matchups or actual != set(matchups):
        raise ValueError("forecast games do not match the loaded scheduled NBA slate")
    generated = preds.generated_at.astimezone(timezone.utc)
    if generated.astimezone(EASTERN).date() != preds.slate_date:
        raise ValueError("forecast was not generated on its Eastern slate date")
    if generated > now + timedelta(minutes=5):
        raise ValueError("forecast generation time is in the future")
    if generated >= min(matchups.values()):
        raise ValueError("forecast was generated after the first tip")
    if now >= min(matchups.values()):
        raise ValueError("the first game has already tipped")
    gap = (preds.slate_date - preds.data_through).days
    # Match predict_today.py's stale-gap guard: a long break is plausible only
    # across the offseason, while an 11-99 day gap indicates missing in-season data.
    if 10 < gap < 100 or gap > 240:
        raise ValueError("forecast underlying data are too old")


def _canonical(preds: Predictions) -> tuple[dict, str]:
    payload = preds.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return payload, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def get_forecast(
    factory: async_sessionmaker[AsyncSession], day: date
) -> Optional[Predictions]:
    async with factory() as session:
        row = await session.get(NbaForecast, day)
        return Predictions.model_validate(row.payload) if row else None


async def store_forecast(
    factory: async_sessionmaker[AsyncSession], preds: Predictions
) -> bool:
    """Return True for a new slate, False for an identical retry; reject rewrites."""
    payload, digest = _canonical(preds)
    async with factory() as session:
        existing = await session.get(NbaForecast, preds.slate_date)
        if existing:
            if existing.payload_sha256 != digest:
                raise ForecastConflict("forecast for this slate already differs")
            return False
        session.add(NbaForecast(
            slate_date=preds.slate_date, schema_version=preds.schema_version,
            model_version=preds.model_version, generated_at=preds.generated_at,
            data_through=preds.data_through, received_at=datetime.now(timezone.utc),
            payload_sha256=digest, payload=payload,
        ))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
    # A concurrent request may have inserted the same slate while we committed.
    async with factory() as session:
        existing = await session.get(NbaForecast, preds.slate_date)
        if existing and existing.payload_sha256 == digest:
            return False
    raise ForecastConflict("forecast for this slate already differs")
