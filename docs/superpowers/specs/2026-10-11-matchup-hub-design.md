# Matchup Hub — Design Spec (betting_aggregator)

## Context
The user wants one app for weekly handicapping across NFL, NBA, college football and college basketball:
- browse the current and upcoming slates, then open one matchup at a time;
- see relevant stats for that matchup;
- see lines and prices across books and Kalshi;
- log bets and track the results and CLV.

What exists today is split across three repos:
- **betting_aggregator**: FastAPI + React + Postgres. Odds for NFL and NBA only, refreshed once a day, no stats.
- **nfl_ats**: Streamlit historical ATS study. Unplayed games are dropped.
- **nba_prediction**: local research. Has the NBA stats and travel pipelines, plus an odds collector.

The user's real-money goal and past findings (nba_prediction `docs/edge-research-findings-2026-10.md`) say the edge comes from **when you bet relative to the close**. That makes line history and CLV central, not decoration.

### Decisions made with the user (2026-10-10/11)
- **Role:** full loop, made up of four parts:
  - context stats
  - prices across books
  - line history
  - bet log with grading and CLV
- **Home:** grow `repos/betting_aggregator`. It keeps one DB and one app. `nfl_ats` and `nba_prediction` stay as research repos.
- **Odds budget:** free tiers plus a scraped consensus source.
  - The Odds API runs on its own free key, separate from the NBA collector's.
  - ESPN's public JSON endpoints supply open, current and close lines.
- **Sport order:** NFL first, NBA second, then college football, then college basketball.
- **Access:** local only. It runs on the user's PC with docker-compose Postgres and has no auth. Fly auto-deploy will be disabled so the bet log can never be published.
- **Free data only.** Any paid dependency gets flagged and is not used.

### Constraints carried in
- The Illinois per-bet fees are kept in a per-venue config, not hardcoded:
  - FanDuel $0.50, waived at $25 and up
  - DraftKings $0.50, waived on singles of $50 and up
  - Fanatics $0.25
  - others $0
