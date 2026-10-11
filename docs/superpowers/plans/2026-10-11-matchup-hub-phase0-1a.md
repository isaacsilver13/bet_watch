# Matchup Hub: Phase 0 + 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make betting_aggregator a local-only app that ingests NFL teams, games and reference lines from nflverse and ESPN on a schedule, and shows a week-by-week NFL slate page.

**Architecture:**
- **Pure parsers:** `app/ingest/` turns ESPN JSON and nflverse CSV into shared `GameRecord` and `LineRecord` dataclasses.
- **Repository:** `GamesRepository` upserts those records into new Postgres tables, with team-alias resolution.
- **Job runner:** `app/jobs/` schedules work as idempotent "slots" stored in `job_slots`, fired by Windows Task Scheduler via `python -m app.jobs run-due`.
- **API and UI:** a new `/api/v1/slate` route feeds a React Router + React Query Slate page. The existing odds board moves to `/odds`.

**Tech Stack:**
- Backend: Python ≥3.9, FastAPI, SQLAlchemy 2 async, Alembic, httpx, `zoneinfo` + `tzdata`, pytest with aiosqlite.
- Frontend: React 18, Vite, TypeScript, react-router-dom 6, @tanstack/react-query 5, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-11-matchup-hub-design.md`. Read it first; it is on this same branch.

## Global Constraints

- Python 3.9 compatible. Use `Optional[...]`, never `X | None`. No `match` statements and no `dataclass(slots=True)`. Ruff targets `py39` with rules `E,F,I,UP,B` and line length 100.
- **Local only.** Nothing may deploy to Fly. Ask the user before any deploy, paid service or irreversible operation.
- **Free data only:**
  - ESPN public JSON (`site.api.espn.com`), no key.
  - nflverse `games.csv`, CC-BY-4.0.
  - The Odds API free tier, on its own key separate from the NBA collector's.
- **Free-tier bookmakers:**
  - Allowed: `draftkings,fanduel,betmgm,betrivers,espnbet`.
  - Never default to the paid-only `williamhill_us` or `fanatics`.
- **No network in tests.** Use committed fixtures and `httpx.MockTransport`.
- **Fixture mode stays the default** (`PROVIDER_MODE=fixture`), so development spends no credits.
- Kalshi stays out of `/odds`. That is unchanged.
- **Spread sign convention everywhere:** `spread_home` is the home team's line. Negative means home is favored.
- **All schedule windows** are in `America/Chicago`, via `zoneinfo`.
- **Framing:** analysis is statistically honest and is not betting advice.

## Review Focus

These are inputs no task's happy-path test exercises but a real week will. Each one has a pinned test in its owning task.

1. **ESPN "close" before kickoff is really the current line.** It must be stored as `current`, never `close`, while the game state is `pre`. Pinned in Task 5.
2. **Postponed or cancelled games** must not show as final or carry 0–0 scores. Pinned in Task 5.
3. **Neutral-site and international games** (London and others) use venues not in the seed. They must create a venue row, never inherit the home team's stadium, and a later nflverse upsert must not wipe that venue. Pinned in Task 6.
4. **The opener must never be overwritten** by later scoreboard pulls, even if ESPN's "open" value changes. Pinned in Task 6.
5. **The PC was off for hours.** Past slots become `missed` exactly once, and terminal slots never re-run. Pinned in Task 8.

---

## Phase 0 precondition (user action, not a code task)

The betting_aggregator working tree has 23 uncommitted files on `feat/sportsbooks-prediction-markets`. Before Task 1, the user must commit or PR that work and merge it into `master`. Then:

```bash
cd C:/Users/justj/nba_prediction/repos/betting_aggregator
git switch master && git pull
git switch -c feat/matchup-hub-1a
git merge --no-edit docs/matchup-hub-spec
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
docker compose up -d postgres
```

Every task below runs from the repo root `C:/Users/justj/nba_prediction/repos/betting_aggregator` with the venv active. `pytest` there runs `backend/tests`.

---

### Task 1: Local-only configuration

**Files:**
- Modify: `.github/workflows/ci-deploy.yml` (delete the `deploy` job)
- Modify: `.env.example`
- Modify: `pyproject.toml` (add `tzdata`)
- Modify: `backend/app/config.py` (add `raw_data_dir`)
- Modify: `.gitignore` (add `/data/`)
- Test: `backend/tests/test_local_config.py`

**Interfaces:**
- Produces: `Settings.raw_data_dir: str`, which defaults to `<repo>/data/raw` and can be overridden with `RAW_DATA_DIR`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_local_config.py
from pathlib import Path

import app.config
from app.config import load_settings

REPO = Path(__file__).resolve().parents[2]


def test_raw_data_dir_defaults_under_repo_data(monkeypatch) -> None:
    monkeypatch.delenv("RAW_DATA_DIR", raising=False)
    assert Path(load_settings().raw_data_dir) == REPO / "data" / "raw"


def test_raw_data_dir_can_be_overridden(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path))
    assert load_settings().raw_data_dir == str(tmp_path)


def test_env_example_has_no_paid_only_books_and_enables_persistence() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    books_line = next(line for line in text.splitlines() if line.startswith("ODDS_BOOKMAKERS="))
    books = books_line.split("=", 1)[1].split(",")
    assert books == ["draftkings", "fanduel", "betmgm", "betrivers", "espnbet"]
    assert "PERSISTENCE_ENABLED=true" in text


def test_ci_never_deploys() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci-deploy.yml").read_text(encoding="utf-8")
    assert "flyctl" not in workflow
    assert "deploy:" not in workflow


def test_dotenv_is_isolated_for_tests() -> None:
    assert app.config._DOTENV_PATH.name == "does-not-exist.env"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_local_config.py -v`
Expected: FAIL. `Settings` has no attribute `raw_data_dir`, the bookmakers line still lists `williamhill_us`, and the workflow still contains `flyctl`.

- [ ] **Step 3: Implement**

In `backend/app/config.py`:
- Add the field as the last one in `Settings`: `raw_data_dir: str = "data/raw"`.
- Compute the default from this module's own location, not from `_DOTENV_PATH`, which the test conftest repoints.
- Treat an empty value as unset, because `.env.example` ships `RAW_DATA_DIR=`.

```python
_REPO_ROOT = Path(__file__).resolve().parents[2]  # next to _DOTENV_PATH
...
        # in load_settings() return:
        raw_data_dir=_setting("RAW_DATA_DIR", "").strip() or str(_REPO_ROOT / "data" / "raw"),
```

In `pyproject.toml`, add `"tzdata>=2024.1",` to `dependencies`. Windows has no IANA time-zone database, and `zoneinfo` needs one.

In `.gitignore`, append:
```
/data/
```

In `.env.example`:
- Set `PERSISTENCE_ENABLED=true`.
- Replace the bookmakers line and its comment with:
```
# Free-tier books only (verified 2026-10-11): Caesars (williamhill_us) and Fanatics are paid-only.
# espnbet is theScore Bet (us2 region); an allowlist spans regions. Up to 10 books = 1 region of quota.
ODDS_BOOKMAKERS=draftkings,fanduel,betmgm,betrivers,espnbet
```
- Add at the end:
```
# Where paid/raw provider payloads are written before parsing (gitignored). Empty = <repo>/data/raw.
RAW_DATA_DIR=
```

In `.github/workflows/ci-deploy.yml`, delete the whole `deploy:` job (from `  deploy:` to the end of the file). Leave `backend-test`, `frontend-build` and `browser-smoke` untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest backend/tests/test_local_config.py -v && pytest -q`
Expected: the new tests PASS and the full suite is still green.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci-deploy.yml .env.example pyproject.toml backend/app/config.py .gitignore backend/tests/test_local_config.py
git commit -m "chore: local-only defaults (no Fly deploy, free-tier books, tzdata, raw data dir)"
```

---

### Task 2: Odds coverage measurement + live smoke script

**Files:**
- Create: `backend/app/analytics/__init__.py` (empty)
- Create: `backend/app/analytics/coverage.py`
- Create: `backend/scripts/odds_coverage_smoke.py`
- Test: `backend/tests/test_coverage.py`

**Interfaces:**
- Produces: `coverage_matrix(comparisons: Sequence[EventComparison]) -> dict[str, dict[str, int]]`. It maps bookmaker title to market type value to the number of distinct events with at least one offer. Phase 1c reuses it for content-aware snapshot success.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_coverage.py
import asyncio

from app.analytics.coverage import coverage_matrix
from app.providers.fixture import FixtureOddsProvider


def _comparisons():
    return list(asyncio.run(FixtureOddsProvider().get_comparisons()))


def test_matrix_lists_every_book_with_event_counts() -> None:
    comparisons = _comparisons()
    matrix = coverage_matrix(comparisons)
    books = {offer.bookmaker for c in comparisons for offer in c.offers}
    assert set(matrix) == books
    counts = [n for markets in matrix.values() for n in markets.values()]
    assert counts and all(1 <= n <= len(comparisons) for n in counts)


def test_same_event_counted_once() -> None:
    comparisons = _comparisons()
    assert coverage_matrix(comparisons + comparisons) == coverage_matrix(comparisons)


def test_empty_input() -> None:
    assert coverage_matrix([]) == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.analytics'`.

- [ ] **Step 3: Implement**

```python
# backend/app/analytics/coverage.py
"""Measure which books and markets a snapshot actually returned (never assume coverage)."""

from collections import defaultdict
from collections.abc import Sequence

from app.domain.models import EventComparison


def coverage_matrix(comparisons: Sequence[EventComparison]) -> dict[str, dict[str, int]]:
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for comparison in comparisons:
        for offer in comparison.offers:
            seen[offer.bookmaker][offer.market_type.value].add(comparison.event.id)
    return {
        book: {market: len(events) for market, events in markets.items()}
        for book, markets in seen.items()
    }
```

```python
# backend/scripts/odds_coverage_smoke.py
"""One live Odds API call to record which books/markets the free tier really returns.

Costs about 3 credits per sport. Needs PROVIDER_MODE=live and THE_ODDS_API_KEY in .env.
Usage:  python backend/scripts/odds_coverage_smoke.py nfl
"""

import asyncio
import json
import sys

from app.analytics.coverage import coverage_matrix
from app.config import load_settings
from app.domain.models import Sport
from app.providers.the_odds_api import TheOddsApiProvider
from app.providers.the_odds_api.client import TheOddsApiClient


async def main(sport_name: str) -> int:
    settings = load_settings()
    if not settings.the_odds_api_key:
        print("Set THE_ODDS_API_KEY (and PROVIDER_MODE=live) in .env first.")
        return 2
    provider = TheOddsApiProvider(
        TheOddsApiClient(
            settings.the_odds_api_key,
            timeout=settings.provider_timeout_seconds,
            regions=settings.odds_regions,
            bookmakers=settings.odds_bookmakers,
        )
    )
    comparisons = await provider.get_comparisons(sport=Sport(sport_name))
    report = {
        "sport": sport_name,
        "events": len(comparisons),
        "requested_books": list(settings.odds_bookmakers),
        "returned_coverage": coverage_matrix(comparisons),
        "quota_remaining": provider.quota_remaining,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "nfl")))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest backend/tests/test_coverage.py -v && ruff check backend`
Expected: PASS, with no lint errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/analytics backend/scripts/odds_coverage_smoke.py backend/tests/test_coverage.py
git commit -m "feat: measure book x market coverage; add one-shot live coverage smoke script"
```

- [ ] **Step 6: Live smoke run (user, spends ~3 credits)**

Ask the user to put their **new, separate** Odds API key in `.env` with `PROVIDER_MODE=live` and `ODDS_PROVIDER=the_odds_api`, then run:

```bash
python backend/scripts/odds_coverage_smoke.py nfl
```

Paste the JSON output into the PR description as the measured coverage. Switch `.env` back to fixture mode afterwards.

---

### Task 3: Matchup schema (models + Alembic migration 0002)

**Files:**
- Modify: `backend/app/storage/models.py` (append six models)
- Create: `backend/migrations/versions/0002_matchup_core.py`
- Create: `backend/tests/matchup_helpers.py`
- Test: `backend/tests/test_matchup_schema.py`

**Interfaces:**
- Produces these ORM classes in `app.storage.models`:
  - `Venue(id, espn_id, name, lat, lon, tz, roof, surface)`
  - `Team(id, sport, abbr, name, espn_id, conference, venue_id)`
  - `TeamAlias(id, team_id, sport, alias, source)`
  - `Game(id, sport, season, season_type, week, start_time, home_team_id, away_team_id, venue_id, neutral, status, home_score, away_score, espn_id, event_id, updated_at)`
  - `GameLine(id, game_id, source, kind, spread_home, spread_home_price, spread_away_price, total, over_price, under_price, ml_home, ml_away, captured_at)`
  - `JobSlot(id, job, slot_key, state, attempts, coverage, raw_path, updated_at)`
- Produces test helpers `tests.matchup_helpers.new_session_factory(tmp_path)` and `load_json(relative_path)`.

- [ ] **Step 1: Write the test helper and the failing test**

```python
# backend/tests/matchup_helpers.py
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.storage.database import create_schema, create_session_factory

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(relative_path: str):
    return json.loads((FIXTURES / relative_path).read_text(encoding="utf-8"))


def db_url(tmp_path: Path) -> str:
    # A file DB (not :memory:) so a second engine, e.g. one used by TestClient, sees the same data.
    return f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


async def new_session_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url(tmp_path))
    await create_schema(engine)
    return create_session_factory(engine)
