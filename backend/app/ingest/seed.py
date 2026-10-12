"""Seed teams, home venues, and name aliases from a committed CSV (idempotent)."""

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity import normalize_name
from app.storage.models import Team, TeamAlias, Venue

NFL_SEED = Path(__file__).parent / "data" / "nfl_teams.csv"


def load_seed_rows(path: Path = NFL_SEED) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


async def seed_teams(
    session_factory: async_sessionmaker[AsyncSession], sport: str = "nfl", path: Path = NFL_SEED
) -> int:
    rows = load_seed_rows(path)
    async with session_factory() as session, session.begin():
        venues = {v.espn_id: v for v in (await session.scalars(select(Venue))).all()}
        teams = {
            t.abbr: t
            for t in (await session.scalars(select(Team).where(Team.sport == sport))).all()
        }
        known = set(
            (await session.scalars(select(TeamAlias.alias).where(TeamAlias.sport == sport))).all()
        )
        for row in rows:
            venue = venues.get(row["venue_espn_id"])
            if venue is None:
                venue = Venue(espn_id=row["venue_espn_id"], name=row["venue_name"])
                session.add(venue)
                venues[row["venue_espn_id"]] = venue
            venue.name = row["venue_name"]
            venue.lat = float(row["lat"])
            venue.lon = float(row["lon"])
            venue.tz = row["tz"]
            venue.roof = row["roof"]

            team = teams.get(row["abbr"])
            if team is None:
                team = Team(sport=sport, abbr=row["abbr"], name=row["name"])
                session.add(team)
                teams[row["abbr"]] = team
            team.name = row["name"]
            team.espn_id = row["espn_id"]
            team.conference = row["conference"]
            await session.flush()
            team.venue_id = venue.id

            extra = [alias for alias in row["aliases"].split("|") if alias]
            for alias in (row["abbr"], row["name"], *extra):
                key = normalize_name(alias)
                if key not in known:
                    session.add(TeamAlias(team_id=team.id, sport=sport, alias=key, source="seed"))
                    known.add(key)
    return len(rows)
