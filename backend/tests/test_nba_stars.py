import asyncio
from datetime import datetime, timedelta, timezone

from app.ingest.seed import SEEDS, seed_teams
from app.storage.context_repository import ContextRepository
from app.storage.models import Game, NbaPlayerGameStat, StarOverride, Team
from sqlalchemy import select

from tests.matchup_helpers import new_session_factory

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _seeded_factory_and_team_ids(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    async with factory() as session:
        teams = (
            await session.scalars(
                select(Team).where(Team.sport == "nba", Team.abbr.in_(("ATL", "BOS", "MIA")))
            )
        ).all()
    return factory, {team.abbr: team.id for team in teams}


def _game(index: int, home_id: int, away_id: int) -> Game:
    tip = BASE + timedelta(days=index)
    return Game(
        sport="nba",
        season=2026,
        season_type="REG",
        week=None,
        start_time=tip,
        home_team_id=home_id,
        away_team_id=away_id,
        neutral=False,
        status="final",
        home_score=100,
        away_score=90,
        espn_id=f"edge-{index}",
        updated_at=tip,
    )


def _player_fact(
    game: Game,
    team_id: int,
    provider_player_id: str,
    player: str,
    minutes: float,
    suffix: str,
) -> NbaPlayerGameStat:
    return NbaPlayerGameStat(
        game_id=game.id,
        team_id=team_id,
        provider="espn",
        provider_game_id=f"edge-{suffix}",
        provider_team_id=str(team_id),
        provider_player_id=provider_player_id,
        player=player,
        minutes=minutes,
        fingerprint=f"edge-{suffix}-{provider_player_id}-{minutes}",
        stats={"MIN": minutes},
        observed_at=game.start_time + timedelta(hours=3),
    )


def test_star_picker_uses_prior_season_until_five_games_and_honors_cutoffs_and_override(
    tmp_path,
) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        async with factory() as session, session.begin():
            bos = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "BOS"))
            mia = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "MIA"))
            games = []
            for index, season in enumerate([2025, 2026, 2026, 2026, 2026, 2026]):
                game = Game(
                    sport="nba",
                    season=season,
                    season_type="REG",
                    week=None,
                    start_time=BASE + timedelta(days=index),
                    home_team_id=bos.id,
                    away_team_id=mia.id,
                    neutral=False,
                    status="final",
                    home_score=100,
                    away_score=90,
                    espn_id=f"star-{index}",
                    updated_at=BASE + timedelta(days=index),
                )
                session.add(game)
                await session.flush()
                player = "Prior Star" if season == 2025 else "Current Star"
                minutes = 40.0 if season == 2025 else 30.0 + index
                session.add(
                    NbaPlayerGameStat(
                        game_id=game.id,
                        team_id=bos.id,
                        provider="espn",
                        provider_game_id=f"p-{index}",
                        provider_team_id="2",
                        provider_player_id="old" if season == 2025 else "new",
                        player=player,
                        minutes=minutes,
                        fingerprint=f"f-{index}",
                        stats={"MIN": minutes},
                        observed_at=game.start_time + timedelta(hours=3),
                    )
                )
                games.append(game)
            preseason = Game(
                sport="nba",
                season=2026,
                season_type="PRE",
                week=None,
                start_time=BASE + timedelta(hours=12),
                home_team_id=bos.id,
                away_team_id=mia.id,
                neutral=False,
                status="final",
                home_score=100,
                away_score=90,
                espn_id="preseason-star",
                updated_at=BASE + timedelta(hours=12),
            )
            session.add(preseason)
            await session.flush()
            session.add(
                NbaPlayerGameStat(
                    game_id=preseason.id,
                    team_id=bos.id,
                    provider="espn",
                    provider_game_id="preseason-star",
                    provider_team_id="2",
                    provider_player_id="preseason",
                    player="Preseason Star",
                    minutes=48,
                    fingerprint="preseason-star",
                    stats={"MIN": 48},
                    observed_at=BASE + timedelta(hours=15),
                )
            )
        repo = ContextRepository(factory)
        before_fifth = BASE + timedelta(days=5)
        stars = await repo.star_players(bos.id, 2026, before_fifth)
        assert [star.player for star in stars] == ["Prior Star"]
        after_fifth = BASE + timedelta(days=7)
        stars = await repo.star_players(bos.id, 2026, after_fifth)
        assert [star.player for star in stars] == ["Current Star"]
        async with factory() as session, session.begin():
            session.add(
                StarOverride(
                    team_id=bos.id,
                    player="Manual Star",
                    starts_at=after_fifth,
                    ends_at=after_fifth + timedelta(days=2),
                    note="trade",
                )
            )
        stars = await repo.star_players(bos.id, 2026, after_fifth + timedelta(hours=1))
        assert [(star.player, star.source) for star in stars] == [("Manual Star", "manual")]
        assert [
            star.player
            for star in await repo.star_players(bos.id, 2026, after_fifth + timedelta(days=3))
        ] == ["Current Star"]

    asyncio.run(scenario())