- Kalshi is a supplementary venue, and its prices stay out of `/odds` (per the aggregator's CLAUDE.md).
- The analysis is framed as statistically honest and is not betting advice (AGENTS.md).
- Per CLAUDE.md, ask before any deploy, paid service or irreversible operation. Tearing down the existing Fly app is the user's call.

## Architecture
The approach is **ingest-and-store**:
- Ingestion jobs normalize every source into the aggregator's Postgres.
- Derived context is computed on read by pure functions.
- Logic from the research repos is reused by porting it, not by reading their files.

**Backend layout** (`backend/app/`):
- `providers/`: the odds sources (The Odds API, Kalshi), unchanged.
- `ingest/` (new): one module per stats source:
  - `espn.py`: schedule, scores, lines, injuries
  - `nflverse.py`: games history, weekly team stats
  - `open_meteo.py`: weather
  - `nba_stats.py`: phase 2
- `analytics/` (new): pure functions with no I/O:
  - ATS, straight-up and over/under records
  - rest and travel
  - CLV and no-vig math
  - bet grading
- `jobs/` (new): `python -m app.jobs run-due | <job>`. It is a registry of job, cadence and function. Last-run state goes in the existing `provider_refresh_runs` table.
- `api/`: new routes, listed under API below.
- `domain/models.py`: the `Sport` enum gains `NCAAF` and `NCAAB`. Only NFL is wired up in phase 1.
- `config.py`: venue fee rules and a separate `ODDS_API_KEY`.
  - Fee rules are **effective-dated**, as a list of `{venue, fee, waive_rule, effective_from}`, because Illinois fee policies change (FanDuel's was recently reinstated).
  - Each bet stores the fee computed at entry, so later rule changes never rewrite history.

**Scheduling:** Windows Task Scheduler runs `python -m app.jobs run-due` every 15 minutes.
- When the PC was off, per-book snapshots are lost.
- ESPN's open and close for past games backfill CLV and ATS.

**ESPN facts (checked 2026-10-11 against live payloads):**
- **The scoreboard's `close`:** until kickoff, the scoreboard's `odds[0]` "close" field is the *current* line. We store it as `current` while the game state is `pre`, and as `close` once the game has started.
- **Finished games:** the scoreboard **drops `odds` once a game is final**. The game summary endpoint (`/summary?event=<id>`, `pickcenter[0]`) keeps open and close, in the same shape. That endpoint is the backfill source.
- **One book:** ESPN lines come from a single book (DraftKings, provider id 100).
- **Bonus fields** for phase 1b: the scoreboard also carries `weather`, and the summary carries `injuries` and `againstTheSpread`.
- **nflverse join:** nflverse `games.csv` has an `espn` column, so historical games join to ESPN games on `espn_id`. nflverse `spread_line` is positive when home is favored, so `spread_home = -spread_line`.

## Data model (new Alembic migrations)

| Table | Key columns | Notes |
|---|---|---|
| `teams` | id, sport, abbr, name, espn_id, conference, venue_id | Seeded from a committed CSV |
| `team_aliases` | team_id, source, alias | Replaces name-hash matching (`identity.py`) for linking odds events to games |
| `venues` | id, name, lat, lon, tz, roof, surface | Used for travel and weather |
| `games` | id, sport, season, week (null for basketball), season_type, start_time, home/away_team_id, venue_id, neutral, status, home/away_score, espn_id (unique), event_id → `events.id` (nullable) | The backbone; `event_id` links odds |
| `game_lines` | game_id, source (espn/nflverse), kind (open/current/close), spread_home, spread_home_price, spread_away_price, total, over_price, under_price, ml_home, ml_away, captured_at | Reference lines for ATS, O/U and CLV. nflverse history is labeled "unverified close" |
| `team_game_stats` | game_id, team_id, stats JSONB | One stats bag per sport |
| `injuries` | team_id, player, position, status, detail, source, reported_at | Snapshots; the latest per player is shown |
| `weather` | game_id, fetched_at, temp_f, wind_mph, precip_pct, conditions | Outdoor games only |
| `job_slots` | job, slot_key (unique together), state, attempts, coverage JSONB, raw_path, updated_at | One current state per scheduled slot (see Job state model) |
| `bets` | id, game_id, placed_at, venue, market, selection, line, price_american, stake, fee, status, payout, close_line, close_price, close_source, clv_points, clv_prob | `fee` comes from venue config at entry |

Per-book prices stay in the existing `offer_observations`, which is append-only. Its default 90-day retention is fine for line charts. Bets copy their own close values, so retention never loses them.

## Ingestion (phase 1, NFL)

| Job | Source | Cadence |
|---|---|---|
| `seed` | Committed CSV with 32 teams, aliases, and stadium coordinates, roof and time zone | Once |
| `nfl_history` | nflverse `games.csv` from 2015: games, scores, closing spread and total, rest | Once, then weekly |
| `espn_scoreboard` | ESPN scoreboard for the current and next week: schedule, status, scores, open and current lines (one call gives both, so schedule and lines are one job) | Hourly, 08:00–23:00 CT |
| `espn_closes` | ESPN game summary `pickcenter` for final games still missing a close: open and close | Daily |
| `team_stats` | nflverse weekly team stats (EPA, success rate, counting stats) | Tuesday |
| `injuries` | ESPN injuries, with nflverse as fallback | Daily |
| `weather` | Open-Meteo forecast for outdoor games within 7 days | Daily; every 3 hours on game day |
| `odds_snapshot` | The Odds API h2h/spreads/totals for the free-tier Illinois books: `draftkings, fanduel, betmgm, betrivers` (`us`) and `espnbet` / theScore Bet (`us2`) | Tue open, Thu, Sat, Sun 10am CT, pre-kickoff, plus a manual Refresh. Budget is about 100 credits a month, under 500 |
| `link_events` | Matches `events` to `games` by local date and aliased teams. There is no tight start-time window, and ESPN ids are authoritative | After each odds snapshot |
| `grade_bets` | Internal tables | After games go final |

**Book coverage (verified 2026-10-11 against the Odds API bookmaker list):**
- Caesars (`williamhill_us`) and Fanatics are **paid-only** and are not snapshotted.
- The aggregator's current default allowlist includes them, so phase 0 fixes it.
- Bets at uncovered venues use a manually entered price in the bet form.
- Coverage is *measured* on every snapshot (books × markets × games returned) and shown on the health panel. It is never assumed.

**Job state model (crash-safe and idempotent):**
- **Attempts:** `provider_refresh_runs` stays the append-only log, with one row per HTTP attempt: provider, status, error, `quota_remaining`.
- **Slots:** a new `job_slots` table holds `(job, slot_key)` unique, `state`, `attempts`, `coverage JSONB`, `raw_path` and `updated_at`.
  - Example `slot_key`: `odds_snapshot:nfl:2026-10-18:pre_kick:<game_id>`.
  - Every slot has exactly one current state.
  - Non-terminal states: `pending`, `raw_saved`, `partial`.
  - Terminal states: `done`, `empty`, `missed`, `skipped_quota`, `failed`.
- **`run-due`:**
  - Acts only on non-terminal slots inside their window.
  - Moves past-window slots to `missed` once.
  - Never re-emits a terminal slot.
- **Raw first, for paid calls:**
  1. The response body is written to `data/raw/<provider>/<date>/<ts>_<slot>.json.gz` (gitignored) before parsing, and the slot becomes `raw_saved`.
  2. Parsing and inserting the observations happen in one DB transaction that sets the final state.
  3. A crash after the HTTP call re-parses `raw_saved` slots from disk and never pays for the request again.
- **Success depends on content:**
  - `odds_snapshot` is `done` only if every targeted game has all 3 markets from at least 3 books. Otherwise it is `partial`, and it retries up to the attempt cap until the window closes.
  - The `espn_closes` close requires the summary `pickcenter` to be present.
  - `espn_closes` and `grade_bets` require final scores.
- **Quota authority:**
  - The Odds API `x-requests-remaining` header (already parsed in `the_odds_api/client.py`) is authoritative.
  - Local counts are audit-only.
  - The slot becomes `skipped_quota` when the floor is hit.
- **Time:**
  - All windows are defined in `America/Chicago` via `zoneinfo`, so DST is handled by the code, not by trigger times.
  - `tzdata` is added as a dependency, since Windows has no IANA database.
  - Task Scheduler simply fires every 15 minutes.
- **Provider fields:** use the providers' structured fields (ESPN event and competitor ids, Odds API `market.key` / `bookmaker.key`) rather than parsing strings.

**Health panel** (extends the existing `/status`):
- When `run-due` last fired, so a dead Task Scheduler is visible.
- Last result per job.
- Today's expected vs completed vs missed slots.
- Credits remaining.
- Book × market coverage of the last snapshot.
- Odds events that couldn't be linked to a game.
- Slots stuck in `raw_saved`.

**Failure handling:**
- A failed attempt writes its error and never deletes stored data.
- The API returns `data_as_of` for each panel.
- The UI flags stale panels.

**Pre-build checks (first plan task):** confirm the exact endpoint shapes and saved fixtures for:
- ESPN scoreboard, odds (open/current/close) and injuries
- the nflverse weekly team-stats file columns
- Open-Meteo

## Matchup analytics (`analytics/`)
- **Records:**
  - straight-up, ATS and over/under for the season and the last N games (default 5, adjustable)
  - home/away and favorite/underdog splits
  - head-to-head ATS over the last N meetings
  - pushes counted as pushes
  - only games before the one being viewed are used
- **Efficiency (NFL):**
  - offense and defense EPA/play, success rate, pass and rush EPA, points for and against per game
  - each with a league rank
- **Counting stats:** yards/play, turnovers, sacks, 3rd-down %. The final list depends on the nflverse file columns.
- **Situational:**
  - days of rest, short week, coming off a bye
  - travel miles (great-circle distance from the previous venue) and time zones crossed
  - divisional and primetime flags
- **Weather:** temperature, wind and precipitation at kickoff. Dome or closed roof shows "indoors".
- **Injuries:** listed by status, with a starting-QB-change flag.
- **Market:**
  - spread and total movement from open to current to close
  - best price on each side across books
  - how far books disagree
  - Kalshi price
- **CLV:**
  - Spread/total, line CLV: points gained against the closing line on your side.
  - Spread/total, price CLV: the no-vig closing probability minus your bet's implied probability.
  - Moneyline: `clv = p_close_novig / p_bet_implied − 1`.
  - Close reference, in order of preference:
    1. the no-vig consensus from the pre-kickoff Odds API snapshot
    2. ESPN's close
  - `close_source` records which one was used.
- **P&L:** payout minus stake minus fee, using the venue fee rules.

## API (`/api/v1`, local and unauthenticated)
- `GET /slate?sport=nfl&season=&week=`: games with consensus lines, movement, best price, and weather/injury/bet flags.
- `GET /games/{id}`: header, venue and weather.
- `GET /games/{id}/context?last_n=5`: records, efficiency, situational and injuries, each with `data_as_of`.
- `GET /games/{id}/lines`: reference lines, per-book offers (reusing `/odds` and `/history` internals) and Kalshi.
- `POST /games/{id}/refresh`: refreshes the odds snapshot for one game, with a budget guard via the existing quota floor.
- `GET/POST/PATCH /bets`, `GET /bets/summary?group_by=venue|sport|market`.

The existing `/odds`, `/history` and `/prediction-markets` are kept as they are.

## Frontend (React + Vite + TS, existing theme)
- Adds `react-router-dom` and `@tanstack/react-query`, which match the developer profile.
- Reuses `OddsMatrix`, `PredictionMarkets` and `GlassCard`.
- **Slate** (`/:sport/:season/:seasonType/:week`, e.g. `/nfl/2026/reg/6`):
  - week picker
  - game cards showing kickoff, spread and total, movement arrow, best price, and weather, injury and bet badges
- **Matchup** (`/game/:id`):
  - Lines tab: the matrix, a line movement chart and Kalshi
  - Context tabs: Records, Efficiency, Situational, Injuries, Head-to-head
  - clicking a price cell opens a pre-filled Log Bet form
- **Bets** (`/bets`):
  - open and settled bets
  - profit, ROI after fees, average CLV and % of bets beating the close
  - breakdowns by venue, sport and market

## Phases
- **Phase 0, prerequisites:**
  - land the 23 uncommitted files on `feat/sportsbooks-prediction-markets` (the user decides whether to commit or PR them)
  - disable the `flyctl deploy` job in `.github/workflows/ci-deploy.yml`
  - get a separate Odds API key
  - remove the paid-only `williamhill_us` and `fanatics` from the default `ODDS_BOOKMAKERS` and add `espnbet`
  - run a one-time coverage smoke test that calls the real API once and records which books and markets actually come back
  - document `docker-compose up` plus Task Scheduler setup as the default way to run
- **Phase 1a:** migrations, seed, `nfl_history`, `espn_scoreboard`, `espn_closes`, `link_events`, `/slate`, Slate page.
- **Phase 1b:** `team_stats`, `injuries`, `weather`, `analytics/` records and situational, `/context`, Matchup page.
- **Phase 1c:** `odds_snapshot` cadence, `/lines` and refresh, bets, `grade_bets`, CLV, Bets page.
- **Phase 2, NBA:**
  - nba_api team ratings (off/def/net, pace, four factors)
  - back-to-back and 3-in-4 flags, plus travel ported from `nba_prediction/src/ingest/dataPrep/steps/calculate_travel_fatigue.py`
  - ESPN injuries
  - odds at a 10am open and pre-tip
- **Phases 3 and 4 (outline only, each gets its own spec):**
  - college football via CFBD (free key; lines history, PPA, SP+)
  - college basketball via ESPN plus Barttorvik (scraped)
  - both need large alias tables (130 FBS teams and 360+ D-I teams) and have no free injury source

## Reuse
- `offer_observations`, `events` and `provider_event_mappings`, plus the `/history` internals (`storage/repository.py`), for line movement.
- `provider_refresh_runs` for job state.
- `providers/cache.py` quota floor, applied to the manual Refresh.
- `providers/kalshi/*` for prediction-market prices.
- `OddsMatrix.tsx` and `PredictionMarkets.tsx`.
- Logic to port, not import:
  - nfl_ats ATS and pre-game record logic (`src/nfl_ats/data/clean.py`)
  - nfl_ats team relocation mapping
  - nba_prediction travel and fatigue logic
- ESPN client patterns in `NFL_Confidence/backend/app/integrations/espn.py`.

## Verification
- **Backend:** `pytest`, with saved JSON fixtures for ESPN, nflverse, Open-Meteo and The Odds API, and no network calls in tests. Hand-worked analytics cases:
  - an ATS push
  - CLV on known prices (e.g. bet -105, close -120 no-vig)
  - KC → BUF travel miles
  - Thursday after Sunday flagged as a short week
  - fees waived at the threshold
- **Frontend:** Vitest for Slate, Matchup and the bet form. The Playwright smoke test is extended to go from slate to matchup to logging a bet in fixture mode.
- **End to end, locally:**
  1. `docker-compose up` for Postgres, then `alembic upgrade head`.
  2. `python -m app.jobs seed && python -m app.jobs nfl_history && python -m app.jobs run-due`.
  3. `uvicorn` plus `npm run dev`.
  4. Open the current NFL week, click through a matchup, log a fixture bet, run `grade_bets` against a finished game, and confirm the CLV and P&L by hand.
- **Crash recovery tests:**
  - Kill after the raw write: the next run re-parses the file and makes no new HTTP call.
  - Kill mid-transaction: the slot stays `raw_saved`.
  - A past-window slot becomes `missed` exactly once.
  - A terminal slot is never re-attempted.
  - A `partial` snapshot retries, then stops at the attempt cap.
- **Go/no-go gate before trusting it for real bets.** After one full NFL week of running locally, the health panel must show:
  - at least 90% of expected slots `done` (excluding windows when the PC was off)
  - pre-kickoff snapshots for every game the PC was on for
  - at least 3 books per market
  - zero unlinked events
  - zero slots stuck in `raw_saved`
  - every ESPN open and close backfilled for graded games
- **Raw storage check:** measure the raw folder size after that week. If the season projection is over 2 GB, add a retention or compaction rule.

## After approval (brainstorming → planning handoff)
1. Copy this spec to `repos/betting_aggregator/docs/superpowers/specs/2026-10-11-matchup-hub-design.md` on a new branch created from `master` in a separate worktree, so the uncommitted feature work is untouched, and commit it.
2. Invoke `superpowers:writing-plans` to produce the phase 0 + 1a implementation plan, and let the user choose how to execute it.
