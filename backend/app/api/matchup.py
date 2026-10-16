"""One matchup: the game header, and the context a handicapper reads before betting it."""

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.efficiency import TeamGame, season_efficiency
from app.analytics.injuries import InjuredPlayer, qb_flag
from app.analytics.ratings import RatedGame, season_ratings
from app.analytics.records import (
    GameResult,
    RecordSet,
    Tally,
    build_records,
    head_to_head,
    summarize,
)
from app.analytics.schedule_spots import schedule_spot
from app.analytics.situational import Stop, is_primetime, situation
from app.api.slate import get_now, get_session_factory, load_slate_games, pick_lines
from app.domain.matchup import (
    ContextResponse,
    HeadToHeadView,
    InjuryView,
    MeetingView,
    MetricView,
    QbFlagView,
    RecordSetView,
    SituationView,
    TallyView,
    TeamContext,
)
from app.domain.slate import SlateGame, TeamView
from app.storage.context_repository import ContextRepository
from app.storage.games_repository import aware
from app.storage.models import Game, GameLine, Team, TeamGameStat, Venue

router = APIRouter(prefix="/api/v1")
COUNTED_SEASON_TYPES = ("REG", "POST")  # preseason results are not form
NBA = "nba"
RECENT_GAMES = 8  # enough to count 4-in-6 and the last seven days


def _tally(value: Tally) -> TallyView:
    return TallyView(win=value.win, loss=value.loss, push=value.push)


def _records(value: dict[str, RecordSet]) -> dict[str, RecordSetView]:
    return {
        name: RecordSetView(su=_tally(r.su), ats=_tally(r.ats), ou=_tally(r.ou))
        for name, r in value.items()
    }


async def _results(session: AsyncSession, sport: str, team_ids: set[int]) -> list[tuple]:
    """Final games for these teams with their closing line: (Game, GameResult) pairs."""
    games = (
        await session.scalars(
            select(Game).where(
                Game.sport == sport,
                Game.status == "final",
                Game.season_type.in_(COUNTED_SEASON_TYPES),
                Game.home_score.is_not(None),
                Game.away_score.is_not(None),
                or_(Game.home_team_id.in_(team_ids), Game.away_team_id.in_(team_ids)),
            )
        )
    ).all()
    lines: dict[int, list[GameLine]] = {game.id: [] for game in games}
    if games:
        for line in (
            await session.scalars(select(GameLine).where(GameLine.game_id.in_(list(lines))))
        ).all():
            lines[line.game_id].append(line)
    out = []
    for game in games:
        close = pick_lines(lines[game.id]).get("close")
        out.append(
            (
                game,
                GameResult(
                    game_id=game.id,
                    season=game.season,
                    start_time=aware(game.start_time),
                    home_id=game.home_team_id,
                    away_id=game.away_team_id,
                    home_score=game.home_score,
                    away_score=game.away_score,
                    spread_home=close.spread_home if close else None,
                    total=close.total if close else None,
                ),
            )
        )
    return out


async def _previous(
    session: AsyncSession, sport: str, team_id: int, before: datetime
) -> Optional[tuple]:
    """The team's last completed game before `before`: (Game, Venue or None, opponent Team)."""
    game = await session.scalar(
        select(Game)
        .where(
            Game.sport == sport,
            Game.status == "final",
            Game.season_type.in_(COUNTED_SEASON_TYPES),
            Game.start_time < before,
            or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
        )
        .order_by(Game.start_time.desc())
        .limit(1)
    )
    if game is None:
        return None
    venue = await session.get(Venue, game.venue_id) if game.venue_id else None
    opponent_id = game.away_team_id if game.home_team_id == team_id else game.home_team_id
    return game, venue, await session.get(Team, opponent_id)