def test_star_picker_uses_early_current_season_when_no_prior_baseline_is_loaded(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        for sport, path in SEEDS.items():
            await seed_teams(factory, sport, path)
        async with factory() as session, session.begin():
            bos = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "BOS"))
            mia = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "MIA"))
            game = Game(
                sport="nba",
                season=2026,
                season_type="REG",
                week=None,
                start_time=BASE,
                home_team_id=bos.id,
                away_team_id=mia.id,
                neutral=False,
                status="final",
                home_score=100,
                away_score=90,
                espn_id="current-only",
                updated_at=BASE,
            )
            session.add(game)
            await session.flush()
            session.add(
                NbaPlayerGameStat(
                    game_id=game.id,
                    team_id=bos.id,
                    provider="espn",
                    provider_game_id="current-only",
                    provider_team_id="2",
                    provider_player_id="new",
                    player="Current Star",
                    minutes=38,
                    fingerprint="current-only",
                    stats={"MIN": 38},
                    observed_at=BASE + timedelta(hours=3),
                )
            )
        stars = await ContextRepository(factory).star_players(
            bos.id, 2026, BASE + timedelta(days=1)
        )
        assert [star.player for star in stars] == ["Current Star"]

    asyncio.run(scenario())


def test_star_picker_breaks_equal_minutes_ties_by_player_name(tmp_path) -> None:
    async def scenario() -> None:
        factory, teams = await _seeded_factory_and_team_ids(tmp_path)
        async with factory() as session, session.begin():
            game = _game(1, teams["BOS"], teams["MIA"])
            session.add(game)
            await session.flush()
            for index, name in enumerate(("Charlie", "Alpha", "Bravo", "Delta")):
                session.add(
                    _player_fact(
                        game,
                        teams["BOS"],
                        str(index),
                        name,
                        32 if name != "Delta" else 31,
                        f"tie-{index}",
                    )
                )
        stars = await ContextRepository(factory).star_players(
            teams["BOS"], 2026, BASE + timedelta(days=2)
        )
        assert [(star.player, star.minutes_per_game) for star in stars] == [
            ("Alpha", 32.0),
            ("Bravo", 32.0),
            ("Charlie", 32.0),
        ]

    asyncio.run(scenario())


def test_star_picker_keeps_a_traded_players_team_samples_separate(tmp_path) -> None:
    async def scenario() -> None:
        factory, teams = await _seeded_factory_and_team_ids(tmp_path)
        async with factory() as session, session.begin():
            old_game = _game(1, teams["BOS"], teams["ATL"])
            new_game = _game(2, teams["MIA"], teams["ATL"])
            session.add_all((old_game, new_game))
            await session.flush()
            session.add_all(
                (
                    _player_fact(
                        old_game, teams["BOS"], "traded", "Traded Player", 40, "trade-old"
                    ),
                    _player_fact(
                        new_game, teams["MIA"], "traded", "Traded Player", 20, "trade-new"
                    ),
                )
            )
        repo = ContextRepository(factory)
        cutoff = BASE + timedelta(days=3)
        bos = await repo.star_players(teams["BOS"], 2026, cutoff)
        mia = await repo.star_players(teams["MIA"], 2026, cutoff)
        assert [(star.player, star.minutes_per_game) for star in bos] == [("Traded Player", 40.0)]
        assert [(star.player, star.minutes_per_game) for star in mia] == [("Traded Player", 20.0)]

    asyncio.run(scenario())


def test_star_picker_excludes_dnp_rows_from_rank_and_minutes_average(tmp_path) -> None:
    async def scenario() -> None:
        factory, teams = await _seeded_factory_and_team_ids(tmp_path)
        async with factory() as session, session.begin():
            first = _game(1, teams["BOS"], teams["MIA"])
            second = _game(2, teams["BOS"], teams["ATL"])
            session.add_all((first, second))
            await session.flush()
            session.add_all(
                (
                    _player_fact(first, teams["BOS"], "rotation", "Rotation Player", 0, "dnp"),
                    _player_fact(second, teams["BOS"], "rotation", "Rotation Player", 20, "played"),
                    _player_fact(first, teams["BOS"], "dnp-only", "DNP Only", 0, "dnp-only"),
                )
            )
        stars = await ContextRepository(factory).star_players(
            teams["BOS"], 2026, BASE + timedelta(days=3)
        )
        assert [(star.player, star.minutes_per_game) for star in stars] == [
            ("Rotation Player", 20.0)
        ]

    asyncio.run(scenario())
