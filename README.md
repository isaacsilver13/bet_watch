# Betting Aggregator

Standalone NFL and NBA odds comparison app.

## Current slice

- FastAPI backend with a canonical event and odds contract.
- Provider protocol plus a credential-free empty provider.
- Credential-free fixture provider for local UI development (`ODDS_PROVIDER=fixture`).
- React/Vite frontend shell for side-by-side comparison.
- The Odds API adapter is available when `THE_ODDS_API_KEY` is configured.
- Optional PostgreSQL persistence stores refresh runs, events, provider mappings,
  and immutable offer observations.
- Canonical event IDs normalize team aliases and five-minute start-time buckets;
    provider-specific offer IDs remain attached to each observation.
- Cached refreshes use single-flight locking, stale last-known-good fallback,
    retry/backoff, and configurable low-quota throttling.
- Sportsbook coverage is configurable: `ODDS_BOOKMAKERS` (allowlist, wins) or `ODDS_REGIONS`;
  selected coverage is shown in `/api/v1/status`.
- Optional read-only Kalshi prediction-market contracts (`KALSHI_ENABLED=true`) are served from
  `GET /api/v1/prediction-markets?sport=nfl|nba`, separate from `/odds` so they never enter the
  sportsbook grid or best-price logic. Only two-sided game-winner contracts are matched to a
  sportsbook game (teams + date); everything else is shown as unmatched behind a UI filter.
  Fixture mode serves committed sample payloads and never contacts Kalshi.
- The responsive frontend uses one sportsbook matrix per game, URL-backed league/date/team/market
  filters, source-specific stale/error states, and paired Kalshi outcomes with last trade labeled
  separately from bid and ask.
- `GET /api/v1/history?event_id=...&market_type=...` reads immutable historical
    observations when persistence is enabled.

## Local matchup hub

Local development runs against docker-compose Postgres and may hold private data. CI does not
deploy automatically; production changes to the existing Fly app remain an explicit owner action.
Analysis here is statistically honest context, not betting advice.

1. `docker compose up -d postgres`
2. `copy .env.example .env` (defaults: fixture odds, persistence on)
3. `alembic upgrade head`
4. `python -m app.jobs seed`, then `python -m app.jobs nfl_history`, then
   `python -m app.jobs espn_scoreboard`, then `python -m app.jobs espn_closes`, then (once)
   `python -m app.jobs team_stats_history`, then `python -m app.jobs injuries` and
   `python -m app.jobs weather`
5. `uvicorn app.main:app --app-dir backend --reload --port 8000`, and `npm run dev` in `frontend/`
6. Open `http://localhost:5173/nfl`. The odds board is at `/odds`.
7. For a one-off local scheduler pass, run `python -m app.jobs run-due`.

Click a game on the slate to open its matchup page (`/game/<id>`): records against the spread,
season efficiency with league ranks, rest and travel, injuries (with a flag when last week's
starting QB is on the report), weather, and head-to-head. Everything shown is computed only from
what was known before kickoff, and each tab says when its data was last refreshed. This is
context, not betting advice. Sources: nflverse team stats (daily), ESPN injuries (daily) and
Open-Meteo forecasts (every 3 hours); all free, none need a key. The scheduled `run-due` picks
up `team_stats`, `injuries` and `weather` on its own; `team_stats_history` is manual.

### Hosted scheduler

Production scheduling uses cron-job.org to dispatch `.github/workflows/run-due.yml`, not Windows
Task Scheduler. Before enabling it:

1. Merge the scheduler workflow and application changes to `master`.
2. Deploy that exact commit with
   `$revision = git rev-parse HEAD; fly deploy --build-arg "APP_REVISION=$revision"`. The Fly
   release command applies migration `0005`, and `fly.toml` enables `NBA_CONTEXT_PROVIDER=espn`.
3. Confirm the deployment is healthy. The workflow deliberately fails if its `GITHUB_SHA` does
   not match the image's embedded `APP_REVISION`, so a merge without a matching deploy cannot run
   stale scheduler code.

Then configure one cron-job.org job with:

