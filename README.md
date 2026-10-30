# Bet Watch

An NFL and NBA odds comparison and research app with a private bet journal. It collects prices and game context, compares books, tracks recorded bets, and reports model observations. It does not place bets or connect to sportsbook accounts.

## Current capabilities

- Odds boards and matchup pages with lines, injuries, team context, and source timestamps.
- Scheduled collection with raw-response retention, quota controls, and retry handling.
- A private journal with settlement, fees, and closing-line comparisons.
- NBA forecast intake and an isolated paper-trial workflow.
- Fixture data for local development without provider credentials.

FastAPI and SQLAlchemy provide the backend; React, TypeScript, and Vite provide the frontend. PostgreSQL persistence is optional for the basic fixture odds board and required for database-backed features.

## Start with fixture data

From the repository root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:PROVIDER_MODE = "fixture"
$env:ODDS_PROVIDER = "fixture"
$env:PERSISTENCE_ENABLED = "false"
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173/odds`. This mode demonstrates the odds board; the journal and matchup data need a database and seed/import steps.

## Verification and operations

Run `python -m pytest -q` from the root. In `frontend/`, run `npm test`, `npm run build`, and the separately configured `npm run test:browser` smoke suite.

The [operations guide](docs/operations-guide.md) contains database setup, provider settings, scheduled jobs, forecast handoff, and access controls. The [source](backend/app/main.py) includes an optional owner-login path as well as local/owner-token access; production access depends on its explicit configuration. The code being public does not make the journal public.

Provider coverage and quotas can change. Historical simulations and paper results are research observations, not validated predictions of future performance.
