# Forward NBA paper trial activation

Status: isolated branch migrated; private handoff published and stale-reference preview prepared (2026-10-22).
Scope: this repository's paper ledger, NBA reports, and existing data jobs. No deployment or email activation.

## Current state

- The configured Neon URL points to the `production` branch at migration `0006_model_picks`.
  The older Fly app is running against that branch, and its cron `run-due` job is enabled.
- Local migrations `0007` through `0011` create the report, private access, and paper-ledger tables.
- The Odds API zero-credit `/v4/sports/` response reports -4 credits remaining. Its last stored
  quota value is stale, so the running job can attempt paid odds calls that will fail.
- The isolated Neon branch `nba-paper-trial-2026-10-22` is at `0011_paper_ledger`. A one-time
  ESPN NBA scoreboard run succeeded on that branch. Production remains at `0006_model_picks`.
- The GitHub NBA producer sends forecasts to the older Fly ingestion endpoint. The new local
  forecast intake has no current-day file and is not yet wired to that producer.
- The producer workflow change was published as NBA repo commit `e3e4a2e`. Its next completed
  daily run will attach a forecast to the existing private data release while retaining the
  bet-watch POST. A completion asset makes partial releases repairable on retry; the Oct 22
  release predates this change and has no forecast asset.
- Saved ESPN `current` lines cover the upcoming slate, but have no source quote-update time or
  verified sportsbook identity. They support a labeled reference preview, not portfolio wagers.

## Activation sequence

1. Add a zero-credit quota preflight before scheduled paid odds calls. On an unavailable or
   unparseable quota, fail closed. Verify with a mocked provider response.
2. Rehearse migrations `0007`-`0011` on an isolated Neon branch copied from the configured
   database. Completed; production's revision and table set were checked unchanged.
3. Point only the trial runner at the isolated branch. Keep the existing Fly app and
   production database on their current version.
4. Publish the private forecast handoff and import a same-day asset before tip. Configure a
   selective NBA runner for the trial. Do not
   turn on a second all-sports scheduler or a second newsletter sender.
5. Start the eleven independent $1,000 paper profiles at 3% net EV, quarter Kelly, and the
   agreed caps. Record provenance and input freshness per slate; settle only observed bets.
6. Audit the first eligible slate's source forecasts, odds, allocation, and settlement. Label
   the output as forward paper results.

## Verification and current stop point

- Neon migration `0006` → `0011` completed on the isolated branch using its direct host.
  Read-only SQL confirmed production stayed on `0006` without the new tables.
- A one-time live ESPN NBA scoreboard job completed on the isolated branch.
- Backend regression suite: 517 passed before the final quota-race test; that affected test
  file then passed 16/16. Ruff passed on all files changed for the quota guard.
- The trial has no eligible paper slate yet: the provider reports -4 credits, and the
  next private forecast asset has not yet been produced. No portfolio allocation has
  been fabricated from stale data. The stale ESPN comparison is a separate research
  preview with no paper wagers.

## Current gates

- No paid odds request from the new local code until the provider reports credits above the
  configured floor. The running Fly app still uses older code and is unaffected by this guard.
- No forecast-dependent comparison until a same-day forecast is received and checked.
- Saved ESPN odds may be used in a labeled gross-EV reference preview. They cannot seed the
  paper ledger's five-book net-EV, Kelly, or top-N profiles.
- No Fly deployment, email delivery, account upgrade, or automatic real bet placement is
  part of this activation.
