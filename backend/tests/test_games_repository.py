import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.ingest.espn import parse_pickcenter, parse_scoreboard
from app.ingest.records import GameRecord, LineRecord
from app.ingest.seed import seed_teams
from app.storage.games_repository import GamesRepository
from app.storage.models import EventRecord, Game, GameLine, Team, Venue
from sqlalchemy import select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


async def _seeded(tmp_path):
    factory = await new_session_factory(tmp_path)
    await seed_teams(factory)
    return factory, GamesRepository(factory)


def _records():
    return parse_scoreboard(load_json("espn/scoreboard_week6.json"))


async def _lines(factory, espn_id):
    async with factory() as session:
        rows = (await session.scalars(
            select(GameLine).join(Game, Game.id == GameLine.game_id).where(Game.espn_id == espn_id)
        )).all()
    return {(r.source, r.kind): r for r in rows}


def test_upsert_creates_games_using_seeded_home_venue(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        result = await repo.upsert_games("nfl", "espn", _records(), NOW)
        assert result.games == 4 and result.skipped == []
        async with factory() as session:
            game = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
            venue = await session.get(Venue, game.venue_id)
            home = await session.get(Team, game.home_team_id)
        assert home.abbr == "DEN"
        assert (venue.name, venue.lat is not None) == ("Empower Field at Mile High", True)
        assert set(await _lines(factory, "401872995")) == {("espn", "open"), ("espn", "current")}

    asyncio.run(scenario())


def test_opener_is_never_overwritten_but_current_is(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        await repo.upsert_games("nfl", "espn", _records(), NOW)
        game = next(r for r in _records() if r.espn_id == "401872995")
        moved = replace(game, lines=(
            LineRecord(kind="open", spread_home=9.5),
            LineRecord(kind="current", spread_home=-0.5, total=41.0),
        ))
        await repo.upsert_games("nfl", "espn", [moved], NOW + timedelta(hours=1))
        lines = await _lines(factory, "401872995")
        assert lines[("espn", "open")].spread_home == 2.5
        assert lines[("espn", "current")].spread_home == -0.5
        assert lines[("espn", "current")].total == 41.0

    asyncio.run(scenario())


def test_neutral_site_creates_venue_and_nflverse_cannot_wipe_it(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        await repo.upsert_games("nfl", "espn", _records(), NOW)
        london = next(r for r in _records() if r.espn_id == "401872981")
        history_view = replace(london, venue_espn_id=None, venue_name="Wembley", lines=(),
                               status="scheduled", home_score=None, away_score=None)
        await repo.upsert_games("nfl", "nflverse", [history_view], NOW)
        async with factory() as session:
            game = await session.scalar(select(Game).where(Game.espn_id == "401872981"))
            venue = await session.get(Venue, game.venue_id)
        assert venue.espn_id == "2227" and venue.name == "Wembley Stadium"
        assert venue.roof == "outdoors"
        # nflverse said "scheduled" but ESPN said in progress; a non-ESPN source cannot downgrade.
        assert game.status == "in_progress" and game.home_score == 7

    asyncio.run(scenario())


def test_nflverse_cannot_rewrite_espn_venue_or_teams_on_existing_game(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        den = next(r for r in _records() if r.espn_id == "401872995")
        relocated = replace(den, venue_espn_id="3970", venue_name="State Farm Stadium")
        await repo.upsert_games("nfl", "espn", [relocated], NOW)
        history_view = replace(den, venue_espn_id=None, home_name="SEA", away_name="DEN",
                               neutral=True, lines=())
        await repo.upsert_games("nfl", "nflverse", [history_view], NOW)
        async with factory() as session:
            game = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
            venue = await session.get(Venue, game.venue_id)
            home = await session.get(Team, game.home_team_id)
        assert venue.espn_id == "3970" and home.abbr == "DEN" and game.neutral is False

    asyncio.run(scenario())


def test_unknown_team_is_skipped_not_crashed(tmp_path) -> None:
    async def scenario() -> None:
        _, repo = await _seeded(tmp_path)
        pro_bowl = GameRecord(
            espn_id="999", season=2026, season_type="POST", week=4, start_time=NOW,
            status="scheduled", home_name="AFC", away_name="NFC", home_score=None,
            away_score=None, neutral=True,
        )
        result = await repo.upsert_games("nfl", "espn", [pro_bowl], NOW)
        assert (result.games, result.skipped) == (0, ["999"])

    asyncio.run(scenario())


def test_finals_missing_close_then_saved(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        await repo.upsert_games("nfl", "espn", _records(), NOW)
        since = datetime(2026, 9, 27, tzinfo=timezone.utc)
        assert await repo.finals_missing_close("nfl", since) == ["401872964"]
        lines = parse_pickcenter(load_json("espn/summary_final.json"))
        assert await repo.save_lines("401872964", "espn", lines, NOW) is True
        assert await repo.finals_missing_close("nfl", since) == []
        assert await repo.save_lines("nope", "espn", lines, NOW) is False
        assert (await _lines(factory, "401872964"))[("espn", "close")].ml_home == 124

    asyncio.run(scenario())


def test_link_events_matches_by_alias_and_date_including_swapped_sides(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        await repo.upsert_games("nfl", "espn", _records(), NOW)
        kickoff = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
        async with factory() as session, session.begin():
            session.add_all([
                EventRecord(id="event:den", sport="nfl", league="NFL",
                            home_team="Denver Broncos", away_team="Seattle Seahawks",
                            start_time=kickoff + timedelta(minutes=3),
                            first_observed_at=NOW, last_observed_at=NOW),
                EventRecord(id="event:london", sport="nfl", league="NFL",
                            home_team="Philadelphia Eagles", away_team="Jacksonville Jaguars",
                            start_time=datetime(2026, 10, 11, 13, 30, tzinfo=timezone.utc),
                            first_observed_at=NOW, last_observed_at=NOW),
                EventRecord(id="event:ghost", sport="nfl", league="NFL",
                            home_team="Springfield Atoms", away_team="Denver Broncos",
                            start_time=kickoff, first_observed_at=NOW, last_observed_at=NOW),
            ])
        result = await repo.link_events("nfl")
        assert result.linked == 2 and result.unlinked == ["event:ghost"]
        async with factory() as session:
            den = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
        assert den.event_id == "event:den"
        assert (await repo.link_events("nfl")).linked == 0  # already linked, nothing new

    asyncio.run(scenario())


def test_quarterbacks_are_stored_and_a_later_espn_upsert_does_not_blank_them(tmp_path) -> None:
    async def scenario() -> None:
        factory, repo = await _seeded(tmp_path)
        await repo.upsert_games("nfl", "espn", _records(), NOW)
        den = next(r for r in _records() if r.espn_id == "401872995")
        await repo.upsert_games(
            "nfl", "nflverse", [replace(den, home_qb="Bo Nix", away_qb="Sam Darnold")], NOW
        )
        await repo.upsert_games("nfl", "espn", _records(), NOW + timedelta(hours=1))
        async with factory() as session:
            game = await session.scalar(select(Game).where(Game.espn_id == "401872995"))
        assert (game.home_qb, game.away_qb) == ("Bo Nix", "Sam Darnold")

    asyncio.run(scenario())
