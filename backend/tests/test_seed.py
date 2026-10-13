import asyncio
from collections import Counter

from app.identity import normalize_name
from app.ingest.seed import NBA_SEED, NFL_SEED, load_seed_rows, seed_teams
from app.storage.models import Team, TeamAlias, Venue
from sqlalchemy import func, select

from tests.matchup_helpers import new_session_factory


def test_seed_file_is_complete() -> None:
    rows = load_seed_rows(NFL_SEED)
    assert len(rows) == 32
    assert len({r["abbr"] for r in rows}) == 32
    assert Counter(r["conference"] for r in rows) == {d: 4 for d in {r["conference"] for r in rows}}
    assert len({r["conference"] for r in rows}) == 8
    for row in rows:
        assert row["roof"] in {"outdoors", "dome", "retractable"}, row["abbr"]
        assert -90 <= float(row["lat"]) <= 90 and -180 <= float(row["lon"]) <= 180
        assert row["tz"].startswith("America/")


def test_seed_is_idempotent_and_resolves_aliases(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        assert await seed_teams(factory) == 32
        assert await seed_teams(factory) == 32
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Team)) == 32
            # SoFi and MetLife are shared, so 30 venues for 32 teams.
            assert await session.scalar(select(func.count()).select_from(Venue)) == 30
            alias_rows = (await session.execute(
                select(TeamAlias.alias, Team.abbr).join(Team, Team.id == TeamAlias.team_id)
            )).all()
        aliases = dict(alias_rows)
        for name, abbr in [
            ("WAS", "WSH"), ("Washington Commanders", "WSH"), ("St. Louis Rams", "LAR"),
            ("LA", "LAR"), ("SD", "LAC"), ("OAK", "LV"), ("Los Angeles Chargers", "LAC"),
            ("DEN", "DEN"),
        ]:
            assert aliases[normalize_name(name)] == abbr, name

    asyncio.run(scenario())


def test_nba_seed_file_is_complete_and_shares_no_venue_with_the_nfl() -> None:
    rows = load_seed_rows(NBA_SEED)
    assert len(rows) == 30 and len({r["abbr"] for r in rows}) == 30
    assert Counter(r["conference"].split()[0] for r in rows) == {"East": 15, "West": 15}
    assert Counter(r["conference"] for r in rows) == {d: 5 for d in {r["conference"] for r in rows}}
    for row in rows:
        assert row["roof"] == "dome", row["abbr"]
        assert -90 <= float(row["lat"]) <= 90 and -180 <= float(row["lon"]) <= 180
        assert row["tz"].startswith("America/")
    nfl_venues = {r["venue_espn_id"] for r in load_seed_rows(NFL_SEED)}
    shared = {r["venue_espn_id"] for r in rows} & nfl_venues
    assert not shared, f"ESPN venue ids used by both leagues would merge their venues: {shared}"


def test_nba_teams_resolve_from_every_feed_s_names(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        assert await seed_teams(factory, "nba", NBA_SEED) == 30
        assert await seed_teams(factory, "nba", NBA_SEED) == 30  # idempotent
        async with factory() as session:
            teams = await session.scalar(
                select(func.count()).select_from(Team).where(Team.sport == "nba"))
            venues = await session.scalar(select(func.count()).select_from(Venue))
            alias_rows = (await session.execute(
                select(TeamAlias.alias, Team.abbr).join(Team, Team.id == TeamAlias.team_id)
                .where(Team.sport == "nba")
            )).all()
        assert teams == 30
        assert venues == 30  # the Clippers moved to the Intuit Dome, so no team shares a venue
        aliases = dict(alias_rows)
        for name, abbr in [
            ("GS", "GSW"), ("GSW", "GSW"), ("NO", "NOP"), ("NY", "NYK"), ("SA", "SAS"),
            ("UTAH", "UTA"), ("WSH", "WAS"), ("Los Angeles Clippers", "LAC"),
            ("LA Clippers", "LAC"), ("Los Angeles Lakers", "LAL"), ("Philadelphia 76ers", "PHI"),
            ("Portland Trail Blazers", "POR"), ("Brooklyn Nets", "BKN"),
        ]:
            assert aliases[normalize_name(name)] == abbr, name

    asyncio.run(scenario())
