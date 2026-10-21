# Scheduled refresh — refresh on a schedule, not on open

Status: complete (code). Step 1, registering the scheduled task, is the owner's call and not done here.
Scope: this repository only, from the workspace plan of the same name (part 4a; the AIsaac part is
a separate repository).

## Goal

Data refreshes on a schedule; opening a page only reads stored data; every existing Refresh button
still refreshes on demand.

## Constraints and non-goals

- Local-only by design (no deploy). On-demand refresh stays limited to the owner's machine.
  Odds API free tier: 500 credits/month, shared by NFL + NBA.
- The game-day-aware scheduler already exists (`backend/app/jobs/`, `python -m app.jobs run-due`;
  `OddsSnapshotJob`: NBA 10:00 CT plus first/last tip, NFL Tue/Thu/Sat/Sun 10:00 plus pre-kick).
  `BettingAggregatorRunDue` was not registered on the owner's PC (checked 2026-10-21).
- Task Scheduler actions use absolute paths, which break on the planned workspace move
  `nba_prediction` → `codebase`: register after the move or re-register as part of it.
- Non-goal: Kalshi `/prediction-markets` (free, keyless, 300s TTL; live is the point).

## Approach

1. **Turn the scheduler on** (owner): point `scripts/register_run_due_task.ps1 -RepoRoot` at a
   checkout on `master` with its own `.venv`, `.env` and `alembic upgrade head`.
2. **No spend on open.** In live mode, `GET /api/v1/odds` without `force_refresh`/`include_props`
   no longer refreshes from the provider when the stored snapshot is over a day old. It serves
   `OddsRepository.latest_comparisons` (which `OddsSnapshotJob` writes), or the in-memory board when
   persistence is off. Fixture mode costs nothing and still fetches its sample board, which keeps
   the persistence-off browser smoke test's board populated.
3. **Buttons work on the owner's machine.** `_may_spend` moved from `api/lines.py` to `api/routes.py`
   and now gates `force_refresh`/`include_props` and `capabilities.public_expensive_refresh`. The
   board's on-load fetch no longer sends `include_props`; props load with "Refresh odds". Quota
   floor, 429 cooldown and admin budget unchanged.
4. `CACHE_TTL_SECONDS` stays: `CachedOddsProvider` still reads it. Its `.env.example` comment and the
   README, `CLAUDE.md` and `docs/design-overview.md` are updated.

## Verification (2026-10-21)

- `pytest`: 450 passed (`OddsRepository.last_success_at` and its test went with the daily gate).
  The stored board drops games that started more than 4 hours ago (the store keeps 90 days); fixture
  mode never takes that path, so its past-dated sample games still show. New tests in `backend/tests/test_api.py`: a plain GET serves the stored
  snapshot and never calls the provider (with and without a database); a refresh stays in memory with
  persistence off; force refresh is honored on loopback with no secret; a forwarded force refresh never
  reaches the provider; the capability is true on loopback with no secret, false with
  `x-forwarded-for`, `fly-client-ip`, a non-loopback peer, or a secret set.
  `backend/tests/test_security.py` updated for the new rule.
- `ruff check .`: clean. Frontend `npm run lint` and `npm test` (66 passed).
- Manual (after registration, owner): `Get-ScheduledTaskInfo BettingAggregatorRunDue` shows runs;
  `/ops` shows `odds_snapshot_nba` `done` or `empty` for today; opening `/odds` adds no
  `provider_refresh_runs` row.
