import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from app.config import load_settings
from app.ingest.espn import (
    EspnNbaBoxScoreError,
    parse_nba_box_score,
    parse_nba_completed_games,
    parse_scoreboard,
)
from app.ingest.seed import SEEDS, seed_teams
from app.jobs import nba as nba_jobs
from app.jobs.nba import NbaTeamStatsJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import GamesRepository
from app.storage.models import Game, NbaContextRefresh, NbaPlayerGameStat, NbaTeamGameStat, Team
from sqlalchemy import func, select

from tests.matchup_helpers import load_json, new_session_factory

NOW = datetime(2026, 1, 16, 15, tzinfo=timezone.utc)
SLOT = Slot("test", NOW, NOW)


def _box_score_records():
    games = parse_nba_completed_games(load_json("espn_nba/scoreboard_day.json"))
    game = next(row for row in games if row.provider_game_id == "401810433")
    teams, players = parse_nba_box_score(game, load_json("espn_nba/summary_boxscore.json"))
    return game, teams, players


def test_espn_parsers_normalize_completed_team_and_player_box_scores() -> None:
    game, teams, players = _box_score_records()
    assert (game.provider_game_id, game.home_team, game.away_team) == (
        "401810433",
        "ORL",
        "MEM",
    )
    assert game.game_date == date(2026, 1, 15)
    assert len(teams) == 2 and {row.team for row in teams} == {"ORL", "MEM"}
    orl = next(row for row in teams if row.team == "ORL")
    assert orl.stats["OFF_RATING"] > 0 and orl.stats["NET_RATING"] > 0
    assert orl.stats["MIN"] == 48 and orl.stats["PACE"] > 0
    paolo = next(row for row in players if row.provider_player_id == "201")
    assert (paolo.player, paolo.minutes) == ("Paolo Banchero", 36.5)
    assert next(row for row in players if row.provider_player_id == "203").minutes == 0


def test_overtime_game_length_comes_from_the_scoreboard_period() -> None:
    game, regulation, _ = _box_score_records()
    overtime, _ = parse_nba_box_score(
        replace(game, period=5), load_json("espn_nba/summary_boxscore.json")
    )
    assert overtime[0].stats["MIN"] == 53
    assert overtime[0].stats["PACE"] < regulation[0].stats["PACE"]


def test_payload_errors_are_not_treated_as_empty_success() -> None:
    game, _, _ = _box_score_records()
    try:
        parse_nba_box_score(game, {})
    except EspnNbaBoxScoreError as error:
        assert "team/player" in str(error)
    else:
        raise AssertionError("provider errors must fail parsing")


