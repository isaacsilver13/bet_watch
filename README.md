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

This app is **local-only**: it runs on your PC against docker-compose Postgres and will hold a
personal bet log. CI no longer deploys; the existing Fly app is left as it is until the owner
decides what to do with it. Analysis here is statistically honest context, not betting advice.

1. `docker compose up -d postgres`
2. `copy .env.example .env` (defaults: fixture odds, persistence on)
3. `alembic upgrade head`
4. `python -m app.jobs seed`, then `python -m app.jobs nfl_history`, then
   `python -m app.jobs espn_scoreboard`, then `python -m app.jobs espn_closes`
5. `uvicorn app.main:app --app-dir backend --reload --port 8000`, and `npm run dev` in `frontend/`
6. Open `http://localhost:5173/nfl`. The odds board is at `/odds`.
7. Optional: `powershell -ExecutionPolicy Bypass -File scripts/register_run_due_task.ps1` runs
   `python -m app.jobs run-due` every 15 minutes while the PC is on. Hours the PC was off are
   marked `missed` once and never retried.

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
