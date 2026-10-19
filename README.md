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

This app is **local-only**: it runs on your PC against docker-compose Postgres and will hold
private data. CI no longer deploys; the existing Fly app is left as it is until the owner
decides what to do with it. Analysis here is statistically honest context, not betting advice.

1. `docker compose up -d postgres`
2. `copy .env.example .env` (defaults: fixture odds, persistence on)
3. `alembic upgrade head`
4. `python -m app.jobs seed`, then `python -m app.jobs nfl_history`, then
   `python -m app.jobs espn_scoreboard`, then `python -m app.jobs espn_closes`, then (once)
   `python -m app.jobs team_stats_history`, then `python -m app.jobs injuries` and
   `python -m app.jobs weather`
5. `uvicorn app.main:app --app-dir backend --reload --port 8000`, and `npm run dev` in `frontend/`
6. Open `http://localhost:5173/nfl`. The odds board is at `/odds`.
7. Optional: `powershell -ExecutionPolicy Bypass -File scripts/register_run_due_task.ps1` runs
   `python -m app.jobs run-due` every 15 minutes while the PC is on. Hours the PC was off are
   marked `missed` once and never retried.

Click a game on the slate to open its matchup page (`/game/<id>`): records against the spread,
season efficiency with league ranks, rest and travel, injuries (with a flag when last week's
starting QB is on the report), weather, and head-to-head. Everything shown is computed only from
what was known before kickoff, and each tab says when its data was last refreshed. This is
context, not betting advice. Sources: nflverse team stats (daily), ESPN injuries (daily) and
Open-Meteo forecasts (every 3 hours); all free, none need a key. The scheduled `run-due` picks
up `team_stats`, `injuries` and `weather` on its own; `team_stats_history` is manual.

**Official injury reports.** `nfl_injuries_official` reads nflverse's injury file (daily, and again at 18:00 Central on Wednesday to Friday) once the file has reached the week about to be played, and `nba_injuries_official` reads the league's injury-report PDF every hour from 10:00 to 22:00 Central (the newest of the last four quarter hours; this is why the project depends on `pypdf`). Each source keeps its own snapshots and the matchup page merges them by player: the league's report wins and ESPN fills in the rest. For the NBA, `nba_player_stats` (daily, from stats.nba.com) finds each team's three players with the most minutes per game (last season's until a team has played 10 games); the slate card and the Injuries tab flag a team whose star is Out or Doubtful and mark one who is Questionable. A game that carries bets shows "N bets · M open" and its profit after fees once settled. Run `alembic upgrade head` once for the new `player_season_stats` table.

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
   `nba_team_stats` (daily, from stats.nba.com), `nba_injuries` (10:00, 16:00 and 22:00 Central) and
   `nba_link_events`.

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


## Private local application boundary

This application holds a private bet journal and is local-only. Bind the API and
Vite to loopback. Direct requests must have a loopback peer and localhost/loopback
Host, with no forwarding headers or hostile browser Origin/Fetch Metadata.
The default Vite origins on port 5173 and the API origin are accepted. Other requests require the existing owner
`X-Admin-Token`; keep it out of client bundles and URLs. Only aggregate health/status
GET endpoints remain public. This boundary also protects journal-derived badges,
operations and static UI, rather than protecting only `/bets` while leaking data
through another route. CI intentionally does not deploy this app.

`run-due` continues processing independent jobs after failures, but logs a failed
heartbeat and exits nonzero for incomplete/failed/missed work. A fresh heartbeat
means the runner executed; `run_due_status` separately records its outcome.
