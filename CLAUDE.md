# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Standalone NFL/NBA odds comparison app: FastAPI backend with a canonical event/odds contract, a provider protocol (fixture / stub / The Odds API), optional PostgreSQL persistence for refresh runs and immutable offer observations, and a React/Vite frontend. The API surface is intentionally read-only — there is no automated bet placement or sportsbook account integration.

## Commands

Backend (Python >=3.9, package sources under `backend/`, installed via `pyproject.toml`):
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker compose up -d postgres
alembic upgrade head                                   # apply migrations (reversible: alembic downgrade base)
uvicorn app.main:app --app-dir backend --reload --port 8000
pytest                                                  # full suite (testpaths = backend/tests)
pytest backend/tests/test_api.py::test_name             # single test
ruff check .                                            # lint
```
To develop without spending provider credits: `$env:ODDS_PROVIDER = "fixture"` before starting uvicorn (deterministic sample odds, no live calls). `PROVIDER_MODE=fixture` is the default when unset. To hit real NFL/NBA lines, set `THE_ODDS_API_KEY`, `PROVIDER_MODE=live`, `ODDS_PROVIDER=the_odds_api` (never commit the key). `PERSISTENCE_ENABLED=false` runs without a database.

Frontend (from `frontend/`): `npm install`, `npm run dev` (Vite dev server proxies `/api` to `127.0.0.1:8000`), `npm run build`, `npm run lint` (this project's "lint" is `tsc -b --pretty false`, not eslint).

## Architecture

- `backend/app/providers/` holds the provider protocol (`base.py`), the credential-free `fixture.py` and `stub.py` implementations, and `the_odds_api/` (client + adapter) for the live integration. `cache.py` implements single-flight locking, stale last-known-good fallback, retry/backoff, and quota-aware throttling — when a live provider's remaining quota drops to/below `PROVIDER_MIN_QUOTA_REMAINING` (default 5), the cache suppresses further refresh and serves the last good snapshot instead of calling out again.
- `backend/app/domain/` defines the canonical event/odds contract: event IDs normalize team aliases and five-minute start-time buckets, while provider-specific offer IDs stay attached to each individual observation — the two ID schemes are deliberately not conflated.
- `backend/app/storage/` + `backend/migrations/` (Alembic) persist refresh runs, events, provider mappings, and immutable offer observations; failed refreshes are recorded with a typed error rather than stored as empty successes, and rows older than `OBSERVATION_RETENTION_DAYS` are pruned after each refresh.
- `backend/app/providers/kalshi/` is a read-only prediction-market adapter (public Kalshi endpoint, no key). Its contracts use a separate `PredictionContract` model and `/prediction-markets` route on purpose -- do not merge them into `Offer`/`/odds`. `KALSHI_ENABLED` defaults off and `KALSHI_BASE_URL` defaults to the official public Trade API host.
- `backend/app/api/routes.py` exposes only: `GET /api/v1/health`, `/status`, `/odds`, `/odds?...&force_refresh=true`, `/history?event_id=...&market_type=...`, `/prediction-markets?sport=...`. A per-event refresh action re-fetches only the selected market for that event and merges it into the existing board without touching other events/markets.
- No code under `backend/app` currently references player-prop markets — if a task involves prop odds, treat that as unimplemented rather than assuming existing plumbing to extend.
- Deployed as one Fly.io app, `betting-aggregator-api`, from `fly.toml`. `docker/Dockerfile` builds the React bundle and copies it into the FastAPI image; FastAPI serves the SPA and `/api/v1` on the same origin. The release command runs Alembic migrations before each deploy.