def test_completed_team_box_scores_reject_missing_required_inputs() -> None:
    game, _, _ = _box_score_records()
    payload = deepcopy(load_json("espn_nba/summary_boxscore.json"))
    payload["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = None
    try:
        parse_nba_box_score(game, payload)
    except EspnNbaBoxScoreError as error:
        assert "field goals" in str(error)
    else:
        raise AssertionError("a partial completed-game box score must not become zero-valued data")


def test_completed_game_parser_excludes_preseason_and_scheduled_games() -> None:
    games = parse_nba_completed_games(load_json("espn_nba/scoreboard_day.json"))
    assert [game.provider_game_id for game in games] == ["401810433", "401810434", "401810435"]

    payload = deepcopy(load_json("espn_nba/scoreboard_day.json"))
    preseason = deepcopy(payload["events"][0])
    preseason["season"]["type"] = 1
    assert parse_nba_completed_games({"events": [preseason]}) == []


def test_completed_game_parser_distinguishes_an_empty_day_from_bad_schema() -> None:
    assert parse_nba_completed_games({"events": []}) == []
    with pytest.raises(EspnNbaBoxScoreError, match="events list"):
        parse_nba_completed_games({})

    malformed = deepcopy(load_json("espn_nba/scoreboard_day.json"))["events"][0]
    del malformed["season"]["type"]
    with pytest.raises(EspnNbaBoxScoreError, match="season type"):
        parse_nba_completed_games({"events": [malformed]})


async def _seeded(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    await GamesRepository(factory).upsert_games(
        "nba", "espn", parse_scoreboard(load_json("espn_nba/scoreboard_day.json")), NOW
    )
    return factory


async def _seeded_single_game(tmp_path):
    factory = await new_session_factory(tmp_path)
    for sport, path in SEEDS.items():
        await seed_teams(factory, sport, path)
    records = parse_scoreboard(load_json("espn_nba/scoreboard_day.json"))
    await GamesRepository(factory).upsert_games("nba", "espn", records[:1], NOW)
    return factory


def _transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/scoreboard"):
            payload = deepcopy(load_json("espn_nba/scoreboard_day.json"))
            payload["events"] = payload["events"][:1]
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=load_json("espn_nba/summary_boxscore.json"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_job_is_opt_in_and_ingests_each_completed_game_once(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        calls = []
        settings = replace(load_settings(), nba_context_provider="espn")
        ctx = JobContext(
            factory, httpx.AsyncClient(transport=_transport(calls)), tmp_path, settings
        )
        monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "done"
        assert [request.url.path for request in calls] == [
            "/apis/site/v2/sports/basketball/nba/scoreboard",
            "/apis/site/v2/sports/basketball/nba/summary",
        ]
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(NbaTeamGameStat)) == 2
            assert await session.scalar(select(func.count()).select_from(NbaPlayerGameStat)) == 5
            refresh = await session.scalar(
                select(NbaContextRefresh).where(NbaContextRefresh.status == "complete")
            )
            assert refresh.status == "complete" and refresh.quota_remaining is None

        calls.clear()
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "empty"
        assert [request.url.path for request in calls] == [
            "/apis/site/v2/sports/basketball/nba/scoreboard"
        ]
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(NbaTeamGameStat)) == 2

    asyncio.run(scenario())


def test_job_plans_no_network_slot_without_explicit_provider(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        settings = replace(load_settings(), nba_context_provider="disabled")
        ctx = JobContext(factory, httpx.AsyncClient(), tmp_path, settings)
        assert await NbaTeamStatsJob().plan_slots(ctx, NOW) == []
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "empty"

    asyncio.run(scenario())


def test_espn_job_needs_no_provider_key(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded(tmp_path)
        settings = replace(load_settings(), nba_context_provider="espn")
        ctx = JobContext(factory, httpx.AsyncClient(), tmp_path, settings)
        assert await NbaTeamStatsJob().plan_slots(ctx, NOW)

    asyncio.run(scenario())


def test_partial_player_coverage_stays_pending_until_one_snapshot_is_complete(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        repo = ContextRepository(factory)
        game, teams, players = _box_score_records()
        home = [row for row in players if row.provider_team_id == game.home_provider_team_id]
        away = [row for row in players if row.provider_team_id == game.away_provider_team_id]

        await repo.save_nba_box_scores(game, teams, home, NOW, None)
        await repo.save_nba_box_scores(game, teams, away, NOW + timedelta(minutes=1), None)
        assert not await repo.nba_game_complete(game.provider_game_id)
        assert game.game_date in await repo.pending_nba_dates(NOW, limit=10)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(NbaTeamGameStat)) == 0
            assert await session.scalar(select(func.count()).select_from(NbaPlayerGameStat)) == 0

        await repo.save_nba_box_scores(game, teams, players, NOW + timedelta(minutes=2), None)
        assert await repo.nba_game_complete(game.provider_game_id)
        assert game.game_date not in await repo.pending_nba_dates(NOW, limit=10)

    asyncio.run(scenario())


def test_pending_dates_rotate_past_an_old_date_that_the_provider_did_not_cover(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        async with factory() as session, session.begin():
            orl = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "ORL"))
            mem = await session.scalar(select(Team).where(Team.sport == "nba", Team.abbr == "MEM"))
            session.add(
                Game(
                    sport="nba",
                    season=2026,
                    season_type="REG",
                    week=None,
                    start_time=datetime(2026, 1, 14, 0, 30, tzinfo=timezone.utc),
                    home_team_id=orl.id,
                    away_team_id=mem.id,
                    neutral=False,
                    status="final",
                    home_score=100,
                    away_score=90,
                    espn_id="older-uncovered",
                    updated_at=NOW,
                )
            )
        repo = ContextRepository(factory)
        await repo.record_nba_date_lookup(datetime(2026, 1, 13, tzinfo=timezone.utc), NOW, None)
        assert await repo.pending_nba_dates(NOW, limit=2) == [
            date(2026, 1, 15),
            date(2026, 1, 13),
        ]

    asyncio.run(scenario())


def test_manual_recheck_preserves_provider_corrections_as_later_observations(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        repo = ContextRepository(factory)
        game, original, players = _box_score_records()
        original_payload = load_json("espn_nba/summary_boxscore.json")
        first_seen = NOW
        corrected_at = NOW + timedelta(minutes=5)
        await repo.save_nba_box_scores(game, original, players, first_seen, None)

        corrected_payload = deepcopy(original_payload)
        corrected_payload["header"]["competitions"][0]["competitors"][0]["score"] = "113"
        corrected, corrected_players = parse_nba_box_score(game, corrected_payload)
        await repo.save_nba_box_scores(game, corrected, corrected_players, corrected_at, None)

        before = await repo.nba_team_stats_before(2026, corrected_at - timedelta(seconds=1))
        after = await repo.nba_team_stats_before(2026, corrected_at + timedelta(seconds=1))
        assert next(row.stats["PTS"] for row, _ in before if row.provider_team_id == "19") == 112
        assert next(row.stats["PTS"] for row, _ in after if row.provider_team_id == "19") == 113

    asyncio.run(scenario())


def test_nba_facts_are_idempotent_and_read_in_their_own_provider_namespace(tmp_path) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        repo = ContextRepository(factory)
        game, teams, players = _box_score_records()
        async with factory() as session, session.begin():
            local_game = await session.scalar(
                select(Game).where(Game.espn_id == game.provider_game_id)
            )
            local_teams = {
                row.abbr: row
                for row in (await session.scalars(select(Team).where(Team.sport == "nba"))).all()
            }
            for record in teams:
                session.add(
                    NbaTeamGameStat(
                        game_id=local_game.id,
                        team_id=local_teams[record.team].id,
                        provider="other",
                        provider_game_id=record.provider_game_id,
                        provider_team_id=record.provider_team_id,
                        fingerprint=repo._fingerprint(record.stats),
                        stats=dict(record.stats),
                        observed_at=NOW - timedelta(minutes=1),
                    )
                )
            for record in players:
                session.add(
                    NbaPlayerGameStat(
                        game_id=local_game.id,
                        team_id=local_teams[record.team].id,
                        provider="other",
                        provider_game_id=record.provider_game_id,
                        provider_team_id=record.provider_team_id,
                        provider_player_id=record.provider_player_id,
                        player=record.player,
                        minutes=record.minutes,
                        fingerprint=repo._fingerprint(
                            {
                                "player": record.player,
                                "minutes": record.minutes,
                                "stats": record.stats,
                            }
                        ),
                        stats=dict(record.stats),
                        observed_at=NOW - timedelta(minutes=1),
                    )
                )
            session.add(
                NbaPlayerGameStat(
                    game_id=local_game.id,
                    team_id=local_teams[game.home_team].id,
                    provider="other",
                    provider_game_id=game.provider_game_id,
                    provider_team_id=game.home_provider_team_id,
                    provider_player_id="foreign-star",
                    player="Foreign Star",
                    minutes=99,
                    fingerprint=repo._fingerprint(
                        {"player": "Foreign Star", "minutes": 99, "stats": {}}
                    ),
                    stats={},
                    observed_at=NOW - timedelta(minutes=1),
                )
            )

        result = await repo.save_nba_box_scores(game, teams, players, NOW, None)
        assert (result.team_rows, result.player_rows) == (2, 5)
        stats = await repo.nba_team_stats_before(2026, NOW + timedelta(minutes=1))
        assert len(stats) == 2
        assert {row.provider for row, _ in stats} == {"espn"}
        stars = await repo.star_players(
            local_teams[game.home_team].id, 2026, NOW + timedelta(minutes=1)
        )
        assert "Foreign Star" not in {row.player for row in stars}
        assert {row.source for row in stars} == {"espn"}

    asyncio.run(scenario())


def test_job_caps_each_batch_at_five_requests(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        calls = []
        games_payload = deepcopy(load_json("espn_nba/scoreboard_day.json"))
        final = games_payload["events"][0]
        games_payload["events"] = []
        for provider_id in range(100, 105):
            row = deepcopy(final)
            row["id"] = str(provider_id)
            games_payload["events"].append(row)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/scoreboard"):
                return httpx.Response(200, json=games_payload)
            return httpx.Response(200, json=load_json("espn_nba/summary_boxscore.json"))

        settings = replace(load_settings(), nba_context_provider="espn")
        ctx = JobContext(
            factory, httpx.AsyncClient(transport=httpx.MockTransport(handler)), tmp_path, settings
        )
        monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "done"
        assert len(calls) == 5
        assert sum(request.url.path.endswith("/summary") for request in calls) == 4

    asyncio.run(scenario())


def test_a_mixed_complete_and_unmatched_batch_remains_partial(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        factory = await _seeded_single_game(tmp_path)
        payload = deepcopy(load_json("espn_nba/scoreboard_day.json"))
        unmatched = deepcopy(payload["events"][0])
        unmatched["id"] = "102"
        competitors = unmatched["competitions"][0]["competitors"]
        next(row for row in competitors if row["homeAway"] == "home")["team"]["abbreviation"] = (
            "BAD"
        )
        next(row for row in competitors if row["homeAway"] == "away")["team"]["abbreviation"] = (
            "MISS"
        )
        payload["events"] = [payload["events"][0], unmatched]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/scoreboard"):
                return httpx.Response(200, json=payload)
            return httpx.Response(200, json=load_json("espn_nba/summary_boxscore.json"))

        settings = replace(load_settings(), nba_context_provider="espn")
        ctx = JobContext(
            factory, httpx.AsyncClient(transport=httpx.MockTransport(handler)), tmp_path, settings
        )
        monkeypatch.setattr(nba_jobs, "_now", lambda: NOW)
        assert await NbaTeamStatsJob().run(ctx, SLOT) == "partial"
        async with factory() as session:
            states = (
                await session.scalars(
                    select(NbaContextRefresh.status).where(
                        NbaContextRefresh.status.in_(("complete", "unmatched"))
                    )
                )
            ).all()
            assert set(states) == {"complete", "unmatched"}

    asyncio.run(scenario())
