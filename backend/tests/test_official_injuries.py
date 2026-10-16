import asyncio
from datetime import datetime, timedelta, timezone

from app.ingest.espn import parse_scoreboard
from app.ingest.records import InjuryRecord, NamedInjury
from app.ingest.seed import SEEDS, seed_teams
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import Injury, Team
from sqlalchemy import select

from tests.matchup_helpers import load_json, new_session_factory

T1 = datetime(2026, 1, 15, 15, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=2)
T3 = T1 + timedelta(hours=4)
WAS, MIA, HOU = "27", "14", "10"  # ESPN team ids in nba_teams.csv


def espn(team_espn_id, player, status, position=None, detail=None):
    return InjuryRecord(team_espn_id, player, position, status, detail, None)


def official(team, player, status, detail=None, position=None):
    return NamedInjury(team, player, position, status, detail)


async def _setup(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    async with factory() as session:
        rows = await session.execute(select(Team.sport, Team.abbr, Team.id))
        ids = {(sport, abbr): team_id for sport, abbr, team_id in rows.all()}
    return ContextRepository(factory), ids, factory


def test_an_official_report_names_its_teams_and_is_stored_as_its_own_snapshot(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, factory = await _setup(tmp_path)
        young = official("Washington Wizards", "Trae Young", "Out", "Right Knee; MCL Sprain")
        stray = official("Atlantis Dolphins", "Nobody", "Out")
        result = await repo.save_official_injuries("nba", "nba_official", [young, stray], T1)
        assert (result.saved, result.unknown_teams) == (1, ["Atlantis Dolphins"])
        dotson = official("LA", "Kevin Dotson", "Out", "Ankle", position="G")  # nflverse's code
        assert (await repo.save_official_injuries("nfl", "nflverse", [dotson], T1)).saved == 1
        async with factory() as session:
            rows = (await session.scalars(select(Injury).order_by(Injury.source))).all()
        assert [
            (r.source, r.team_id, r.player, r.position, r.observed_at.replace(tzinfo=timezone.utc))
            for r in rows
        ] == [
            ("nba_official", ids[("nba", "WAS")], "Trae Young", None, T1),
            ("nflverse", ids[("nfl", "LAR")], "Kevin Dotson", "G", T1),
        ]

    asyncio.run(scenario())


def test_the_official_status_wins_and_espn_fills_the_gaps(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, _ = await _setup(tmp_path)
        await repo.save_injuries("nba", [
            espn(WAS, "Trae Young", "Day-To-Day", "PG", "Knee"),
            espn(WAS, "Alex Sarr", "Out", "C", "Hamstring"),
            espn(MIA, "Tyler Herro", "Out", "SG", "Toe"),
        ], T1)
        await repo.save_official_injuries("nba", "nba_official", [
            official("Washington Wizards", "Trae Young", "Out", "Right Knee; MCL Sprain"),
        ], T2)
        was, mia = ids[("nba", "WAS")], ids[("nba", "MIA")]
        by_team, observed = await repo.injury_snapshot("nba", {was, mia}, T3)
        rows = {row.player: row for row in by_team[was]}
        young = rows["Trae Young"]
        assert (young.status, young.detail, young.source) == (
            "Out", "Right Knee; MCL Sprain", "nba_official")
        assert young.position == "PG"  # the official report has none, so ESPN's is kept
        assert rows["Alex Sarr"].source == "espn"  # the official report does not list him
        assert [row.player for row in by_team[mia]] == ["Tyler Herro"]
        assert observed == T2

    asyncio.run(scenario())


def test_a_newer_espn_snapshot_does_not_hide_the_official_report(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, _ = await _setup(tmp_path)
        await repo.save_official_injuries("nba", "nba_official", [
            official("Washington Wizards", "Trae Young", "Out", "Right Knee; MCL Sprain"),
        ], T1)
        await repo.save_injuries("nba", [
            espn(WAS, "Trae Young", "Day-To-Day", "PG"), espn(WAS, "Alex Sarr", "Out", "C"),
        ], T2)
        was = ids[("nba", "WAS")]
        by_team, observed = await repo.injury_snapshot("nba", {was}, T3)
        rows = {row.player: row for row in by_team[was]}
        assert (rows["Trae Young"].status, rows["Trae Young"].source) == ("Out", "nba_official")
        assert rows["Alex Sarr"].source == "espn"
        assert observed == T2

    asyncio.run(scenario())


def test_the_cutoff_ignores_reports_after_it_whatever_their_source(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, _ = await _setup(tmp_path)
        await repo.save_injuries("nba", [espn(WAS, "Alex Sarr", "Out", "C")], T1)
        await repo.save_official_injuries("nba", "nba_official", [
            official("Washington Wizards", "Trae Young", "Out"),
        ], T2)
        was = ids[("nba", "WAS")]
        by_team, observed = await repo.injury_snapshot("nba", {was}, T1 + timedelta(hours=1))
        assert [row.player for row in by_team[was]] == ["Alex Sarr"] and observed == T1
        none_yet, observed = await repo.injury_snapshot("nba", {was}, T1 - timedelta(days=1))
        assert none_yet == {was: []} and observed is None

    asyncio.run(scenario())


def test_an_old_official_report_no_longer_beats_espn(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, _ = await _setup(tmp_path)
        # the league's last report of the old season is still the newest official snapshot
        await repo.save_official_injuries("nba", "nba_official", [
            official("Washington Wizards", "Trae Young", "Out", "Right Knee; MCL Sprain"),
            official("Washington Wizards", "Alex Sarr", "Out", "Hamstring"),
        ], T1)
        later = T1 + timedelta(days=120)
        await repo.save_injuries("nba", [espn(WAS, "Trae Young", "Day-To-Day", "PG")], later)
        was = ids[("nba", "WAS")]
        by_team, observed = await repo.injury_snapshot("nba", {was}, later + timedelta(hours=1))
        [row] = by_team[was]  # Sarr's four-month-old "Out" is gone, and so is Young's
        assert (row.player, row.status, row.source) == ("Trae Young", "Day-To-Day", "espn")
        assert observed == later

    asyncio.run(scenario())


def test_a_suffix_does_not_split_one_player_in_two(tmp_path) -> None:
    async def scenario() -> None:
        repo, ids, _ = await _setup(tmp_path)
        await repo.save_injuries("nba", [espn(HOU, "Jabari Smith", "Day-To-Day", "PF")], T1)
        await repo.save_official_injuries("nba", "nba_official", [
            official("Houston Rockets", "Jabari Smith Jr.", "Out", "Right Ankle; Sprain"),
        ], T2)
        hou = ids[("nba", "HOU")]
        by_team, _ = await repo.injury_snapshot("nba", {hou}, T3)
        [row] = by_team[hou]
        assert (row.player, row.status, row.position) == ("Jabari Smith Jr.", "Out", "PF")

    asyncio.run(scenario())


def test_the_upcoming_week_is_the_first_regular_season_week_not_yet_over(tmp_path) -> None:
    async def scenario() -> None:
        repo, _, factory = await _setup(tmp_path)
        now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        records = parse_scoreboard(load_json("espn/scoreboard_week6.json"))
        await GamesRepository(factory).upsert_games("nfl", "espn", records, now)
        assert await repo.upcoming_week("nfl", now) == (2026, 6)
        assert await repo.upcoming_week("nfl", datetime(2027, 1, 1, tzinfo=timezone.utc)) is None
        assert await repo.upcoming_week("nba", now) is None

    asyncio.run(scenario())