async def _recent_stops(
    session: AsyncSession, sport: str, team_id: int, before: datetime
) -> list[Stop]:
    """The team's last few games before `before`, as places and times. Games not yet played still
    count: tomorrow's matchup is a back-to-back with tonight's game, which is still scheduled."""
    games = (
        await session.scalars(
            select(Game)
            .where(
                Game.sport == sport,
                Game.status.notin_(("postponed", "canceled")),
                Game.season_type.in_(COUNTED_SEASON_TYPES),
                Game.start_time < before,
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
            .order_by(Game.start_time.desc())
            .limit(RECENT_GAMES)
        )
    ).all()
    stops = []
    for game in games:
        venue = await session.get(Venue, game.venue_id) if game.venue_id else None
        opponent_id = game.away_team_id if game.home_team_id == team_id else game.home_team_id
        opponent = await session.get(Team, opponent_id)
        stops.append(
            Stop(
                kickoff=aware(game.start_time),
                season=game.season,
                lat=venue.lat if venue else None,
                lon=venue.lon if venue else None,
                tz=venue.tz if venue else None,
                opponent=opponent.abbr,
            )
        )
    return stops


async def _efficiency_rows(session: AsyncSession, sport: str, season: int) -> list[tuple]:
    rows = await session.execute(
        select(TeamGameStat, Game)
        .join(Game, Game.id == TeamGameStat.game_id)
        .where(Game.sport == sport, Game.season == season)
    )
    return rows.all()


def _team_game(stat: TeamGameStat, game: Game) -> TeamGame:
    is_home = stat.team_id == game.home_team_id
    return TeamGame(
        game_id=game.id,
        team_id=stat.team_id,
        start_time=aware(game.start_time),
        stats=stat.stats,
        points_for=game.home_score if is_home else game.away_score,
        points_against=game.away_score if is_home else game.home_score,
    )


@router.get("/games/{game_id}", response_model=SlateGame)
async def get_game(
    game_id: int,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> SlateGame:
    async with session_factory() as session:
        games = await load_slate_games(session, Game.id == game_id)
    if not games:
        raise HTTPException(404, detail="Game not found")
    return games[0]


@router.get("/games/{game_id}/context", response_model=ContextResponse)
async def get_context(
    game_id: int,
    last_n: int = Query(5, ge=1, le=20),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> ContextResponse:
    async with session_factory() as session:
        game = await session.get(Game, game_id)
        if game is None:
            raise HTTPException(404, detail="Game not found")
        kickoff = aware(game.start_time)
        teams = {
            team.id: team
            for team in (
                await session.scalars(
                    select(Team).where(Team.id.in_([game.home_team_id, game.away_team_id]))
                )
            ).all()
        }
        home, away = teams[game.home_team_id], teams[game.away_team_id]
        venue = await session.get(Venue, game.venue_id) if game.venue_id else None
        pairs = await _results(session, game.sport, {home.id, away.id})
        results = [result for _, result in pairs]
        stat_rows = await _efficiency_rows(session, game.sport, game.season)
        is_nba = game.sport == NBA
        if is_nba:
            rated = [
                RatedGame(g.id, stat.team_id, aware(g.start_time), stat.stats)
                for stat, g in stat_rows
            ]
            efficiency = season_ratings(rated, kickoff)
        else:
            efficiency = season_efficiency(
                [_team_game(stat, g) for stat, g in stat_rows], kickoff
            )
        stat_times = [
            aware(stat.fetched_at) for stat, g in stat_rows if aware(g.start_time) < kickoff
        ]
        injuries, injuries_at = await ContextRepository(session_factory).injury_snapshot(
            game.sport, {home.id, away.id}, min(now, kickoff)
        )
        previous = {
            team.id: await _previous(session, game.sport, team.id, kickoff) for team in (home, away)
        }
        recent = (
            {
                team.id: await _recent_stops(session, game.sport, team.id, kickoff)
                for team in (home, away)
            }
            if is_nba
            else {}
        )

    def context(team: Team) -> TeamContext:
        prev = previous[team.id]
        stop = None
        last_qb = None
        if prev is not None:
            prev_game, prev_venue, opponent = prev
            stop = Stop(
                kickoff=aware(prev_game.start_time),
                season=prev_game.season,
                lat=prev_venue.lat if prev_venue else None,
                lon=prev_venue.lon if prev_venue else None,
                tz=prev_venue.tz if prev_venue else None,
                opponent=opponent.abbr,
            )
            last_qb = prev_game.home_qb if prev_game.home_team_id == team.id else prev_game.away_qb
        injured = [InjuredPlayer(i.player, i.position, i.status) for i in injuries[team.id]]
        flag = qb_flag(last_qb, injured)
        arena = (
            venue.lat if venue else None, venue.lon if venue else None,
            venue.tz if venue else None,
        )
        if is_nba:
            nba_spot = schedule_spot(recent[team.id], kickoff, game.season, *arena)
            spot_view = SituationView(
                rest_days=nba_spot.rest_days, short_week=False, off_bye=False,
                travel_miles=nba_spot.travel_miles, tz_crossed=nba_spot.tz_crossed,
                previous_opponent=nba_spot.previous_opponent,
                back_to_back=nba_spot.back_to_back, three_in_four=nba_spot.three_in_four,
                four_in_six=nba_spot.four_in_six, games_last_7=nba_spot.games_last_7,
            )
        else:
            spot_view = SituationView(
                **asdict(situation(stop, kickoff, game.season, *arena))
            )
        return TeamContext(
            team=TeamView(id=team.id, abbr=team.abbr, name=team.name),
            records=_records(build_records(results, team.id, game.season, kickoff, last_n)),
            efficiency={
                name: MetricView(value=m.value, rank=m.rank, of=m.of)
                for name, m in efficiency.get(team.id, {}).items()
            },
            situation=spot_view,
            injuries=[
                InjuryView(
                    player=i.player, position=i.position, status=i.status, detail=i.detail,
                    reported_at=i.reported_at,
                )
                for i in injuries[team.id]
            ],
            qb_flag=QbFlagView(player=flag.player, status=flag.status) if flag else None,
        )

    meetings = head_to_head(results, home.id, away.id, kickoff, last_n)
    abbr = {home.id: home.abbr, away.id: away.abbr}
    prior_updates = [aware(g.updated_at) for g, r in pairs if r.start_time < kickoff]
    return ContextResponse(
        game_id=game.id,
        sport=game.sport,
        last_n=last_n,
        divisional=bool(home.conference) and home.conference == away.conference,
        primetime=False if is_nba else is_primetime(kickoff),
        home=context(home),
        away=context(away),
        head_to_head=HeadToHeadView(
            meetings=[
                MeetingView(
                    game_id=m.game_id, start_time=m.start_time, home=abbr[m.home_id],
                    away=abbr[m.away_id], home_score=m.home_score, away_score=m.away_score,
                    spread_home=m.spread_home, total=m.total,
                )
                for m in meetings
            ],
            home_su=_tally(summarize(meetings, home.id).su),
            home_ats=_tally(summarize(meetings, home.id).ats),
        ),
        data_as_of={
            "records": max(prior_updates) if prior_updates else None,
            "efficiency": max(stat_times) if stat_times else None,
            "situational": aware(game.updated_at),
            "injuries": injuries_at,
        },
    )