- URL: `https://api.github.com/repos/isaacsilver13/betting_aggregator/actions/workflows/run-due.yml/dispatches`
- Method: `POST`
- Schedule: every 15 minutes, timezone `America/Chicago`
- Body: `{"ref":"master"}` with content type `application/json`
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <GITHUB_TOKEN>`, and
  `X-GitHub-Api-Version: 2026-03-10`
- Default 30-second request timeout; saved responses disabled

Use a fine-grained GitHub token limited to this repository with Actions write permission. Store it
only in Bitwarden and cron-job.org; never put it in the URL, repository, request body, or job title.
Add `DATABASE_URL` as a GitHub Actions secret alongside `FLY_API_TOKEN_API`. GitHub accepts the
dispatch immediately, then the workflow runs the database-only `python -m app.jobs check-due`
preflight against PostgreSQL. It records the scheduler heartbeat but makes no provider calls. When
no slot is due, the workflow exits without contacting Fly. When work is
due, it starts the existing Fly machine, keeps that exact machine awake with health requests,
verifies its deployed revision, and runs `python -m app.jobs run-due` over Fly SSH. The existing
`FLY_API_TOKEN_API` Actions secret authorizes the Fly step. The preflight plans paid slots without
holding or calling the Odds API key. A PostgreSQL advisory lock prevents overlap
across workflow, Fly, and manual CLI invocations (SQLite uses a process lock locally). Successful
passes record the heartbeat shown on `/ops`; a terminal job failure makes the Actions run fail.
Unavailable hours are marked `missed` once and are not replayed.

### Prices, bets and closing-line value

The matchup page opens on a **Lines** tab: every tracked book's spread, total and moneyline
(DraftKings, FanDuel, BetMGM, BetRivers, theScore Bet), the consensus with the vig removed, the best
price on each side, a line-movement chart, and Kalshi when it is enabled. Click any price to open
the bet form pre-filled; the fee (FanDuel and DraftKings charge one under $25 and $50) is worked out
and stored with the bet. `/bets` shows profit after fees, ROI, and how often you beat the closing
line, by book, sport or market. `/ops` shows whether the scheduled jobs are actually running.

Prices come from The Odds API and cost credits, so they are **off until you turn them on**: set
`PROVIDER_MODE=live`, `ODDS_PROVIDER=the_odds_api` and a free `THE_ODDS_API_KEY` in `.env`. Then
`run-due` takes a snapshot at 10am CT on Tuesday, Thursday, Saturday and Sunday and 30 minutes
before each kickoff time. Each call is 3 credits and covers every game, so a week is about 30
credits against the free 500 a month. Every paid response is written to `data/raw/` (git-ignored)
before it is parsed, so a crash never costs a second call, and the job stops at the
`PROVIDER_MIN_QUOTA_REMAINING` floor. The Lines tab's *Refresh prices* button makes one extra
call on demand (at most one per 10 minutes; it only shows for requests from this PC unless `ADMIN_REFRESH_SECRET` is set). The pre-kickoff snapshot is the closing line used
for CLV; ESPN's close is the fallback. `grade_bets` runs hourly: it settles bets on final games
and copies the closing line onto bets whose game has started. Context, not betting advice.

### NBA

The NBA lives next to the NFL: `/nba` is a day-by-day slate (with previous and next game day), and
each matchup page has Lines, Records, Ratings, Schedule, Injuries and Head-to-head. Ratings are
season-to-date offensive, defensive and net rating, pace and the four factors with league ranks;
Schedule counts back-to-backs, third games in four days, fourth in six, and travel. Everything is
computed only from games before tip-off, and ATS and O/U use ESPN's closing line (DraftKings).

1. `python -m app.jobs seed` seeds the NBA teams along with the NFL's.
2. `python -m app.jobs nba_history` loads the season so far: ESPN day by day, then each finished
   game's closing line. It takes a few minutes and is free (no key).
3. `run-due` then keeps it current: `nba_scoreboard` (hourly), `nba_closes` (daily),
   `nba_injuries` (10:00, 16:00 and 22:00 Central) and `nba_link_events`. NBA team/player
   context is disabled by default. To opt into the same zero-key ESPN feed, set
   `NBA_CONTEXT_PROVIDER=espn`; `nba_team_stats`
   then runs hourly from 08:00 through 11:00 Central for uncovered completed-game dates. Never-
   checked and least-recently checked dates go first, so a missing provider date cannot block newer
   games. Each run handles at most four games: one scoreboard request plus one summary request per
   game, or at most five requests. Four runs can cover a 15-game day in 19 requests and skip games
   already stored successfully. A manual
   `python -m app.jobs nba_team_stats` run rechecks yesterday's least-recently checked four games
   so provider corrections remain ingestible without an unbounded burst.

The ESPN JSON feed is undocumented and best-effort, even though the app already uses it for the
schedule, closes, and injuries. The integration is intended for this private/local app; a public or
commercial betting product needs a separate data-rights review. The Health page reports exact
locally attempted requests, last successful coverage, and the last persistence error. HTTP or
parser failures remain job errors; they are never recorded as empty successful refreshes.

Team and player box scores are append-only observations, so historical context ignores data first
seen after its matchup cutoff; preseason games are excluded from ratings and star samples. Stars
are the top three players by minutes per game: prior-season minutes remain the baseline until five
current-season team games are stored; on a fresh install without prior-season rows, the available
early current-season sample is used instead. ESPN also remains the best-effort NBA injury source. Every
successful poll writes a snapshot header, including an empty
all-clear, and reports older than 36 hours do not keep showing players as injured. The
`star_overrides` and `injury_overrides` tables support explicitly bounded `starts_at`/`ends_at`
manual corrections with provenance; a manual healthy status stays visible and does not make stale
ESPN data look fresh. There is intentionally no public mutation endpoint. Apply migration
`0005_free_nba_context` with `alembic upgrade head` before enabling the provider.

NBA prices use the same Odds API key and the same credit pool. With `PROVIDER_MODE=live`,
`odds_snapshot_nba` takes a snapshot at 10am Central every day and shortly before the first and
the last tip of each night (one call, 3 credits, covers every game), so about 9 credits a day.
Games that tip between those two get ESPN's close for CLV instead of a multi-book consensus. In
October through February, with the NFL's roughly 30 credits a week, the two sports use about 400 of
the free 500 credits a month; `PROVIDER_MIN_QUOTA_REMAINING` still stops everything at the floor.

## Run locally

### Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker compose up -d postgres
alembic upgrade head
uvicorn app.main:app --app-dir backend --reload --port 8000
```