```

```python
# backend/tests/test_matchup_schema.py
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.storage.models import Game, GameLine, JobSlot, Team, TeamAlias, Venue
from tests.matchup_helpers import new_session_factory

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def test_game_with_lines_round_trips(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            venue = Venue(espn_id="3937", name="Empower Field", roof="outdoors")
            session.add(venue)
            await session.flush()
            den = Team(sport="nfl", abbr="DEN", name="Denver Broncos", venue_id=venue.id)
            sea = Team(sport="nfl", abbr="SEA", name="Seattle Seahawks")
            session.add_all([den, sea])
            await session.flush()
            session.add(TeamAlias(team_id=den.id, sport="nfl", alias="den", source="seed"))
            game = Game(
                sport="nfl", season=2026, season_type="REG", week=6, start_time=NOW,
                home_team_id=den.id, away_team_id=sea.id, venue_id=venue.id, neutral=False,
                status="scheduled", espn_id="401872995", updated_at=NOW,
            )
            session.add(game)
            await session.flush()
            session.add(GameLine(game_id=game.id, source="espn", kind="open", spread_home=2.5,
                                 total=43.5, ml_home=110, ml_away=-130, captured_at=NOW))
            session.add(JobSlot(job="espn_scoreboard", slot_key="k1", state="pending",
                                attempts=0, coverage={"DraftKings": {"spread": 1}}, updated_at=NOW))
        async with factory() as session:
            line = (await session.execute(GameLine.__table__.select())).first()
            assert line.spread_home == 2.5
            slot = (await session.execute(JobSlot.__table__.select())).first()
            assert slot.coverage == {"DraftKings": {"spread": 1}}

    asyncio.run(scenario())


def test_one_line_per_game_source_kind(tmp_path) -> None:
    async def scenario() -> None:
        factory = await new_session_factory(tmp_path)
        async with factory() as session, session.begin():
            a = Team(sport="nfl", abbr="A", name="A")
            b = Team(sport="nfl", abbr="B", name="B")
            session.add_all([a, b])
            await session.flush()
            game = Game(sport="nfl", season=2026, season_type="REG", week=1, start_time=NOW,
                        home_team_id=a.id, away_team_id=b.id, neutral=False,
                        status="scheduled", espn_id="1", updated_at=NOW)
            session.add(game)
            await session.flush()
            session.add(GameLine(game_id=game.id, source="espn", kind="open", captured_at=NOW))
        with pytest.raises(IntegrityError):
            async with factory() as session, session.begin():
                session.add(GameLine(game_id=1, source="espn", kind="open", captured_at=NOW))

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_matchup_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'Game'`.

- [ ] **Step 3: Implement the models**

Append to `backend/app/storage/models.py`. Extend the existing `sqlalchemy` import line to also bring in `JSON`, `Boolean` and `UniqueConstraint`.

```python
class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    espn_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tz: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    roof: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # outdoors|dome|retractable
    surface: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport", "abbr", name="uq_teams_sport_abbr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    abbr: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    espn_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    conference: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # e.g. "AFC West"
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), nullable=True)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("sport", "alias", name="uq_team_aliases_sport_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)  # identity.normalize_name()
    source: Mapped[str] = mapped_column(String(40), nullable=False)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_sport_season_week", "sport", "season", "season_type", "week"),
        Index("ix_games_start_time", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(20), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str] = mapped_column(String(10), nullable=False)  # PRE|REG|POST
    week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id"), nullable=True)
    neutral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    espn_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(ForeignKey("events.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameLine(Base):
    __tablename__ = "game_lines"
    __table_args__ = (
        UniqueConstraint("game_id", "source", "kind", name="uq_game_lines_game_source_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # espn|nflverse
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # open|current|close
    spread_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread_home_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    spread_away_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    over_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    under_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_home: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_away: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobSlot(Base):
    __tablename__ = "job_slots"
    __table_args__ = (UniqueConstraint("job", "slot_key", name="uq_job_slots_job_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(60), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    raw_path: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Write migration 0002**

```python
# backend/migrations/versions/0002_matchup_core.py
"""Venues, teams, aliases, games, reference lines, and job slots for the matchup hub."""

import sqlalchemy as sa
from alembic import op

revision = "0002_matchup_core"
down_revision = "0001_odds_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "venues",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("espn_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("tz", sa.String(length=60), nullable=True),
        sa.Column("roof", sa.String(length=20), nullable=True),
        sa.Column("surface", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_id"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("abbr", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("espn_id", sa.String(length=40), nullable=True),
        sa.Column("conference", sa.String(length=60), nullable=True),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "abbr", name="uq_teams_sport_abbr"),
    )
    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("alias", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sport", "alias", name="uq_team_aliases_sport_alias"),
    )
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.String(length=10), nullable=False),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=True),
        sa.Column("neutral", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("espn_id", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=180), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_id"),
    )
    op.create_index(
        "ix_games_sport_season_week", "games", ["sport", "season", "season_type", "week"]
    )
    op.create_index("ix_games_start_time", "games", ["start_time"])
    op.create_table(
        "game_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("spread_home", sa.Float(), nullable=True),
        sa.Column("spread_home_price", sa.Integer(), nullable=True),
        sa.Column("spread_away_price", sa.Integer(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("over_price", sa.Integer(), nullable=True),
        sa.Column("under_price", sa.Integer(), nullable=True),
        sa.Column("ml_home", sa.Integer(), nullable=True),
        sa.Column("ml_away", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "source", "kind", name="uq_game_lines_game_source_kind"),
    )
    op.create_table(
        "job_slots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job", sa.String(length=60), nullable=False),
        sa.Column("slot_key", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("raw_path", sa.String(length=400), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job", "slot_key", name="uq_job_slots_job_key"),
    )


def downgrade() -> None:
    op.drop_table("job_slots")
    op.drop_table("game_lines")
    op.drop_index("ix_games_start_time", table_name="games")
    op.drop_index("ix_games_sport_season_week", table_name="games")
    op.drop_table("games")
    op.drop_table("team_aliases")
    op.drop_table("teams")
    op.drop_table("venues")
```

- [ ] **Step 5: Run the tests, then the migration round trip against local Postgres**

Run: `pytest backend/tests/test_matchup_schema.py -v`
Expected: PASS.

Run (with `docker compose up -d postgres` running):
```bash
alembic upgrade head && alembic downgrade 0001_odds_observations && alembic upgrade head
```
Expected: no errors. Running `alembic check` afterwards reports no differences between the models and the migrations ("No new upgrade operations detected").

- [ ] **Step 6: Commit**

```bash
git add backend/app/storage/models.py backend/migrations/versions/0002_matchup_core.py backend/tests/matchup_helpers.py backend/tests/test_matchup_schema.py
git commit -m "feat: matchup core schema (venues, teams, aliases, games, lines, job slots)"
```

---

### Task 4: NFL seed (teams, venues, aliases)

**Files:**
- Create: `backend/app/ingest/__init__.py` (empty)
- Create: `backend/app/ingest/data/nfl_teams.csv`
- Create: `backend/app/ingest/seed.py`
- Modify: `pyproject.toml` (ship the CSV as package data)
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: `Venue`, `Team`, `TeamAlias` (Task 3) and `app.identity.normalize_name` (existing).
- Produces:
  - `seed_teams(session_factory, sport: str = "nfl", path: Path = NFL_SEED) -> int`, which returns the number of rows seeded and is idempotent.
  - The constant `NFL_SEED: Path`.
  - Aliases stored as `normalize_name(alias)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_seed.py
import asyncio
from collections import Counter

from sqlalchemy import func, select

from app.identity import normalize_name
from app.ingest.seed import NFL_SEED, load_seed_rows, seed_teams
from app.storage.models import Team, TeamAlias, Venue
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest'`.

- [ ] **Step 3: Write the seed CSV**

How the values were sourced:
- ESPN team ids and venue ids come from ESPN's `/teams` and scoreboard payloads, checked 2026-10-11.
- Coordinates are stadium locations.
- `aliases` is pipe-separated and adds to the abbreviation and full name, which are always aliased. It covers nflverse abbreviations (WAS, LA) and pre-relocation names for 2015+ history.

```csv
abbr,name,espn_id,conference,venue_espn_id,venue_name,lat,lon,tz,roof,aliases
ARI,Arizona Cardinals,22,NFC West,3970,State Farm Stadium,33.5276,-112.2626,America/Phoenix,retractable,
ATL,Atlanta Falcons,1,NFC South,5348,Mercedes-Benz Stadium,33.7554,-84.4008,America/New_York,retractable,
BAL,Baltimore Ravens,33,AFC North,3814,M&T Bank Stadium,39.2780,-76.6227,America/New_York,outdoors,
BUF,Buffalo Bills,2,AFC East,11938,Highmark Stadium,42.7738,-78.7870,America/New_York,outdoors,
CAR,Carolina Panthers,29,NFC South,3628,Bank of America Stadium,35.2258,-80.8528,America/New_York,outdoors,
CHI,Chicago Bears,3,NFC North,3933,Soldier Field,41.8623,-87.6167,America/Chicago,outdoors,
CIN,Cincinnati Bengals,4,AFC North,3874,Paycor Stadium,39.0955,-84.5161,America/New_York,outdoors,
CLE,Cleveland Browns,5,AFC North,3679,Huntington Bank Field,41.5061,-81.6995,America/New_York,outdoors,
DAL,Dallas Cowboys,6,NFC East,3687,AT&T Stadium,32.7473,-97.0945,America/Chicago,retractable,
DEN,Denver Broncos,7,AFC West,3937,Empower Field at Mile High,39.7439,-105.0201,America/Denver,outdoors,
DET,Detroit Lions,8,NFC North,3727,Ford Field,42.3400,-83.0456,America/Detroit,dome,
GB,Green Bay Packers,9,NFC North,3798,Lambeau Field,44.5013,-88.0622,America/Chicago,outdoors,
HOU,Houston Texans,34,AFC South,3891,NRG Stadium,29.6847,-95.4107,America/Chicago,retractable,
IND,Indianapolis Colts,11,AFC South,3812,Lucas Oil Stadium,39.7601,-86.1639,America/Indiana/Indianapolis,retractable,
JAX,Jacksonville Jaguars,30,AFC South,3712,EverBank Stadium,30.3239,-81.6373,America/New_York,outdoors,
KC,Kansas City Chiefs,12,AFC West,3622,Arrowhead Stadium,39.0489,-94.4839,America/Chicago,outdoors,
LAC,Los Angeles Chargers,24,AFC West,7065,SoFi Stadium,33.9535,-118.3392,America/Los_Angeles,dome,SD|San Diego Chargers
LAR,Los Angeles Rams,14,NFC West,7065,SoFi Stadium,33.9535,-118.3392,America/Los_Angeles,dome,LA|STL|St. Louis Rams
LV,Las Vegas Raiders,13,AFC West,6501,Allegiant Stadium,36.0909,-115.1833,America/Los_Angeles,dome,OAK|LVR|Oakland Raiders
MIA,Miami Dolphins,15,AFC East,3948,Hard Rock Stadium,25.9580,-80.2389,America/New_York,outdoors,
MIN,Minnesota Vikings,16,NFC North,5239,U.S. Bank Stadium,44.9737,-93.2575,America/Chicago,dome,
NE,New England Patriots,17,AFC East,3738,Gillette Stadium,42.0909,-71.2643,America/New_York,outdoors,
NO,New Orleans Saints,18,NFC South,3493,Caesars Superdome,29.9511,-90.0812,America/Chicago,dome,
NYG,New York Giants,19,NFC East,3839,MetLife Stadium,40.8135,-74.0745,America/New_York,outdoors,
NYJ,New York Jets,20,AFC East,3839,MetLife Stadium,40.8135,-74.0745,America/New_York,outdoors,
PHI,Philadelphia Eagles,21,NFC East,3806,Lincoln Financial Field,39.9008,-75.1675,America/New_York,outdoors,
PIT,Pittsburgh Steelers,23,AFC North,3752,Acrisure Stadium,40.4468,-80.0158,America/New_York,outdoors,
SEA,Seattle Seahawks,26,NFC West,3673,Lumen Field,47.5952,-122.3316,America/Los_Angeles,outdoors,
SF,San Francisco 49ers,25,NFC West,4738,Levi's Stadium,37.4030,-121.9700,America/Los_Angeles,outdoors,
TB,Tampa Bay Buccaneers,27,NFC South,3886,Raymond James Stadium,27.9759,-82.5033,America/New_York,outdoors,
TEN,Tennessee Titans,10,AFC South,3810,Nissan Stadium,36.1665,-86.7713,America/Chicago,outdoors,
WSH,Washington Commanders,28,NFC East,3719,Northwest Stadium,38.9077,-76.8645,America/New_York,outdoors,WAS|Washington|Washington Football Team|Washington Redskins
```

- [ ] **Step 4: Implement the seed loader**

```python
# backend/app/ingest/seed.py
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
            t.abbr: t for t in (await session.scalars(select(Team).where(Team.sport == sport))).all()
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
```

In `pyproject.toml`, add the following so `pip install -e .` and wheels include the CSV:
```toml
[tool.setuptools.package-data]
"app.ingest" = ["data/*.csv"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/tests/test_seed.py -v && ruff check backend`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingest pyproject.toml backend/tests/test_seed.py
git commit -m "feat: NFL team/venue/alias seed"
```

---

### Task 5: Shared records + ESPN parsing

**Files:**
- Create: `backend/app/ingest/records.py`
- Create: `backend/app/ingest/espn.py`
- Create: `backend/tests/fixtures/espn/scoreboard_week6.json`
- Create: `backend/tests/fixtures/espn/summary_final.json`
- Test: `backend/tests/test_espn_parse.py`

**Interfaces:**
- Produces in `app.ingest.records`:
  - `LineRecord(kind, spread_home, spread_home_price, spread_away_price, total, over_price, under_price, ml_home, ml_away)` with method `.is_empty() -> bool`.
  - `GameRecord(espn_id, season, season_type, week, start_time, status, home_name, away_name, home_score, away_score, neutral, venue_espn_id, venue_name, venue_indoor, lines: tuple[LineRecord, ...])`.
- Produces in `app.ingest.espn`:
  - `parse_american(value) -> Optional[int]`
  - `parse_line(value) -> Optional[float]`
  - `parse_scoreboard(payload) -> list[GameRecord]`
  - `parse_pickcenter(summary) -> tuple[LineRecord, ...]`
  - `fetch_scoreboard(http, sport, season=None, season_type=None, week=None) -> dict` (async)
  - `fetch_summary(http, sport, espn_id) -> dict` (async)
- Value conventions:
  - `status` is one of `scheduled|in_progress|final|postponed|canceled`.
  - `season_type` is one of `PRE|REG|POST`.
  - Line `kind` is one of `open|current|close`.

- [ ] **Step 1: Add the fixtures**

These are trimmed from live ESPN payloads captured 2026-10-11. The third event is a constructed neutral-site in-progress game, and the fourth a postponed one.

```json
// backend/tests/fixtures/espn/scoreboard_week6.json  (remove this comment line; JSON has no comments)
{
  "season": {"type": 2, "year": 2026},
  "week": {"number": 6},
  "events": [
    {
      "id": "401872995",
      "date": "2026-10-16T00:15Z",
      "season": {"year": 2026, "type": 2},
      "week": {"number": 6},
      "competitions": [{
        "neutralSite": false,
        "venue": {"id": "3937", "fullName": "Empower Field at Mile High", "indoor": false},
        "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": false}},
        "competitors": [
          {"homeAway": "home", "score": "0", "team": {"id": "7", "abbreviation": "DEN", "displayName": "Denver Broncos"}},
          {"homeAway": "away", "score": "0", "team": {"id": "26", "abbreviation": "SEA", "displayName": "Seattle Seahawks"}}
        ],
        "odds": [{
          "provider": {"id": "100", "name": "DraftKings"},
          "spread": 1.5,
          "overUnder": 42.5,
          "pointSpread": {
            "home": {"close": {"line": "+1.5", "odds": "-110"}, "open": {"line": "+2.5", "odds": "-115"}},
            "away": {"close": {"line": "-1.5", "odds": "-110"}, "open": {"line": "-2.5", "odds": "-105"}}
          },
          "total": {
            "over": {"close": {"line": "o42.5", "odds": "-110"}, "open": {"line": "o43.5", "odds": "-110"}},
            "under": {"close": {"line": "u42.5", "odds": "-110"}, "open": {"line": "u43.5", "odds": "-110"}}
          },
          "moneyline": {
            "home": {"close": {"odds": "+102"}, "open": {"odds": "+110"}},
            "away": {"close": {"odds": "-122"}, "open": {"odds": "-130"}}
          }
        }]
      }]
    },
    {
      "id": "401872964",
      "date": "2026-10-04T17:00Z",
      "season": {"year": 2026, "type": 2},
      "week": {"number": 4},
      "competitions": [{
        "neutralSite": false,
        "venue": {"id": "3679", "fullName": "Huntington Bank Field", "indoor": false},
        "status": {"type": {"name": "STATUS_FINAL", "state": "post", "completed": true}},
        "competitors": [
          {"homeAway": "home", "score": "27", "team": {"id": "5", "abbreviation": "CLE", "displayName": "Cleveland Browns"}},
          {"homeAway": "away", "score": "24", "team": {"id": "23", "abbreviation": "PIT", "displayName": "Pittsburgh Steelers"}}
        ]
      }]
    },
    {
      "id": "401872981",
      "date": "2026-10-11T13:30Z",
      "season": {"year": 2026, "type": 2},
      "week": {"number": 6},
      "competitions": [{
        "neutralSite": true,
        "venue": {"id": "2227", "fullName": "Wembley Stadium", "indoor": false},
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in", "completed": false}},
        "competitors": [
          {"homeAway": "home", "score": "7", "team": {"id": "30", "abbreviation": "JAX", "displayName": "Jacksonville Jaguars"}},
          {"homeAway": "away", "score": "3", "team": {"id": "21", "abbreviation": "PHI", "displayName": "Philadelphia Eagles"}}
        ],
        "odds": [{
          "provider": {"id": "100", "name": "DraftKings"},
          "pointSpread": {
            "home": {"close": {"line": "-8.5", "odds": "-105"}, "open": {"line": "+2.5", "odds": "-115"}},
            "away": {"close": {"line": "+8.5", "odds": "-115"}, "open": {"line": "-2.5", "odds": "-105"}}
          },
          "total": {
            "over": {"close": {"line": "o41.5", "odds": "-108"}, "open": {"line": "o44.5", "odds": "-102"}},
            "under": {"close": {"line": "u41.5", "odds": "-112"}, "open": {"line": "u44.5", "odds": "-118"}}
          },
          "moneyline": {
            "home": {"close": {"odds": "-425"}, "open": {"odds": "+110"}},
            "away": {"close": {"odds": "+330"}, "open": {"odds": "-130"}}
          }
        }]
      }]
    },
    {
      "id": "401872999",
      "date": "2026-10-18T17:00Z",
      "season": {"year": 2026, "type": 2},
      "week": {"number": 6},
      "competitions": [{
        "neutralSite": false,
        "venue": {"id": "3948", "fullName": "Hard Rock Stadium", "indoor": false},
        "status": {"type": {"name": "STATUS_POSTPONED", "state": "post", "completed": false}},
        "competitors": [
          {"homeAway": "home", "score": "0", "team": {"id": "15", "abbreviation": "MIA", "displayName": "Miami Dolphins"}},
          {"homeAway": "away", "score": "0", "team": {"id": "2", "abbreviation": "BUF", "displayName": "Buffalo Bills"}}
        ]
      }]
    }
  ]
}
```

```json
// backend/tests/fixtures/espn/summary_final.json  (remove this comment line)
{
  "header": {"id": "401872964"},
  "pickcenter": [{
    "provider": {"id": "100", "name": "DraftKings"},
    "spread": 2.5,
    "overUnder": 38.5,
    "pointSpread": {
      "home": {"close": {"line": "+2.5", "odds": "+100"}, "open": {"line": "+2.5", "odds": "-110"}},
      "away": {"close": {"line": "-2.5", "odds": "-120"}, "open": {"line": "-2.5", "odds": "-110"}}
    },
    "total": {
      "over": {"close": {"line": "o38.5", "odds": "-105"}, "open": {"line": "o40.5", "odds": "-110"}},
      "under": {"close": {"line": "u38.5", "odds": "-115"}, "open": {"line": "u40.5", "odds": "-110"}}
    },
    "moneyline": {
      "home": {"close": {"odds": "+124"}, "open": {"odds": "+114"}},
      "away": {"close": {"odds": "-148"}, "open": {"odds": "-135"}}
    }
  }]
}
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_espn_parse.py
from datetime import datetime, timezone

import pytest

from app.ingest.espn import parse_american, parse_line, parse_pickcenter, parse_scoreboard
from tests.matchup_helpers import load_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+102", 102), ("-110", -110), ("EVEN", 100), ("", None), (None, None), ("OFF", None)],
)
def test_parse_american(raw, expected) -> None:
    assert parse_american(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+1.5", 1.5), ("-8.5", -8.5), ("o42.5", 42.5), ("u42.5", 42.5), ("PK", 0.0), (None, None)],
)
def test_parse_line(raw, expected) -> None:
    assert parse_line(raw) == expected


def _by_id():
    return {g.espn_id: g for g in parse_scoreboard(load_json("espn/scoreboard_week6.json"))}


def test_scheduled_game_maps_teams_time_venue_and_season() -> None:
    game = _by_id()["401872995"]
    assert (game.home_name, game.away_name) == ("DEN", "SEA")
    assert game.start_time == datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)
    assert (game.season, game.season_type, game.week) == (2026, "REG", 6)
    assert game.status == "scheduled"
    assert (game.home_score, game.away_score) == (None, None)
    assert (game.venue_espn_id, game.venue_indoor, game.neutral) == ("3937", False, False)


def test_pre_game_close_is_stored_as_current_not_close() -> None:
    lines = {line.kind: line for line in _by_id()["401872995"].lines}
    assert set(lines) == {"open", "current"}
    assert lines["open"].spread_home == 2.5
    assert (lines["open"].spread_home_price, lines["open"].spread_away_price) == (-115, -105)
    assert (lines["open"].total, lines["open"].ml_home, lines["open"].ml_away) == (43.5, 110, -130)
    assert (lines["current"].spread_home, lines["current"].total) == (1.5, 42.5)
    assert (lines["current"].ml_home, lines["current"].ml_away) == (102, -122)


def test_started_game_close_is_close() -> None:
    game = _by_id()["401872981"]
    assert game.status == "in_progress"
    assert (game.home_score, game.away_score) == (7, 3)
    assert game.neutral is True
    lines = {line.kind: line for line in game.lines}
    assert set(lines) == {"open", "close"}
    assert lines["close"].spread_home == -8.5


def test_final_game_without_odds_has_scores_and_no_lines() -> None:
    game = _by_id()["401872964"]
    assert game.status == "final"
    assert (game.home_score, game.away_score) == (27, 24)
    assert game.lines == ()


def test_postponed_game_is_not_final_and_has_no_scores() -> None:
    game = _by_id()["401872999"]
    assert game.status == "postponed"
    assert (game.home_score, game.away_score) == (None, None)


def test_pickcenter_gives_open_and_true_close() -> None:
    lines = {line.kind: line for line in parse_pickcenter(load_json("espn/summary_final.json"))}
    assert set(lines) == {"open", "close"}
    close = lines["close"]
    assert (close.spread_home, close.spread_home_price, close.spread_away_price) == (2.5, 100, -120)
    assert (close.total, close.over_price, close.under_price) == (38.5, -105, -115)
    assert (close.ml_home, close.ml_away) == (124, -148)
    assert (lines["open"].total, lines["open"].ml_home) == (40.5, 114)


def test_pickcenter_missing_returns_nothing() -> None:
    assert parse_pickcenter({"header": {}}) == ()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest backend/tests/test_espn_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.espn'`.

- [ ] **Step 4: Implement the records**

```python
# backend/app/ingest/records.py
"""Source-neutral game and line records that every ingest parser produces."""

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class LineRecord:
    kind: str  # open | current | close
    spread_home: Optional[float] = None  # home team's line; negative = home favored
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self) if f.name != "kind")


@dataclass(frozen=True)
class GameRecord:
    espn_id: str
    season: int
    season_type: str  # PRE | REG | POST
    week: Optional[int]
    start_time: datetime
    status: str  # scheduled | in_progress | final | postponed | canceled
    home_name: str  # any alias (abbr, full name); resolved via team_aliases
    away_name: str
    home_score: Optional[int]
    away_score: Optional[int]
    neutral: bool
    venue_espn_id: Optional[str] = None
    venue_name: Optional[str] = None
    venue_indoor: Optional[bool] = None
    lines: tuple[LineRecord, ...] = ()
```

- [ ] **Step 5: Implement the ESPN parsing and fetching**

```python
# backend/app/ingest/espn.py
"""ESPN public JSON: schedule, scores, and one book's (DraftKings) open/current/close lines.

Verified 2026-10-11:
- The scoreboard's odds "close" is the live line until kickoff, so it is stored as "current"
  while a game is "pre".
- The scoreboard drops odds once a game is final; the game summary's pickcenter keeps open and
  close in the same shape.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Optional

import httpx

from app.ingest.records import GameRecord, LineRecord

SITE_API = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUE_PATHS = {"nfl": "football/nfl"}
SEASON_TYPES = {1: "PRE", 2: "REG", 3: "POST"}
_STATES = {"pre": "scheduled", "in": "in_progress", "post": "final"}
_SPECIAL_STATUSES = {"STATUS_POSTPONED": "postponed", "STATUS_CANCELED": "canceled"}


def parse_american(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"EVEN", "EV"}:
        return 100
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_line(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().lower().lstrip("ou")
    if text in {"pk", "even"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def _quote(market: Mapping, side: str, kind: str) -> Mapping:
    return ((market or {}).get(side) or {}).get(kind) or {}


def _line(odds: Mapping, espn_kind: str, kind: str) -> Optional[LineRecord]:
    spread = odds.get("pointSpread") or {}
    total = odds.get("total") or {}
    money = odds.get("moneyline") or {}
    record = LineRecord(
        kind=kind,
        spread_home=parse_line(_quote(spread, "home", espn_kind).get("line")),
        spread_home_price=parse_american(_quote(spread, "home", espn_kind).get("odds")),
        spread_away_price=parse_american(_quote(spread, "away", espn_kind).get("odds")),
        total=parse_line(_quote(total, "over", espn_kind).get("line")),
        over_price=parse_american(_quote(total, "over", espn_kind).get("odds")),
        under_price=parse_american(_quote(total, "under", espn_kind).get("odds")),
        ml_home=parse_american(_quote(money, "home", espn_kind).get("odds")),
        ml_away=parse_american(_quote(money, "away", espn_kind).get("odds")),
    )
    return None if record.is_empty() else record


def parse_odds(odds: Mapping, game_started: bool) -> tuple[LineRecord, ...]:
    lines = (
        _line(odds, "open", "open"),
        _line(odds, "close", "close" if game_started else "current"),
    )
    return tuple(line for line in lines if line is not None)


def _status(status_type: Mapping) -> str:
    name = status_type.get("name", "")
    if name in _SPECIAL_STATUSES:
        return _SPECIAL_STATUSES[name]
    return _STATES.get(status_type.get("state", ""), "scheduled")


def _score(value: Any, started: bool) -> Optional[int]:
    if not started or value in (None, ""):
        return None
    return int(float(value))


def parse_start_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_scoreboard(payload: Mapping) -> list[GameRecord]:
    games = []
    for event in payload.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        status_type = (competition.get("status") or event.get("status") or {}).get("type") or {}
        status = _status(status_type)
        started = status in {"in_progress", "final"}
        sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        season = event.get("season") or {}
        venue = competition.get("venue") or {}
        odds = (competition.get("odds") or [None])[0]
        games.append(
            GameRecord(
                espn_id=str(event["id"]),
                season=int(season["year"]),
                season_type=SEASON_TYPES.get(season.get("type"), "REG"),
                week=(event.get("week") or {}).get("number"),
                start_time=parse_start_time(event["date"]),
                status=status,
                home_name=home["team"]["abbreviation"],
                away_name=away["team"]["abbreviation"],
                home_score=_score(home.get("score"), started),
                away_score=_score(away.get("score"), started),
                neutral=bool(competition.get("neutralSite")),
                venue_espn_id=str(venue["id"]) if venue.get("id") else None,
                venue_name=venue.get("fullName"),
                venue_indoor=venue.get("indoor"),
                lines=parse_odds(odds, started) if odds else (),
            )
        )
    return games


def parse_pickcenter(summary: Mapping) -> tuple[LineRecord, ...]:
    picks = summary.get("pickcenter") or []
    return parse_odds(picks[0], game_started=True) if picks else ()


async def fetch_scoreboard(
    http: httpx.AsyncClient,
    sport: str,
    season: Optional[int] = None,
    season_type: Optional[int] = None,
    week: Optional[int] = None,
) -> dict:
    params = {}
    if season is not None:
        params["dates"] = str(season)
    if season_type is not None:
        params["seasontype"] = str(season_type)
    if week is not None:
        params["week"] = str(week)
    response = await http.get(f"{SITE_API}/{LEAGUE_PATHS[sport]}/scoreboard", params=params)
    response.raise_for_status()
    return response.json()


async def fetch_summary(http: httpx.AsyncClient, sport: str, espn_id: str) -> dict:
    response = await http.get(
        f"{SITE_API}/{LEAGUE_PATHS[sport]}/summary", params={"event": espn_id}
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/tests/test_espn_parse.py -v && ruff check backend`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ingest/records.py backend/app/ingest/espn.py backend/tests/fixtures/espn backend/tests/test_espn_parse.py
git commit -m "feat: ESPN scoreboard/pickcenter parsing into source-neutral records"
```

---

### Task 6: Games repository (upsert, closes backlog, event linking)

**Files:**
- Create: `backend/app/storage/games_repository.py`
- Test: `backend/tests/test_games_repository.py`

**Interfaces:**
- Consumes: Task 3 models, Task 4 `seed_teams`, Task 5 `GameRecord`/`LineRecord`/`parse_scoreboard`, and `app.identity.normalize_name`.
- Produces in `app.storage.games_repository`:
  - `aware(value: datetime) -> datetime`, which treats a naive value as UTC. SQLite returns naive datetimes.
  - `UpsertResult(games: int, lines: int, skipped: list[str])`
  - `LinkResult(linked: int, unlinked: list[str])`
  - `GamesRepository(session_factory)` with these async methods:
    - `upsert_games(sport, source, records, captured_at) -> UpsertResult`
    - `save_lines(espn_id, source, lines, captured_at) -> bool`
    - `finals_missing_close(sport, since) -> list[str]`
    - `link_events(sport, max_gap=timedelta(hours=36)) -> LinkResult`
- Rules:
  - An `open` line is insert-if-absent. `current` and `close` overwrite.
  - `source="espn"` always sets status. Other sources set status only for new games or a `final`.
  - Scores overwrite only when they are not None.
  - `venue_id` is only replaced by a non-null venue.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_games_repository.py
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ingest.espn import parse_pickcenter, parse_scoreboard
from app.ingest.records import GameRecord, LineRecord
from app.ingest.seed import seed_teams
from app.storage.games_repository import GamesRepository
from app.storage.models import EventRecord, Game, GameLine, Team, Venue
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_games_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.storage.games_repository'`.

- [ ] **Step 3: Implement**

```python
# backend/app/storage/games_repository.py
"""Upsert source-neutral game records, track missing closes, and link odds events to games."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity import normalize_name
from app.ingest.records import GameRecord, LineRecord
from app.storage.models import EventRecord, Game, GameLine, Team, TeamAlias, Venue

LINE_FIELDS = (
    "spread_home",
    "spread_home_price",
    "spread_away_price",
    "total",
    "over_price",
    "under_price",
    "ml_home",
    "ml_away",
)


def aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything we store is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class UpsertResult:
    games: int = 0
    lines: int = 0
    skipped: list[str] = field(default_factory=list)


@dataclass
class LinkResult:
    linked: int = 0
    unlinked: list[str] = field(default_factory=list)


async def _alias_map(session: AsyncSession, sport: str) -> dict[str, int]:
    rows = await session.execute(
        select(TeamAlias.alias, TeamAlias.team_id).where(TeamAlias.sport == sport)
    )
    return dict(rows.all())


async def _upsert_line(
    session: AsyncSession, game_id: int, source: str, line: LineRecord, captured_at: datetime
) -> None:
    row = await session.scalar(
        select(GameLine).where(
            GameLine.game_id == game_id, GameLine.source == source, GameLine.kind == line.kind
        )
    )
    if row is not None and line.kind == "open":
        return  # the opener never changes; keep the first one seen
    if row is None:
        row = GameLine(game_id=game_id, source=source, kind=line.kind)
        session.add(row)
    for name in LINE_FIELDS:
        setattr(row, name, getattr(line, name))
    row.captured_at = captured_at


class GamesRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def upsert_games(
        self, sport: str, source: str, records: Sequence[GameRecord], captured_at: datetime
    ) -> UpsertResult:
        result = UpsertResult()
        async with self.session_factory() as session, session.begin():
            aliases = await _alias_map(session, sport)
            home_venues = dict(
                (await session.execute(select(Team.id, Team.venue_id).where(Team.sport == sport)))
                .all()
            )
            games = {
                g.espn_id: g
                for g in (await session.scalars(select(Game).where(Game.sport == sport))).all()
            }
            venues = {
                v.espn_id: v
                for v in (await session.scalars(select(Venue).where(Venue.espn_id.is_not(None))))
                .all()
            }
            for record in records:
                home_id = aliases.get(normalize_name(record.home_name))
                away_id = aliases.get(normalize_name(record.away_name))
                if home_id is None or away_id is None:
                    result.skipped.append(record.espn_id)
                    continue
                venue_id = await self._venue_id(session, record, venues, home_venues.get(home_id))
                game = games.get(record.espn_id)
                is_new = game is None
                if game is None:
                    game = Game(espn_id=record.espn_id, sport=sport)
                    session.add(game)
                    games[record.espn_id] = game
                game.season = record.season
                game.season_type = record.season_type
                game.week = record.week
                game.start_time = record.start_time
                game.home_team_id = home_id
                game.away_team_id = away_id
                game.neutral = record.neutral
                if venue_id is not None or is_new:
                    game.venue_id = venue_id
                if is_new or source == "espn" or record.status == "final":
                    game.status = record.status
                if record.home_score is not None:
                    game.home_score = record.home_score
                if record.away_score is not None:
                    game.away_score = record.away_score
                game.updated_at = captured_at
                await session.flush()
                for line in record.lines:
                    await _upsert_line(session, game.id, source, line, captured_at)
                    result.lines += 1
                result.games += 1
        return result

    @staticmethod
    async def _venue_id(
        session: AsyncSession,
        record: GameRecord,
        venues: dict[str, Venue],
        home_venue_id: Optional[int],
    ) -> Optional[int]:
        if record.venue_espn_id:
            venue = venues.get(record.venue_espn_id)
            if venue is None:
                roof = None
                if record.venue_indoor is not None:
                    roof = "dome" if record.venue_indoor else "outdoors"
                venue = Venue(
                    espn_id=record.venue_espn_id,
                    name=record.venue_name or "Unknown venue",
                    roof=roof,
                )
                session.add(venue)
                await session.flush()
                venues[record.venue_espn_id] = venue
            return venue.id
        return None if record.neutral else home_venue_id

    async def save_lines(
        self, espn_id: str, source: str, lines: Sequence[LineRecord], captured_at: datetime
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            game_id = await session.scalar(select(Game.id).where(Game.espn_id == espn_id))
            if game_id is None:
                return False
            for line in lines:
                await _upsert_line(session, game_id, source, line, captured_at)
        return True

    async def finals_missing_close(self, sport: str, since: datetime) -> list[str]:
        has_close = select(GameLine.game_id).where(
            GameLine.source == "espn", GameLine.kind == "close"
        )
        async with self.session_factory() as session:
            rows = await session.scalars(
                select(Game.espn_id)
                .where(
                    Game.sport == sport,
                    Game.status == "final",
                    Game.start_time >= since,
                    Game.id.not_in(has_close),
                )
                .order_by(Game.start_time)
            )
            return list(rows.all())

    async def link_events(
        self, sport: str, max_gap: timedelta = timedelta(hours=36)
    ) -> LinkResult:
        result = LinkResult()
        async with self.session_factory() as session, session.begin():
            aliases = await _alias_map(session, sport)
            linked = select(Game.event_id).where(Game.event_id.is_not(None))
            events = (
                await session.scalars(
                    select(EventRecord)
                    .where(EventRecord.sport == sport, EventRecord.id.not_in(linked))
                    .order_by(EventRecord.start_time)
                )
            ).all()
            for event in events:
                home_id = aliases.get(normalize_name(event.home_team))
                away_id = aliases.get(normalize_name(event.away_team))
                matches = []
                if home_id is not None and away_id is not None:
                    # Some feeds flip home/away for neutral-site games, so accept either order.
                    candidates = (
                        await session.scalars(
                            select(Game).where(
                                Game.sport == sport,
                                Game.event_id.is_(None),
                                (
                                    (Game.home_team_id == home_id) & (Game.away_team_id == away_id)
                                )
                                | (
                                    (Game.home_team_id == away_id) & (Game.away_team_id == home_id)
                                ),
                            )
                        )
                    ).all()
                    matches = [
                        game
                        for game in candidates
                        if abs(aware(game.start_time) - aware(event.start_time)) <= max_gap
                    ]
                if len(matches) == 1:
                    matches[0].event_id = event.id
                    result.linked += 1
                else:
                    result.unlinked.append(event.id)
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest backend/tests/test_games_repository.py -v && ruff check backend`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/games_repository.py backend/tests/test_games_repository.py
git commit -m "feat: games repository (alias-resolved upsert, opener lock, closes backlog, event linking)"
```

---

### Task 7: nflverse history parser

**Files:**
- Create: `backend/app/ingest/nflverse.py`
- Test: `backend/tests/test_nflverse.py`

**Interfaces:**
- Consumes: `GameRecord` and `LineRecord` (Task 5).
- Produces: `GAMES_CSV_URL: str` and `parse_games_csv(text: str, min_season: int = 2015) -> list[GameRecord]`.
- Behavior:
  - nflverse `gameday`/`gametime` are US Eastern and are converted to UTC.
  - `spread_home = -spread_line`.
  - Lines are kind `close` and are emitted only for final games.
  - Postseason types map as WC→1, DIV→2, CON→3, SB→5, matching ESPN's postseason week numbers.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nflverse.py
from datetime import datetime, timezone

from app.ingest.nflverse import parse_games_csv

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,"
    "stadium_id,stadium"
)


def _row(**overrides) -> str:
    base = {
        "game_id": "2025_01_SEA_DEN", "season": "2025", "game_type": "REG", "week": "1",
        "gameday": "2025-09-07", "weekday": "Sunday", "gametime": "16:25", "away_team": "SEA",
        "away_score": "20", "home_team": "DEN", "home_score": "17", "location": "Home",
        "result": "-3", "total": "37", "overtime": "0", "old_game_id": "", "gsis": "",
        "nfl_detail_id": "", "pfr": "", "pff": "", "espn": "401700001", "ftn": "",
        "away_rest": "7", "home_rest": "7", "away_moneyline": "-130", "home_moneyline": "110",
        "spread_line": "-2.5", "away_spread_odds": "-105", "home_spread_odds": "-115",
        "total_line": "43.5", "under_odds": "-110", "over_odds": "-110", "div_game": "0",
        "roof": "outdoors", "surface": "grass", "temp": "", "wind": "", "away_qb_id": "",
        "home_qb_id": "", "away_qb_name": "", "home_qb_name": "", "away_coach": "",
        "home_coach": "", "referee": "", "stadium_id": "DEN00", "stadium": "Empower Field",
    }
    base.update(overrides)
    return ",".join(base[c] for c in HEADER.split(","))


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_final_regular_season_game_converts_time_spread_and_prices() -> None:
    [game] = parse_games_csv(_csv(_row()))
    assert game.espn_id == "401700001"
    assert (game.season, game.season_type, game.week) == (2025, "REG", 1)
    assert game.start_time == datetime(2025, 9, 7, 20, 25, tzinfo=timezone.utc)  # 16:25 EDT
    assert (game.home_name, game.away_name, game.home_score, game.away_score) == ("DEN", "SEA", 17, 20)
    assert game.status == "final" and game.neutral is False
    [close] = game.lines
    assert close.kind == "close"
    assert close.spread_home == 2.5  # nflverse -2.5 = home dog by 2.5
    assert (close.spread_home_price, close.spread_away_price) == (-115, -105)
    assert (close.total, close.over_price, close.under_price) == (43.5, -110, -110)
    assert (close.ml_home, close.ml_away) == (110, -130)


def test_postseason_week_mapping_and_neutral_super_bowl() -> None:
    [sb] = parse_games_csv(_csv(_row(game_type="SB", week="22", location="Neutral")))
    assert (sb.season_type, sb.week, sb.neutral) == ("POST", 5, True)


def test_future_game_has_no_scores_or_lines() -> None:
    [game] = parse_games_csv(_csv(_row(away_score="", home_score="", result="", total="")))
    assert game.status == "scheduled"
    assert (game.home_score, game.away_score, game.lines) == (None, None, ())


def test_rows_without_espn_id_or_before_min_season_are_skipped() -> None:
    rows = _csv(_row(espn=""), _row(espn="NA"), _row(season="2014", espn="1"), _row(espn="7"))
    assert [g.espn_id for g in parse_games_csv(rows)] == ["7"]


def test_missing_gametime_defaults_to_1pm_eastern() -> None:
    [game] = parse_games_csv(_csv(_row(gametime="")))
    assert game.start_time == datetime(2025, 9, 7, 17, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_nflverse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.nflverse'`.

- [ ] **Step 3: Implement**

```python
# backend/app/ingest/nflverse.py
"""nflverse schedules (games.csv, CC-BY-4.0): NFL history with closing lines since 1999.

nflverse spread_line is positive when the HOME team is favored, so spread_home = -spread_line.
Its lines are an unverified close (no timestamp), so they are only attached to final games.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.ingest.records import GameRecord, LineRecord

GAMES_CSV_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
POST_WEEKS = {"WC": 1, "DIV": 2, "CON": 3, "SB": 5}  # matches ESPN's postseason week numbers
EASTERN = ZoneInfo("America/New_York")


def _blank(value: Optional[str]) -> bool:
    return value is None or value.strip() in {"", "NA"}


def _int(value: Optional[str]) -> Optional[int]:
    return None if _blank(value) else int(float(value))


def _float(value: Optional[str]) -> Optional[float]:
    return None if _blank(value) else float(value)


def parse_games_csv(text: str, min_season: int = 2015) -> list[GameRecord]:
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        season = int(row["season"])
        game_type = row["game_type"]
        week = int(row["week"]) if game_type == "REG" else POST_WEEKS.get(game_type)
        if season < min_season or _blank(row.get("espn")) or week is None:
            continue
        kickoff = datetime.strptime(
            f"{row['gameday']} {row['gametime'] or '13:00'}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=EASTERN)
        home_score, away_score = _int(row["home_score"]), _int(row["away_score"])
        final = home_score is not None and away_score is not None
        spread_line = _float(row["spread_line"])
        close = LineRecord(
            kind="close",
            spread_home=None if spread_line is None else -spread_line + 0.0,
            spread_home_price=_int(row["home_spread_odds"]),
            spread_away_price=_int(row["away_spread_odds"]),
            total=_float(row["total_line"]),
            over_price=_int(row["over_odds"]),
            under_price=_int(row["under_odds"]),
            ml_home=_int(row["home_moneyline"]),
            ml_away=_int(row["away_moneyline"]),
        )
        records.append(
            GameRecord(
                espn_id=row["espn"].strip(),
                season=season,
                season_type="REG" if game_type == "REG" else "POST",
                week=week,
                start_time=kickoff.astimezone(timezone.utc),
                status="final" if final else "scheduled",
                home_name=row["home_team"],
                away_name=row["away_team"],
                home_score=home_score,
                away_score=away_score,
                neutral=row["location"] == "Neutral",
                venue_name=row.get("stadium") or None,
                lines=(close,) if final and not close.is_empty() else (),
            )
        )
    return records
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest backend/tests/test_nflverse.py -v && ruff check backend`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/nflverse.py backend/tests/test_nflverse.py
git commit -m "feat: nflverse games.csv parser (history + unverified closing lines)"
```

---

### Task 8: Job slots and the run-due runner

**Files:**
- Create: `backend/app/jobs/__init__.py` (empty)
- Create: `backend/app/jobs/slots.py`
- Create: `backend/app/jobs/runner.py`
- Test: `backend/tests/test_job_runner.py`

**Interfaces:**
- Consumes: `JobSlot` and `ProviderRefreshRun` (models).
- Produces in `app.jobs.slots`:
  - `CENTRAL = ZoneInfo("America/Chicago")`
  - `TERMINAL_STATES: frozenset[str]`
  - `Slot(key: str, window_start: datetime, window_end: datetime)`
  - `hourly_slots(job: str, now: datetime, first_hour: int = 8, last_hour: int = 23) -> list[Slot]`, which covers today's hours that have already started.
  - `daily_slot(job: str, now: datetime, start_hour: int = 8, weekday: Optional[int] = None) -> list[Slot]`
- Produces in `app.jobs.runner`:
  - `JobContext(session_factory, http: httpx.AsyncClient, raw_dir: Path)`
  - The `Job` Protocol, with attributes `name: str` and `max_attempts: int`, a method `slots(now) -> list[Slot]`, and an async `run(ctx, slot) -> str` that returns a state.
  - `run_due(jobs, ctx, now) -> dict[str, str]`, which maps slot key to new state.
  - `run_once(job, ctx) -> str`
  - `HEARTBEAT_PROVIDER = "run_due"`
- States:
  - Non-terminal: `pending`, `raw_saved`, `partial`.
  - Terminal: `done`, `empty`, `missed`, `skipped_quota`, `failed`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_job_runner.py
import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.jobs.runner import HEARTBEAT_PROVIDER, JobContext, run_due
from app.jobs.slots import CENTRAL, Slot, daily_slot, hourly_slots
from app.storage.models import JobSlot, ProviderRefreshRun
from tests.matchup_helpers import new_session_factory

# 10:30 CT on a Sunday (CDT, UTC-5).
NOW = datetime(2026, 10, 11, 10, 30, tzinfo=CENTRAL).astimezone(timezone.utc)


class FakeJob:
    def __init__(self, outcomes, max_attempts=3, slots=None):
        self.name = "fake"
        self.max_attempts = max_attempts
        self.outcomes = list(outcomes)
        self.calls = 0
        self._slots = slots

    def slots(self, now):
        if self._slots is not None:
            return self._slots
        return [Slot("fake:now", now.replace(minute=0), now.replace(minute=59))]

    async def run(self, ctx, slot):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _ctx(tmp_path):
    factory = await new_session_factory(tmp_path)
    return JobContext(factory, httpx.AsyncClient(), tmp_path)


async def _slot(ctx, key):
    async with ctx.session_factory() as session:
        return await session.scalar(select(JobSlot).where(JobSlot.slot_key == key))


def test_hourly_slots_cover_started_hours_in_central_time() -> None:
    slots = hourly_slots("espn_scoreboard", NOW)
    assert [s.key for s in slots] == [
        "espn_scoreboard:2026-10-11T08", "espn_scoreboard:2026-10-11T09",
        "espn_scoreboard:2026-10-11T10",
    ]
    assert slots[-1].window_start == datetime(2026, 10, 11, 10, tzinfo=CENTRAL)
    assert slots[-1].window_end == datetime(2026, 10, 11, 11, tzinfo=CENTRAL)


def test_hourly_slots_before_first_hour_is_empty() -> None:
    early = datetime(2026, 10, 11, 7, 59, tzinfo=CENTRAL)
    assert hourly_slots("x", early) == []


def test_daily_slot_respects_weekday() -> None:
    assert [s.key for s in daily_slot("nfl_history", NOW)] == ["nfl_history:2026-10-11"]
    assert daily_slot("nfl_history", NOW, weekday=1) == []  # Sunday is weekday 6


def test_done_slot_runs_once(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob(["done"])
        assert await run_due([job], ctx, NOW) == {"fake:now": "done"}
        assert await run_due([job], ctx, NOW) == {}
        assert job.calls == 1

    asyncio.run(scenario())


def test_exception_retries_then_fails_at_cap(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob([RuntimeError("espn down"), RuntimeError("espn down")], max_attempts=2)
        assert await run_due([job], ctx, NOW) == {"fake:now": "pending"}
        assert await run_due([job], ctx, NOW) == {"fake:now": "failed"}
        assert await run_due([job], ctx, NOW) == {}
        slot = await _slot(ctx, "fake:now")
        assert (slot.state, slot.attempts) == ("failed", 2)
        async with ctx.session_factory() as session:
            failures = (await session.scalars(select(ProviderRefreshRun).where(
                ProviderRefreshRun.provider == "job:fake", ProviderRefreshRun.status == "failed"
            ))).all()
        assert len(failures) == 2 and failures[0].error_type == "RuntimeError"

    asyncio.run(scenario())


def test_partial_retries_until_cap(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        job = FakeJob(["partial", "partial"], max_attempts=2)
        await run_due([job], ctx, NOW)
        assert (await _slot(ctx, "fake:now")).state == "partial"
        await run_due([job], ctx, NOW)
        assert (await _slot(ctx, "fake:now")).state == "failed"

    asyncio.run(scenario())


def test_past_window_is_missed_exactly_once_without_running(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        past = Slot("fake:08", datetime(2026, 10, 11, 8, tzinfo=CENTRAL),
                    datetime(2026, 10, 11, 9, tzinfo=CENTRAL))
        job = FakeJob([], slots=[past])
        assert await run_due([job], ctx, NOW) == {"fake:08": "missed"}
        assert await run_due([job], ctx, NOW) == {}
        assert job.calls == 0

    asyncio.run(scenario())


def test_heartbeat_recorded(tmp_path) -> None:
    async def scenario() -> None:
        ctx = await _ctx(tmp_path)
        await run_due([], ctx, NOW)
        async with ctx.session_factory() as session:
            beat = await session.scalar(select(ProviderRefreshRun).where(
                ProviderRefreshRun.provider == HEARTBEAT_PROVIDER
            ))
        assert beat is not None and beat.status == "succeeded"

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_job_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs'`.

- [ ] **Step 3: Implement the slots**

```python
# backend/app/jobs/slots.py
"""Scheduled slots in Central time. zoneinfo handles DST, so trigger times never need to."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
TERMINAL_STATES = frozenset({"done", "empty", "missed", "skipped_quota", "failed"})


@dataclass(frozen=True)
class Slot:
    key: str
    window_start: datetime
    window_end: datetime


def hourly_slots(job: str, now: datetime, first_hour: int = 8, last_hour: int = 23) -> list[Slot]:
    day = now.astimezone(CENTRAL).date()
    slots = []
    for hour in range(first_hour, last_hour + 1):
        start = datetime.combine(day, time(hour), CENTRAL)
        if start > now:
            break
        slots.append(Slot(f"{job}:{day.isoformat()}T{hour:02d}", start, start + timedelta(hours=1)))
    return slots


def daily_slot(
    job: str, now: datetime, start_hour: int = 8, weekday: Optional[int] = None
) -> list[Slot]:
    day = now.astimezone(CENTRAL).date()
    if weekday is not None and day.weekday() != weekday:
        return []
    start = datetime.combine(day, time(start_hour), CENTRAL)
    end = datetime.combine(day + timedelta(days=1), time(0), CENTRAL)
    return [Slot(f"{job}:{day.isoformat()}", start, end)] if now >= start else []
```

- [ ] **Step 4: Implement the runner**

```python
# backend/app/jobs/runner.py
"""Run each job's due slots once. Attempts are logged; every slot has one current state."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.jobs.slots import TERMINAL_STATES, Slot
from app.storage.models import JobSlot, ProviderRefreshRun

logger = logging.getLogger(__name__)
HEARTBEAT_PROVIDER = "run_due"


@dataclass
class JobContext:
    session_factory: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    raw_dir: Path


class Job(Protocol):
    name: str
    max_attempts: int

    def slots(self, now: datetime) -> list[Slot]: ...

    async def run(self, ctx: JobContext, slot: Slot) -> str: ...


async def _log_attempt(
    ctx: JobContext, provider: str, started_at: datetime, error: Optional[BaseException] = None
) -> None:
    async with ctx.session_factory() as session, session.begin():
        session.add(
            ProviderRefreshRun(
                provider=provider,
                status="failed" if error else "succeeded",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                error_type=type(error).__name__ if error else None,
                error_message=str(error)[:500] if error else None,
            )
        )


async def _slot_state(ctx: JobContext, job: str, key: str) -> tuple[str, int]:
    async with ctx.session_factory() as session:
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
    return (row.state, row.attempts) if row else ("pending", 0)


async def _save_slot(ctx: JobContext, job: str, key: str, state: str, attempts: int) -> None:
    async with ctx.session_factory() as session, session.begin():
        row = await session.scalar(
            select(JobSlot).where(JobSlot.job == job, JobSlot.slot_key == key)
        )
        if row is None:
            row = JobSlot(job=job, slot_key=key)
            session.add(row)
        row.state = state
        row.attempts = attempts
        row.updated_at = datetime.now(timezone.utc)


async def run_due(jobs: Sequence[Job], ctx: JobContext, now: datetime) -> dict[str, str]:
    results: dict[str, str] = {}
    for job in jobs:
        for slot in job.slots(now):
            if now < slot.window_start:
                continue
            state, attempts = await _slot_state(ctx, job.name, slot.key)
            if state in TERMINAL_STATES:
                continue
            if now >= slot.window_end:
                await _save_slot(ctx, job.name, slot.key, "missed", attempts)
                results[slot.key] = "missed"
                continue
            started_at = datetime.now(timezone.utc)
            try:
                state = await job.run(ctx, slot)
                await _log_attempt(ctx, f"job:{job.name}", started_at)
            except Exception as error:  # one job's failure must not stop the others
                logger.exception("job %s slot %s failed", job.name, slot.key)
                await _log_attempt(ctx, f"job:{job.name}", started_at, error)
                state = "pending"
            attempts += 1
            if state not in TERMINAL_STATES and attempts >= job.max_attempts:
                state = "failed"
            await _save_slot(ctx, job.name, slot.key, state, attempts)
            results[slot.key] = state
    await _log_attempt(ctx, HEARTBEAT_PROVIDER, now)
    return results


async def run_once(job: Job, ctx: JobContext) -> str:
    """Manual run (python -m app.jobs <name>): no slot bookkeeping, attempt still logged."""
    started_at = datetime.now(timezone.utc)
    slot = Slot(f"{job.name}:manual:{started_at.isoformat()}", started_at, started_at)
    try:
        state = await job.run(ctx, slot)
    except Exception as error:
        await _log_attempt(ctx, f"job:{job.name}", started_at, error)
        raise
    await _log_attempt(ctx, f"job:{job.name}", started_at)
    return state
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/tests/test_job_runner.py -v && ruff check backend`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs backend/tests/test_job_runner.py
git commit -m "feat: idempotent job slots + run-due runner with attempt log and heartbeat"
```

---

### Task 9: NFL jobs + `python -m app.jobs` CLI

**Files:**
- Create: `backend/app/jobs/nfl.py`
- Create: `backend/app/jobs/__main__.py`
- Test: `backend/tests/test_nfl_jobs.py`

**Interfaces:**
- Consumes: `seed_teams` (Task 4), `fetch_scoreboard`/`fetch_summary`/`parse_scoreboard`/`parse_pickcenter` (Task 5), `GamesRepository` (Task 6), `GAMES_CSV_URL`/`parse_games_csv` (Task 7), and `hourly_slots`/`daily_slot`/`JobContext`/`run_due`/`run_once` (Task 8).
- Produces in `app.jobs.nfl`:
  - `SeedJob`, which has no slots and is run manually.
  - `NflHistoryJob`, Tuesdays.
  - `EspnScoreboardJob`, hourly 08–23 CT.
  - `EspnClosesJob`, daily.
  - `LinkEventsJob`, hourly.
  - `JOBS: list`, holding all five.
- Produces the CLI `python -m app.jobs {run-due|seed|nfl_history|espn_scoreboard|espn_closes|link_events}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nfl_jobs.py
import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.jobs.__main__ import main
from app.jobs.nfl import EspnClosesJob, EspnScoreboardJob, NflHistoryJob, SeedJob
from app.jobs.runner import JobContext
from app.jobs.slots import Slot
from app.storage.models import Game, GameLine
from tests.matchup_helpers import load_json, new_session_factory
from tests.test_nflverse import _csv, _row

SLOT = Slot("test", datetime(2026, 10, 11, tzinfo=timezone.utc),
            datetime(2026, 10, 12, tzinfo=timezone.utc))


def _transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/scoreboard"):
            if request.url.params.get("week") == "7":
                return httpx.Response(200, json={"events": []})
            return httpx.Response(200, json=load_json("espn/scoreboard_week6.json"))
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=load_json("espn/summary_final.json"))
        if request.url.path.endswith("games.csv"):
            return httpx.Response(200, text=_csv(_row(espn="401872964", home_team="CLE",
                                                      away_team="PIT", season="2026",
                                                      gameday="2026-10-04")))
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _ctx(tmp_path, calls):
    factory = await new_session_factory(tmp_path)
    ctx = JobContext(factory, httpx.AsyncClient(transport=_transport(calls)), tmp_path / "raw")
    await SeedJob().run(ctx, SLOT)
    return ctx


async def _count(ctx, model):
    async with ctx.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def test_scoreboard_job_fetches_current_and_next_week(tmp_path) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        assert await EspnScoreboardJob().run(ctx, SLOT) == "done"
        assert await _count(ctx, Game) == 4
        assert any("week=7" in url and "seasontype=2" in url and "dates=2026" in url
                   for url in calls)

    asyncio.run(scenario())


def test_closes_job_backfills_final_game(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        await EspnScoreboardJob().run(ctx, SLOT)
        monkeypatch.setattr("app.jobs.nfl._now",
                            lambda: datetime(2026, 10, 11, 15, tzinfo=timezone.utc))
        assert await EspnClosesJob().run(ctx, SLOT) == "done"
        async with ctx.session_factory() as session:
            close = await session.scalar(select(GameLine).where(GameLine.kind == "close",
                                                                GameLine.source == "espn",
                                                                GameLine.ml_home == 124))
        assert close is not None
        assert sum("summary" in url for url in calls) == 1
        assert await EspnClosesJob().run(ctx, SLOT) == "done"  # nothing left to fetch
        assert sum("summary" in url for url in calls) == 1

    asyncio.run(scenario())


def test_history_job_writes_raw_file_and_upserts(tmp_path) -> None:
    async def scenario() -> None:
        calls = []
        ctx = await _ctx(tmp_path, calls)
        assert await NflHistoryJob().run(ctx, SLOT) == "done"
        assert (tmp_path / "raw" / "nflverse" / "games.csv").exists()
        async with ctx.session_factory() as session:
            line = await session.scalar(select(GameLine).where(GameLine.source == "nflverse"))
        assert line.kind == "close" and line.spread_home == 2.5

    asyncio.run(scenario())


def test_cli_rejects_unknown_job() -> None:
    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(main(["not-a-job"]))
    assert excinfo.value.code == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_nfl_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs.nfl'`.

- [ ] **Step 3: Implement the jobs**

```python
# backend/app/jobs/nfl.py
"""Phase 1a NFL jobs. ESPN and nflverse are free, so these do not use raw-first slots."""

import logging
from datetime import datetime, timedelta, timezone

from app.ingest.espn import fetch_scoreboard, fetch_summary, parse_pickcenter, parse_scoreboard
from app.ingest.nflverse import GAMES_CSV_URL, parse_games_csv
from app.ingest.seed import seed_teams
from app.jobs.runner import JobContext
from app.jobs.slots import Slot, daily_slot, hourly_slots
from app.storage.games_repository import GamesRepository

logger = logging.getLogger(__name__)
NFL = "nfl"
CLOSE_LOOKBACK = timedelta(days=14)
TUESDAY = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SeedJob:
    name = "seed"
    max_attempts = 1

    def slots(self, now: datetime) -> list[Slot]:
        return []  # manual only: python -m app.jobs seed

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        await seed_teams(ctx.session_factory, NFL)
        return "done"


class NflHistoryJob:
    name = "nfl_history"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now, weekday=TUESDAY)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        response = await ctx.http.get(GAMES_CSV_URL)
        response.raise_for_status()
        raw = ctx.raw_dir / "nflverse" / "games.csv"  # latest copy only; the source keeps history
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(response.text, encoding="utf-8")
        records = parse_games_csv(response.text)
        if not records:
            return "empty"
        result = await GamesRepository(ctx.session_factory).upsert_games(
            NFL, "nflverse", records, _now()
        )
        logger.info("nfl_history: %s games, %s skipped", result.games, len(result.skipped))
        return "done"


class EspnScoreboardJob:
    name = "espn_scoreboard"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        current = await fetch_scoreboard(ctx.http, NFL)
        payloads = [current]
        season = current.get("season") or {}
        week = (current.get("week") or {}).get("number")
        if season.get("year") and week:
            payloads.append(
                await fetch_scoreboard(ctx.http, NFL, season["year"], season.get("type"), week + 1)
            )
        records = [game for payload in payloads for game in parse_scoreboard(payload)]
        if not records:
            return "empty"
        result = await GamesRepository(ctx.session_factory).upsert_games(
            NFL, "espn", records, _now()
        )
        if result.skipped:
            logger.warning("espn_scoreboard skipped unresolved games: %s", result.skipped)
        return "done"


class EspnClosesJob:
    name = "espn_closes"
    max_attempts = 3

    def slots(self, now: datetime) -> list[Slot]:
        return daily_slot(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        repo = GamesRepository(ctx.session_factory)
        now = _now()
        missing = await repo.finals_missing_close(NFL, since=now - CLOSE_LOOKBACK)
        saved = 0
        for espn_id in missing:
            lines = parse_pickcenter(await fetch_summary(ctx.http, NFL, espn_id))
            if lines and await repo.save_lines(espn_id, "espn", lines, now):
                saved += 1
        return "done" if saved == len(missing) else "partial"


class LinkEventsJob:
    name = "link_events"
    max_attempts = 2

    def slots(self, now: datetime) -> list[Slot]:
        return hourly_slots(self.name, now)

    async def run(self, ctx: JobContext, slot: Slot) -> str:
        result = await GamesRepository(ctx.session_factory).link_events(NFL)
        if result.unlinked:
            logger.warning("link_events: unlinked odds events %s", result.unlinked)
        return "done"


JOBS = [SeedJob(), NflHistoryJob(), EspnScoreboardJob(), EspnClosesJob(), LinkEventsJob()]
```

- [ ] **Step 4: Implement the CLI**

```python
# backend/app/jobs/__main__.py
"""python -m app.jobs run-due | <job name>   (Windows Task Scheduler runs `run-due` every 15 min)."""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import load_settings
from app.jobs.nfl import JOBS
from app.jobs.runner import JobContext, run_due, run_once
from app.storage.database import create_engine, create_session_factory


async def main(argv: Optional[list[str]] = None) -> int:
    by_name = {job.name: job for job in JOBS}
    parser = argparse.ArgumentParser(prog="python -m app.jobs")
    parser.add_argument("command", choices=["run-due", *by_name])
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(
            timeout=settings.provider_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "betting-aggregator/0.1 (local personal use)"},
        ) as http:
            ctx = JobContext(create_session_factory(engine), http, Path(settings.raw_data_dir))
            if args.command == "run-due":
                for key, state in (await run_due(JOBS, ctx, datetime.now(timezone.utc))).items():
                    print(f"{state:>13}  {key}")
            else:
                print(await run_once(by_name[args.command], ctx))
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/tests/test_nfl_jobs.py -v && pytest -q && ruff check backend`
Expected: all PASS.

- [ ] **Step 6: Live check against local Postgres (free endpoints, no credits)**

With `.env` set to `PERSISTENCE_ENABLED=true` and the DB migrated:

```bash
python -m app.jobs seed
python -m app.jobs nfl_history
python -m app.jobs espn_scoreboard
python -m app.jobs espn_closes
python -m app.jobs run-due
```

Expected:
- `seed` prints `done`.
- `nfl_history` prints `done` and loads about 3,000 games from 2015 onward.
- `espn_scoreboard` prints `done`.
- `espn_closes` prints `done` or `partial`.
- `run-due` prints one line per due slot. Earlier hours today show `missed` and the current hour shows `done`.

Spot-check in SQL that the current week has open and current lines:

```sql
SELECT g.espn_id, l.kind, l.spread_home, l.total
FROM games g JOIN game_lines l ON l.game_id = g.id
WHERE g.season = 2026 AND g.week = 6 AND l.source = 'espn'
ORDER BY g.start_time;
```

If ESPN calls fail with certificate errors on this machine, check that httpx uses certifi. It does by default. curl's Windows schannel revocation check does not apply to httpx.

- [ ] **Step 7: Commit**

```bash
git add backend/app/jobs/nfl.py backend/app/jobs/__main__.py backend/tests/test_nfl_jobs.py
git commit -m "feat: NFL ingest jobs (seed, nflverse history, ESPN scoreboard/closes, event linking) + CLI"
```

---

### Task 10: Slate API

**Files:**
- Create: `backend/app/domain/slate.py`
- Create: `backend/app/api/slate.py`
- Modify: `backend/app/main.py` (include the router)
- Test: `backend/tests/test_slate_api.py`

**Interfaces:**
- Consumes: the models, `aware` (Task 6) and `GamesRepository` via data seeded in tests.
- Produces `GET /api/v1/slate?sport=nfl&season=&season_type=REG|POST&week=`, returning `SlateResponse`:
  - top level: `{sport, season, season_type, week, weeks: [{season_type, week}], games: [SlateGame]}`
  - `SlateGame` is `{id, espn_id, season, season_type, week, start_time, status, home: {id, abbr, name}, away: {...}, home_score, away_score, venue, neutral, lines: {open?, current?, close?: LineView}, spread_move, total_move, odds_event_id}`
  - `LineView` is `{source, spread_home, spread_home_price, spread_away_price, total, over_price, under_price, ml_home, ml_away, captured_at}`
- Produces the FastAPI dependencies `get_session_factory()` and `get_now()`, which tests override.
- Rules:
  - Line source priority per kind is `espn`, then `nflverse`.
  - Movement is (close, else current) minus open.
  - With no season or week given, the week defaults to the one holding the earliest game starting after now − 12h. If there is none, it is the latest game's week.
  - The endpoint returns 503 when persistence is off, and 404 with a how-to message when no games are loaded.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slate_api.py
import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.slate import get_now, get_session_factory
from app.ingest.espn import parse_pickcenter, parse_scoreboard
from app.ingest.seed import seed_teams
from app.main import app
from app.storage.database import create_session_factory
from app.storage.games_repository import GamesRepository
from tests.matchup_helpers import db_url, load_json, new_session_factory

NOW = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


def _client(tmp_path, now=NOW, load=True):
    async def setup():
        factory = await new_session_factory(tmp_path)
        if load:
            await seed_teams(factory)
            repo = GamesRepository(factory)
            await repo.upsert_games("nfl", "espn", parse_scoreboard(
                load_json("espn/scoreboard_week6.json")), NOW)
            await repo.save_lines("401872964", "espn",
                                  parse_pickcenter(load_json("espn/summary_final.json")), NOW)

    asyncio.run(setup())
    # A fresh engine: pooled connections from setup's event loop must not leak into TestClient's.
    factory = create_session_factory(create_async_engine(db_url(tmp_path)))
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_now] = lambda: now
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_explicit_week_returns_games_lines_and_movement(tmp_path) -> None:
    body = _client(tmp_path).get(
        "/api/v1/slate", params={"sport": "nfl", "season": 2026, "week": 6}
    ).json()
    assert (body["season"], body["season_type"], body["week"]) == (2026, "REG", 6)
    assert {"season_type": "REG", "week": 4} in body["weeks"]
    assert {"season_type": "REG", "week": 6} in body["weeks"]
    games = {g["espn_id"]: g for g in body["games"]}
    assert set(games) == {"401872981", "401872995", "401872999"}
    den = games["401872995"]
    assert (den["home"]["abbr"], den["away"]["abbr"]) == ("DEN", "SEA")
    assert den["lines"]["open"]["spread_home"] == 2.5
    assert den["lines"]["current"]["spread_home"] == 1.5
    assert "close" not in den["lines"]
    assert den["spread_move"] == -1.0 and den["total_move"] == -1.0
    assert den["start_time"].startswith("2026-10-16T00:15:00")
    assert den["venue"] == "Empower Field at Mile High"
    # Games come back in kickoff order.
    assert [g["espn_id"] for g in body["games"]][0] == "401872981"


def test_default_week_is_next_upcoming(tmp_path) -> None:
    body = _client(tmp_path).get("/api/v1/slate", params={"sport": "nfl"}).json()
    assert body["week"] == 6


def test_final_game_prefers_close_for_movement(tmp_path) -> None:
    body = _client(tmp_path).get(
        "/api/v1/slate", params={"sport": "nfl", "season": 2026, "week": 4}
    ).json()
    [game] = body["games"]
    assert game["status"] == "final" and game["home_score"] == 27
    assert game["lines"]["close"]["total"] == 38.5
    assert game["total_move"] == -2.0  # 38.5 close - 40.5 open


def test_no_games_loaded_is_404_with_guidance(tmp_path) -> None:
    response = _client(tmp_path, load=False).get("/api/v1/slate", params={"sport": "nfl"})
    assert response.status_code == 404
    assert "python -m app.jobs" in response.json()["detail"]


def test_persistence_off_is_503() -> None:
    app.dependency_overrides.clear()
    response = TestClient(app).get("/api/v1/slate", params={"sport": "nfl"})
    assert response.status_code == 503


def test_unsupported_sport_rejected(tmp_path) -> None:
    assert _client(tmp_path).get("/api/v1/slate", params={"sport": "nba"}).status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_slate_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.slate'`.

- [ ] **Step 3: Implement the response models**

```python
# backend/app/domain/slate.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TeamView(BaseModel):
    id: int
    abbr: str
    name: str


class LineView(BaseModel):
    source: str
    spread_home: Optional[float] = None
    spread_home_price: Optional[int] = None
    spread_away_price: Optional[int] = None
    total: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    ml_home: Optional[int] = None
    ml_away: Optional[int] = None
    captured_at: datetime


class SlateGame(BaseModel):
    id: int
    espn_id: str
    season: int
    season_type: str
    week: Optional[int]
    start_time: datetime
    status: str
    home: TeamView
    away: TeamView
    home_score: Optional[int]
    away_score: Optional[int]
    venue: Optional[str]
    neutral: bool
    lines: dict[str, LineView]
    spread_move: Optional[float]
    total_move: Optional[float]
    odds_event_id: Optional[str]


class WeekRef(BaseModel):
    season_type: str
    week: int


class SlateResponse(BaseModel):
    sport: str
    season: int
    season_type: str
    week: int
    weeks: list[WeekRef]
    games: list[SlateGame]
```

- [ ] **Step 4: Implement the route**

```python
# backend/app/api/slate.py
"""Week slate: games with reference lines (ESPN, else nflverse) and movement since open."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import load_settings
from app.domain.slate import LineView, SlateGame, SlateResponse, TeamView, WeekRef
from app.storage.database import create_engine, create_session_factory
from app.storage.games_repository import LINE_FIELDS, aware
from app.storage.models import Game, GameLine, Team, Venue

router = APIRouter(prefix="/api/v1")
SOURCE_PRIORITY = ("espn", "nflverse")
SEASON_TYPE_ORDER = {"PRE": 0, "REG": 1, "POST": 2}
NO_DATA = (
    "No games loaded yet. Run `python -m app.jobs seed`, `python -m app.jobs nfl_history` "
    "and `python -m app.jobs espn_scoreboard`."
)


@lru_cache(maxsize=1)
def _default_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    settings = load_settings()
    if not settings.persistence_enabled:
        return None
    return create_session_factory(create_engine(settings))


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    factory = _default_session_factory()
    if factory is None:
        raise HTTPException(503, detail="Matchup data needs PERSISTENCE_ENABLED=true and Postgres.")
    return factory


def get_now() -> datetime:
    return datetime.now(timezone.utc)


def pick_lines(rows: Sequence[GameLine]) -> dict[str, GameLine]:
    rank = {source: index for index, source in enumerate(SOURCE_PRIORITY)}
    chosen: dict[str, GameLine] = {}
    for row in sorted(rows, key=lambda r: rank.get(r.source, len(rank))):
        chosen.setdefault(row.kind, row)
    return chosen


def movement(lines: dict[str, GameLine], field: str) -> Optional[float]:
    latest = lines.get("close") or lines.get("current")
    opener = lines.get("open")
    if latest is None or opener is None:
        return None
    now_value, open_value = getattr(latest, field), getattr(opener, field)
    if now_value is None or open_value is None:
        return None
    return round(now_value - open_value, 2)


async def resolve_week(
    session: AsyncSession, sport: str, now: datetime
) -> Optional[tuple[int, str, int]]:
    columns = select(Game.season, Game.season_type, Game.week).where(
        Game.sport == sport, Game.week.is_not(None)
    )
    row = (
        await session.execute(
            columns.where(Game.start_time >= now - timedelta(hours=12))
            .order_by(Game.start_time)
            .limit(1)
        )
    ).first()
    if row is None:
        row = (await session.execute(columns.order_by(Game.start_time.desc()).limit(1))).first()
    return (row[0], row[1], row[2]) if row else None


@router.get("/slate", response_model=SlateResponse)
async def get_slate(
    sport: str = Query("nfl", pattern="^nfl$"),
    season: Optional[int] = Query(None, ge=2000, le=2100),
    season_type: Optional[str] = Query(None, pattern="^(REG|POST)$"),
    week: Optional[int] = Query(None, ge=1, le=25),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    now: datetime = Depends(get_now),
) -> SlateResponse:
    async with session_factory() as session:
        if season is None or week is None:
            resolved = await resolve_week(session, sport, now)
            if resolved is None:
                raise HTTPException(404, detail=NO_DATA)
            season, season_type, week = resolved
        season_type = season_type or "REG"

        week_rows = (
            await session.execute(
                select(Game.season_type, Game.week)
                .where(Game.sport == sport, Game.season == season, Game.week.is_not(None))
                .distinct()
            )
        ).all()
        weeks = sorted(
            (WeekRef(season_type=kind, week=number) for kind, number in week_rows),
            key=lambda ref: (SEASON_TYPE_ORDER.get(ref.season_type, 9), ref.week),
        )

        home, away = aliased(Team), aliased(Team)
        rows = (
            await session.execute(
                select(Game, home, away, Venue.name)
                .join(home, Game.home_team_id == home.id)
                .join(away, Game.away_team_id == away.id)
                .outerjoin(Venue, Game.venue_id == Venue.id)
                .where(
                    Game.sport == sport,
                    Game.season == season,
                    Game.season_type == season_type,
                    Game.week == week,
                )
                .order_by(Game.start_time, Game.id)
            )
        ).all()
        game_ids = [row[0].id for row in rows]
        line_rows = (
            (await session.scalars(select(GameLine).where(GameLine.game_id.in_(game_ids)))).all()
            if game_ids
            else []
        )

    lines_by_game: dict[int, list[GameLine]] = defaultdict(list)
    for line in line_rows:
        lines_by_game[line.game_id].append(line)

    games = []
    for game, home_team, away_team, venue_name in rows:
        lines = pick_lines(lines_by_game[game.id])
        games.append(
            SlateGame(
                id=game.id,
                espn_id=game.espn_id,
                season=game.season,
                season_type=game.season_type,
                week=game.week,
                start_time=aware(game.start_time),
                status=game.status,
                home=TeamView(id=home_team.id, abbr=home_team.abbr, name=home_team.name),
                away=TeamView(id=away_team.id, abbr=away_team.abbr, name=away_team.name),
                home_score=game.home_score,
                away_score=game.away_score,
                venue=venue_name,
                neutral=game.neutral,
                lines={
                    kind: LineView(
                        source=row.source,
                        captured_at=aware(row.captured_at),
                        **{name: getattr(row, name) for name in LINE_FIELDS},
                    )
                    for kind, row in lines.items()
                },
                spread_move=movement(lines, "spread_home"),
                total_move=movement(lines, "total"),
                odds_event_id=game.event_id,
            )
        )
    return SlateResponse(
        sport=sport, season=season, season_type=season_type, week=week, weeks=weeks, games=games
    )
```

In `backend/app/main.py`, register the router directly after `app.include_router(router)`. This must come before the SPA catch-all route.

```python
from app.api.slate import router as slate_router
...
app.include_router(slate_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/tests/test_slate_api.py -v && pytest -q && ruff check backend`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/slate.py backend/app/api/slate.py backend/app/main.py backend/tests/test_slate_api.py
git commit -m "feat: GET /api/v1/slate (week games, reference lines, movement since open)"
```

---

### Task 11: Frontend router, React Query and the Slate page

**Files:**
- Modify: `frontend/package.json` and `package-lock.json`, via `npm install`
- Create: `frontend/src/lib/slate.ts`
- Create: `frontend/src/pages/SlatePage.tsx`
- Create: `frontend/src/pages/SlatePage.test.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles.css` (append slate styles)
- Modify: `frontend/tests/browser/board.spec.ts` (the odds board moves to `/odds`)

**Interfaces:**
- Consumes: `GET /api/v1/slate` from Task 10.
- Produces these routes:
  - `/` redirects to `/nfl`.
  - `/nfl` shows the current week.
  - `/nfl/:season/:seasonType/:week` shows a specific week, for example `/nfl/2026/reg/6`.
  - `/odds` shows the existing board (`App`).
- Produces `fetchSlate`, `formatSpread`, and the types `SlateResponse`, `SlateGame` and `WeekRef`.

- [ ] **Step 1: Install dependencies**

Run (in `frontend/`): `npm install react-router-dom@^6.28.0 @tanstack/react-query@^5.59.0`
Expected: both appear under `dependencies` in `package.json`.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/pages/SlatePage.test.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchSlate, type SlateResponse } from "../lib/slate";
import { SlatePage } from "./SlatePage";

vi.mock("../lib/slate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/slate")>();
  return { ...actual, fetchSlate: vi.fn() };
});

const line = (spread: number, total: number) => ({
  source: "espn", spread_home: spread, spread_home_price: -110, spread_away_price: -110,
  total, over_price: -110, under_price: -110, ml_home: 102, ml_away: -122,
  captured_at: "2026-10-11T15:00:00Z",
});

function slate(week: number, withGames = true): SlateResponse {
  return {
    sport: "nfl", season: 2026, season_type: "REG", week,
    weeks: [5, 6, 7].map((w) => ({ season_type: "REG", week: w })),
    games: withGames ? [{
      id: 1, espn_id: "401872995", season: 2026, season_type: "REG", week,
      start_time: "2026-10-16T00:15:00Z", status: "scheduled",
      home: { id: 10, abbr: "DEN", name: "Denver Broncos" },
      away: { id: 26, abbr: "SEA", name: "Seattle Seahawks" },
      home_score: null, away_score: null, venue: "Empower Field at Mile High", neutral: false,
      lines: { open: line(2.5, 43.5), current: line(1.5, 42.5) },
      spread_move: -1, total_move: -1, odds_event_id: null,
    }] : [],
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SlatePage", () => {
  afterEach(() => { cleanup(); vi.mocked(fetchSlate).mockReset(); });

  it("shows each game's current line, opener and movement", async () => {
    vi.mocked(fetchSlate).mockResolvedValue(slate(6));
    renderAt("/nfl");
    expect(await screen.findByText("SEA @ DEN")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2026 · Week 6" })).toBeInTheDocument();
    expect(screen.getByText("SEA -1.5")).toBeInTheDocument();
    expect(screen.getByText("O/U 42.5")).toBeInTheDocument();
    expect(screen.getByText("SEA -2.5 · 43.5")).toBeInTheDocument();
    expect(screen.getByText("Home line -1 since open")).toBeInTheDocument();
  });

  it("navigates to the next week", async () => {
    vi.mocked(fetchSlate).mockImplementation(async ({ week } = {}) => slate(Number(week ?? 6)));
    renderAt("/nfl/2026/reg/6");
    fireEvent.click(await screen.findByRole("button", { name: "Next week" }));
    await waitFor(() =>
      expect(fetchSlate).toHaveBeenLastCalledWith(
        expect.objectContaining({ season: "2026", seasonType: "reg", week: "7" }),
      ),
    );
  });

  it("says so when a week has no games loaded", async () => {
    vi.mocked(fetchSlate).mockResolvedValue(slate(6, false));
    renderAt("/nfl");
    expect(await screen.findByText("No games loaded for this week yet.")).toBeInTheDocument();
  });

  it("shows the API error message", async () => {
    vi.mocked(fetchSlate).mockRejectedValue(new Error("Slate request failed with status 503"));
    renderAt("/nfl");
    expect(await screen.findByRole("alert")).toHaveTextContent("503");
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run (in `frontend/`): `npx vitest run src/pages/SlatePage.test.tsx`
Expected: FAIL. The import of `../lib/slate` cannot be resolved.

- [ ] **Step 4: Implement the API client and the page**

```ts
// frontend/src/lib/slate.ts
export interface SlateTeam { id: number; abbr: string; name: string }

export interface SlateLine {
  source: string;
  spread_home: number | null;
  spread_home_price: number | null;
  spread_away_price: number | null;
  total: number | null;
  over_price: number | null;
  under_price: number | null;
  ml_home: number | null;
  ml_away: number | null;
  captured_at: string;
}

export type LineKind = "open" | "current" | "close";

export interface SlateGame {
  id: number;
  espn_id: string;
  season: number;
  season_type: string;
  week: number | null;
  start_time: string;
  status: string;
  home: SlateTeam;
  away: SlateTeam;
  home_score: number | null;
  away_score: number | null;
  venue: string | null;
  neutral: boolean;
  lines: Partial<Record<LineKind, SlateLine>>;
  spread_move: number | null;
  total_move: number | null;
  odds_event_id: string | null;
}

export interface WeekRef { season_type: string; week: number }

export interface SlateResponse {
  sport: string;
  season: number;
  season_type: string;
  week: number;
  weeks: WeekRef[];
  games: SlateGame[];
}

export interface SlateRequest {
  season?: string;
  seasonType?: string;
  week?: string;
  signal?: AbortSignal;
}

export async function fetchSlate({ season, seasonType, week, signal }: SlateRequest = {}): Promise<SlateResponse> {
  const params = new URLSearchParams({ sport: "nfl" });
  if (season && week) {
    params.set("season", season);
    params.set("week", week);
    params.set("season_type", (seasonType ?? "reg").toUpperCase());
  }
  const response = await fetch(`/api/v1/slate?${params.toString()}`, { signal });
  if (!response.ok) throw new Error(`Slate request failed with status ${response.status}`);
  return response.json() as Promise<SlateResponse>;
}

/** Book-style spread label from the home line: -1.5 -> "DEN -1.5", +1.5 -> "SEA -1.5". */
export function formatSpread(game: Pick<SlateGame, "home" | "away">, spreadHome: number | null | undefined): string {
  if (spreadHome === null || spreadHome === undefined) return "—";
  if (spreadHome === 0) return "PK";
  return spreadHome < 0 ? `${game.home.abbr} ${spreadHome}` : `${game.away.abbr} -${spreadHome}`;
}
```

```tsx
// frontend/src/pages/SlatePage.tsx
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { GlassCard } from "../components/GlassCard";
import { fetchSlate, formatSpread, type SlateGame, type WeekRef } from "../lib/slate";

const STATUS_LABELS: Record<string, string> = {
  in_progress: "Live",
  final: "Final",
  postponed: "Postponed",
  canceled: "Canceled",
};

function weekLabel(ref: WeekRef): string {
  return ref.season_type === "POST" ? `Playoffs round ${ref.week}` : `Week ${ref.week}`;
}

function weekPath(season: number, ref: WeekRef): string {
  return `/nfl/${season}/${ref.season_type.toLowerCase()}/${ref.week}`;
}

function kickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function statusText(game: SlateGame): string {
  const label = STATUS_LABELS[game.status];
  if (!label) return kickoff(game.start_time);
  const score = game.home_score !== null && game.away_score !== null
    ? ` · ${game.away.abbr} ${game.away_score}–${game.home_score} ${game.home.abbr}`
    : "";
  return `${label}${score}`;
}

function GameCard({ game }: { game: SlateGame }) {
  const latest = game.lines.close ?? game.lines.current;
  const opener = game.lines.open;
  return (
    <GlassCard className="slate-card">
      <div className="slate-card-head">
        <h3>{game.away.abbr} @ {game.home.abbr}</h3>
        <span className="slate-status">{statusText(game)}</span>
      </div>
      <p className="slate-venue">{game.venue ?? "Venue TBD"}{game.neutral ? " · neutral site" : ""}</p>
      <dl className="slate-lines">
        <div><dt>Spread</dt><dd>{formatSpread(game, latest?.spread_home)}</dd></div>
        <div><dt>Total</dt><dd>{latest?.total != null ? `O/U ${latest.total}` : "—"}</dd></div>
        <div><dt>Open</dt><dd>{opener ? `${formatSpread(game, opener.spread_home)} · ${opener.total ?? "—"}` : "—"}</dd></div>
      </dl>
      {game.spread_move ? <p className="slate-move">Home line {game.spread_move > 0 ? "+" : ""}{game.spread_move} since open</p> : null}
      {latest && <p className="slate-source">{latest.source} {game.lines.close ? "close" : "current"} line</p>}
    </GlassCard>
  );
}

export function SlatePage() {
  const { season, seasonType, week } = useParams();
  const navigate = useNavigate();
  const { data, error, isLoading } = useQuery({
    queryKey: ["slate", "nfl", season ?? "current", seasonType ?? "", week ?? ""],
    queryFn: ({ signal }) => fetchSlate({ season, seasonType, week, signal }),
  });

  const index = data ? data.weeks.findIndex((ref) => ref.season_type === data.season_type && ref.week === data.week) : -1;
  const previous = data && index > 0 ? data.weeks[index - 1] : undefined;
  const next = data && index >= 0 && index < data.weeks.length - 1 ? data.weeks[index + 1] : undefined;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">NFL slate</p>
          <h1>{data ? `${data.season} · ${weekLabel(data)}` : "Slate"}</h1>
        </div>
        <nav className="app-nav" aria-label="Sections"><Link to="/odds">Odds board</Link></nav>
      </header>

      <section className="control-band" aria-label="Week picker">
        <button className="text-button" disabled={!previous} onClick={() => data && previous && navigate(weekPath(data.season, previous))} type="button">Previous week</button>
        <button className="text-button" disabled={!next} onClick={() => data && next && navigate(weekPath(data.season, next))} type="button">Next week</button>
      </section>

      {error && <div className="notice error-notice" role="alert">{error instanceof Error ? error.message : "Could not load the slate"}</div>}
      {isLoading && <div className="skeleton-card" aria-label="Loading games"><span /><span /><span /></div>}
      {data && data.games.length === 0 && <div className="notice info-notice" role="status">No games loaded for this week yet.</div>}

      <section className="slate-grid" aria-live="polite">
        {data?.games.map((game) => <GameCard game={game} key={game.id} />)}
      </section>
    </main>
  );
}
```

```tsx
// frontend/src/main.tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import { SlatePage } from "./pages/SlatePage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate replace to="/nfl" />} />
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
          <Route path="/odds" element={<App />} />
          <Route path="*" element={<Navigate replace to="/nfl" />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
```

Append to `frontend/src/styles.css`:

```css
.app-nav a { color: var(--secondary); text-decoration: none; font-weight: 600; }
.app-nav a:hover { text-decoration: underline; }
.slate-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); margin-top: 16px; }
.slate-card { padding: 16px 18px; }
.slate-card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.slate-card-head h3 { margin: 0; font-size: 1.15rem; }
.slate-status { color: var(--muted); font-size: 0.85rem; white-space: nowrap; }
.slate-venue { color: var(--muted); font-size: 0.8rem; margin: 4px 0 12px; }
.slate-lines { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 0; }
.slate-lines dt { color: var(--muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; }
.slate-lines dd { margin: 2px 0 0; font-family: "DM Mono", ui-monospace, monospace; }
.slate-move { color: var(--warning); font-size: 0.8rem; margin: 10px 0 0; }
.slate-source { color: var(--muted); font-size: 0.7rem; margin: 6px 0 0; }
```

In `frontend/tests/browser/board.spec.ts`, change `await page.goto("/?sport=nfl&market=moneyline");` to `await page.goto("/odds?sport=nfl&market=moneyline");`.

- [ ] **Step 5: Run the tests, lint and build**

Run (in `frontend/`): `npx vitest run && npm run lint && npm run build`
Expected: all Vitest suites PASS (the existing `App.test.tsx` is unchanged), there are no TypeScript errors, and the build succeeds.

Run (in `frontend/`): `npm run test:browser`
Expected: the existing fixture smoke test PASSES at `/odds`.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/slate.ts frontend/src/pages frontend/src/main.tsx frontend/src/styles.css frontend/tests/browser/board.spec.ts
git commit -m "feat(ui): router + React Query; NFL slate page with week picker; odds board at /odds"
```

---

### Task 12: Local operations (Task Scheduler, docs) + end-to-end check

**Files:**
- Create: `scripts/register_run_due_task.ps1`
- Modify: `README.md` (add a "Local matchup hub" section)
- Modify: `CLAUDE.md` (local-only; new modules; `/slate` route)

**Interfaces:**
- Produces the Windows scheduled task `BettingAggregatorRunDue`, which runs `.venv\Scripts\python.exe -m app.jobs run-due` every 15 minutes.

- [ ] **Step 1: Write the Task Scheduler script**

```powershell
# scripts/register_run_due_task.ps1
# Registers a per-user scheduled task that runs the ingest jobs every 15 minutes while you're logged on.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts/register_run_due_task.ps1
# Remove:                      Unregister-ScheduledTask -TaskName BettingAggregatorRunDue -Confirm:$false
param([string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path)

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create the venv first: python -m venv .venv; .venv\Scripts\pip install -e .[dev]" }

$action = New-ScheduledTaskAction -Execute $python -Argument "-m app.jobs run-due" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "BettingAggregatorRunDue" -Action $action -Trigger $trigger `
    -Settings $settings -Description "betting_aggregator: python -m app.jobs run-due" -Force | Out-Null
Write-Host "Registered BettingAggregatorRunDue (every 15 min). Check: Get-ScheduledTaskInfo BettingAggregatorRunDue"
```

- [ ] **Step 2: Update the README and CLAUDE.md**

Add a "Local matchup hub" section to `README.md` with exactly these steps:
1. `docker compose up -d postgres`
2. `copy .env.example .env`. The defaults are fixture odds with persistence on.
3. `alembic upgrade head`
4. `python -m app.jobs seed`, then `python -m app.jobs nfl_history`, then `python -m app.jobs espn_scoreboard`, then `python -m app.jobs espn_closes`.
5. `uvicorn app.main:app --app-dir backend --reload --port 8000`, and `npm run dev` in `frontend/`.
6. Open `http://localhost:5173/nfl`. The odds board is at `/odds`.
7. Optionally run `powershell -ExecutionPolicy Bypass -File scripts/register_run_due_task.ps1` to keep data fresh every 15 minutes while the PC is on.

Add a note that the app is local-only, that CI no longer deploys, and that the existing Fly app is left as it is until the owner decides.

In `CLAUDE.md`:
- Replace the "Deployed as one Fly.io app" bullet with a local-only bullet. It should state that the CI deploy job was removed on purpose because the app will hold a personal bet log.
- Add bullets for `app/ingest/` (pure parsers producing `GameRecord`/`LineRecord`), `app/storage/games_repository.py`, `app/jobs/` (slots, `run-due`, and the CLI) and `GET /api/v1/slate`.
- Update the routes bullet to include `/slate`.

- [ ] **Step 3: Full verification**

Run from the repo root: `pytest -q && ruff check backend`
Expected: all PASS.

Run in `frontend/`: `npm test && npm run lint && npm run build`
Expected: all PASS.

End to end against local Postgres:
1. Run steps 1–5 of the README section.
2. Open `http://localhost:5173/nfl`. The current NFL week's games show with a spread, total, opener and movement. The ESPN numbers match espn.com's game page for at least two games.
3. Click "Previous week". Final games show scores, and lines are labeled "espn close line" once `espn_closes` has run, or "nflverse close line" for older weeks.
4. Open `http://localhost:5173/odds`. The existing board still works in fixture mode.
5. Run `python -m app.jobs run-due` twice in a row. The second run prints nothing for slots already `done` this hour.

- [ ] **Step 4: Commit**

```bash
git add scripts/register_run_due_task.ps1 README.md CLAUDE.md
git commit -m "docs+ops: local matchup hub runbook and Task Scheduler registration"
```

---

## Out of scope for this plan (later phases, per spec)

- **Phase 1b:**
  - team stats, injuries and weather
  - `analytics/` records and situational context
  - `/games/{id}/context` and the Matchup page
- **Phase 1c:**
  - the `odds_snapshot` slots with the raw-first write, content-aware success and quota floor
  - the health panel
  - `/games/{id}/lines` and refresh
  - bets, `grade_bets`, CLV and the Bets page
  - the go/no-go week
- **Phase 2:** NBA.
- **Phases 3 and 4:** college football and college basketball, each with its own spec.
