"""One matchup: the game header, and the context a handicapper reads before betting it."""

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.efficiency import TeamGame, season_efficiency
from app.analytics.injuries import InjuredPlayer, qb_flag
from app.analytics.records import (
    GameResult,
    RecordSet,
    Tally,
    build_records,
    head_to_head,
    summarize,
)
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
from app.storage.games_repository import aware
from app.storage.models import Game, GameLine, Injury, Team, TeamGameStat, Venue

router = APIRouter(prefix="/api/v1")
COUNTED_SEASON_TYPES = ("REG", "POST")  # preseason results are not form


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


async def _injury_snapshot(
    session: AsyncSession, team_ids: set[int], as_of: datetime
) -> tuple[dict[int, list[Injury]], Optional[datetime]]:
    observed = await session.scalar(
        select(func.max(Injury.observed_at)).where(Injury.observed_at <= as_of)
    )
    by_team: dict[int, list[Injury]] = {team_id: [] for team_id in team_ids}
    if observed is None:
        return by_team, None
    rows = await session.scalars(
        select(Injury)
        .where(Injury.observed_at == observed, Injury.team_id.in_(team_ids))
        .order_by(Injury.status, Injury.player)
    )
    for row in rows.all():
        by_team[row.team_id].append(row)
    return by_team, aware(observed)


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
        efficiency = season_efficiency([_team_game(stat, g) for stat, g in stat_rows], kickoff)
        stat_times = [
            aware(stat.fetched_at) for stat, g in stat_rows if aware(g.start_time) < kickoff
        ]
        injuries, injuries_at = await _injury_snapshot(
            session, {home.id, away.id}, min(now, kickoff)
        )
        previous = {
            team.id: await _previous(session, game.sport, team.id, kickoff) for team in (home, away)
        }

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
        spot = situation(
            stop, kickoff, game.season,
            venue.lat if venue else None, venue.lon if venue else None, venue.tz if venue else None,
        )
        return TeamContext(
            team=TeamView(id=team.id, abbr=team.abbr, name=team.name),
            records=_records(build_records(results, team.id, game.season, kickoff, last_n)),
            efficiency={
                name: MetricView(value=m.value, rank=m.rank, of=m.of)
                for name, m in efficiency.get(team.id, {}).items()
            },
            situation=SituationView(**asdict(spot)),
            injuries=[
                InjuryView(
                    player=i.player, position=i.position, status=i.status, detail=i.detail,
                    reported_at=aware(i.reported_at) if i.reported_at else None,
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
        last_n=last_n,
        divisional=bool(home.conference) and home.conference == away.conference,
        primetime=is_primetime(kickoff),
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

