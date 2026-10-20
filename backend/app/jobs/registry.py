"""The jobs invoked by both the CLI and the hosted scheduler endpoint."""

from app.domain.models import Sport
from app.jobs.bets import GradeBetsJob
from app.jobs.context import CONTEXT_JOBS
from app.jobs.nba import NBA_JOBS
from app.jobs.nfl import JOBS as NFL_JOBS
from app.jobs.odds import OddsSnapshotJob

JOBS = [
    *NFL_JOBS,
    *CONTEXT_JOBS,
    *NBA_JOBS,
    OddsSnapshotJob(),
    OddsSnapshotJob(Sport.NBA, "odds_snapshot_nba"),
    GradeBetsJob(),
]