To load deterministic sample odds without spending provider credits:

```powershell
$env:ODDS_PROVIDER = "fixture"
$env:PERSISTENCE_ENABLED = "true"
uvicorn app.main:app --app-dir backend --reload --port 8000
```

To use real NFL lines from The Odds API locally, copy `.env.example` to `.env`,
fill in the key, and select live mode before starting the backend:

```dotenv
# .env (keep this file untracked)
THE_ODDS_API_KEY=replace-with-your-key
PROVIDER_MODE=live
ODDS_PROVIDER=the_odds_api
PERSISTENCE_ENABLED=false
```

PowerShell variables take precedence over `.env` if you prefer session-only
configuration:

```powershell
$env:THE_ODDS_API_KEY = "replace-with-your-key"
$env:PROVIDER_MODE = "live"
$env:ODDS_PROVIDER = "the_odds_api"
$env:PERSISTENCE_ENABLED = "false"
uvicorn app.main:app --app-dir backend --reload --port 8000
```

Then verify the provider and request NFL odds:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/odds?sport=nfl"
```

The response should report `the_odds_api` as the configured provider and return
the current events, bookmakers, moneylines, spreads, totals, and any alternate
spreads returned by the upstream API. The frontend uses the same response and
does not receive or manage the API key.

`THE_ODDS_API_KEY` takes precedence over fixture mode when both are set, but
`PROVIDER_MODE=live` is still required. Never commit a real key. Set
`PERSISTENCE_ENABLED=false` to run without a database. Migrations are
reversible with `alembic downgrade base`.

`PROVIDER_MODE=fixture` is the application default when no environment is
provided and never makes live provider calls. Set `PROVIDER_MODE=live`
explicitly before using `THE_ODDS_API_KEY`. The
`PROVIDER_MIN_QUOTA_REMAINING` setting defaults to `5`; when a live provider
reports that remaining quota is at or below this value, the cache suppresses
another refresh and serves the last successful snapshot when one exists.

With persistence enabled, observations and refresh runs older than
`OBSERVATION_RETENTION_DAYS` are pruned after each refresh. Failed provider
refreshes are recorded with their typed error and are not stored as empty
successful observations.

The API surface is intentionally read-only:

- `GET /api/v1/health`
- `GET /api/v1/status`
- `GET /api/v1/odds?sport=nfl|nba`
- `GET /api/v1/odds?sport=...&event_id=...&market_type=...&force_refresh=true`
- `GET /api/v1/history?event_id=<canonical-id>&market_type=<market>&limit=...`

When the server reports that public expensive refresh is safe (fixture mode), the comparison
board includes refresh actions for the board and individual events. Live public clients use the
server cache instead.

### Paid-request controls

`force_refresh=true` and `include_props=true` can spend provider quota, so they only
take effect with the `X-Admin-Token` header matching `ADMIN_REFRESH_SECRET`. Anonymous
requests are still answered (200) from the cache / last saved snapshot, with an
`X-Refresh-Ignored: unauthorized` response header. In live mode with no secret
configured these actions are disabled. Admin refreshes are throttled per client and by a
global budget (`ADMIN_REFRESH_BUDGET` per `ADMIN_REFRESH_BUDGET_WINDOW_SECONDS`, both
process-local); throttled calls get `X-Refresh-Ignored: throttled`. `/health/errors`
(redacted) and quota figures require the same header; `/health` and `/status` stay public.

The browser never receives `ADMIN_REFRESH_SECRET`. It hides protected refresh controls when the
public capability is disabled and reports an ignored/throttled response instead of claiming a
refresh succeeded. Populate player props from a trusted operator session by calling `/odds` with
the admin header; never put the secret in frontend configuration or a browser bundle.

There is no automated bet placement or sportsbook account integration.

### Frontend

```powershell
cd frontend
npm install
npm run dev
npm test
npm run test:browser
```

The Vite development server proxies `/api` to `http://127.0.0.1:8000`.
`npm run test:browser` starts credential-isolated fixture servers and runs the responsive Playwright
smoke test in desktop and mobile viewports. It never reads the repository `.env`.
The multi-stage Docker build still compiles this frontend and has FastAPI serve it from the same
origin as `/api/v1`, but nothing deploys it: the app is local-only (see "Local matchup hub").
