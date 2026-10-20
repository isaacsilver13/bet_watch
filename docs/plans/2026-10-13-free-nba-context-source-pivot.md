# Betting Aggregator — Free NBA context source pivot

Status: implemented; ESPN live smoke passed; operational enablement intentionally pending
Scope: `repos/betting_aggregator` NBA context ingestion, persistence, API, and badges
Date: 2026-10-13

## Goal

Replace the planned NBA.com statistics and official-injury-PDF integrations with a
zero-recurring-cost path that can supply season-to-date team context and player
minutes without changing the existing odds-provider budget.

The first implementation target is the local/private app described in `CLAUDE.md`.
No deployment, production flag change, paid subscription, or scheduler registration
is part of this plan.

## Provider decision

- Use the existing zero-key ESPN scoreboard and summary feeds for completed-game team
  and player box scores, in addition to schedules, closes, and injuries.
- Strengthen ESPN injury snapshot and freshness semantics instead of adding an NBA
  injury-report PDF parser.
- Keep nflverse unchanged for NFL injuries.
- Do not call `stats.nba.com`, `data.nba.com`, or an NBA.com wrapper.
- Do not add `pypdf`.
- Do not use Big Balls Sports Data for NBA injuries while its documentation reports
  that the NBA upstream feed is paused.

The originally implemented API-Sports path failed its 2026-10-13 live evidence gate:
the free key allowed only dates 2026-10-13 through 2026-10-15 while allowing only
seasons 2022 through 2024. Those constraints have no useful intersection for current
NBA data. No API-Sports observations were stored, and that adapter was removed.

ESPN's feed is zero-key but undocumented. Treat it as appropriate for this
private/local app, not as authorization for a public or commercial data product.
Revisit reliability and data rights before any public deployment.

### Live evidence (2026-10-13)

- API-Sports: three approved requests authenticated but could not produce a usable
  sample. The free plan rejected a historical date, then reported mutually exclusive
  current-date and season ranges. No response was persisted.
- ESPN: two read-only requests for 2026-04-12 selected game `401811041` (ORL at BOS).
  The production parser produced two team rows and 28 player rows: 18 positive-minute
  rows and 10 explicit DNP/zero-minute rows. Final scores were BOS 113, ORL 108; both
  team IDs matched the scoreboard, and regulation minutes were 48. No response was
  persisted during the smoke test.

## Constraints and non-goals

- No recurring provider cost.
- Fixture/disabled mode remains the default and needs no network access.
- Store provider observations with their observed/effective times. Historical matchup
  context must use only information available before the matchup cutoff.
- A successful empty response is data, not a failed refresh.
- Do not add player props in this slice.
- Do not redesign the existing odds, Kalshi, bet, or NFL pipelines.
- Do not register or enable scheduled jobs until the free-tier live smoke test passes
  and the user separately approves operational enablement.

## Approach

### 1. Retire the NBA.com path behind a safe default

1. Make the existing `nba_team_stats` job disabled unless a supported NBA context
   provider is configured.
2. Add configuration for `NBA_CONTEXT_PROVIDER=disabled|fixture|espn`; ESPN needs no
   provider key.
3. Remove the NBA.com client from the active job graph. Delete it only when its
   replacement tests cover the same repository and analytics boundaries.
4. Update `.env.example` and operational health output with exact local request counts.

Exit gate: the default configuration makes no NBA context network request, and the
old NBA.com code cannot be selected accidentally.

### 2. Extend the existing ESPN adapter narrowly

Implement and contract-test only the calls required by this feature:

1. Find completed regular/postseason NBA games for a date.
2. Fetch one completed game's summary, including team and player box scores.
3. Normalize provider team/player/game IDs without replacing the repository's
   canonical team or game identities.
4. Treat null statistics, DNP rows, malformed responses, and partial games as explicit
   outcomes.

All parser tests use committed synthetic JSON fixtures. HTTP failures remain visible
to the bounded job runner.

Exit gate: fixtures produce source-neutral records and failures never become empty
successful snapshots.

### 3. Persist append-only, point-in-time records

1. Reuse the current game/team-stat persistence where its grain is already one team
   in one completed game.
2. Add the smallest migration needed for player game minutes if no suitable table
   exists. Store provider ID, canonical team, game, minutes, observed time, and source.
3. Make re-ingestion idempotent for the same provider game/player observation while
   preserving corrections as later observations when values change.
4. Record a refresh/snapshot header containing source, observed time, covered game
   date, completion state, and row count.
5. Query ratings and stars with an explicit `before` cutoff.

Exit gate: replaying a fixture creates no duplicate facts, a corrected fixture remains
auditable, and future observations cannot affect an earlier matchup.

### 4. Ingest incrementally with a bounded request footprint

1. Process newly completed games rather than refetching every player or team each run.
2. Run at 08:00, 09:00, 10:00, and 11:00 Central, process at most four games per run,
   and spend no more than five requests in a run (one scoreboard plus one summary per
   game). A full 15-game day costs about 19 requests across the four batches, before
   bounded retries.
3. Cache stable reference data and resume partial days without repeating completed
   work.
4. Expose daily request count, last success, last covered game date, and last error in
   ops health.
5. Backfill slowly and resumably; do not turn the undocumented feed into an unbounded
   scraper.

Exit gate: request-cap tests prove a restart does not repay for stored games.

### 5. Derive ratings and star players locally

1. Continue computing team context from stored completed-game facts; do not store a
   provider's mutable season aggregate as the only source of truth. Exclude preseason
   games from regular/postseason ratings and star samples.
2. Preserve the analytics module's `before` cutoff and label any simplified metric
   accurately if the new box score lacks an input required by the current rating.
3. Rank each team's players by minutes per game using observations available before
   the cutoff.
4. Use prior-season minutes as the opening baseline when those rows are loaded. Switch
   a team to current-season minutes after five completed team games; on a fresh install
   with no prior-season rows, use the available early current-season sample instead.
5. Add an effective-dated manual star override for roster changes, missing data, and
   obvious provider errors. Manual values must not silently become permanent.

Exit gate: unit tests cover season rollover, ties, trades/team changes, DNP rows,
minimum-sample fallback, overrides, and historical cutoffs.

### 6. Repair NBA injury snapshot semantics

1. Keep ESPN as the automated NBA injury input and label it as best-effort.
2. Write a snapshot header after every successful poll, including a poll that returns
   zero injuries.
3. Scope snapshots by sport and coverage time; reject stale or incomplete snapshots
   instead of carrying their rows forward indefinitely.
4. An explicit healthy/available state clears an older injury. A successful all-clear
   snapshot clears prior rows within its coverage.
5. Add effective-dated manual overrides with source and note fields. Manual overrides
   take precedence only during their stated effective window.
6. Surface source and `data_as_of` so the UI can distinguish fresh, stale, unavailable,
   and manually overridden data.

Exit gate: regression tests prove that an old `Out` row cannot override a later
all-clear, available, expired, or manual state.

### 7. Add API fields and badges after ingestion is trustworthy

1. Add star status and its source/as-of metadata to the matchup context.
2. Add slate-card star and recorded-bet badges without coupling them to refresh logic.
3. Show injury freshness and manual/stale state on the Injuries tab.
4. Preserve usable cards when NBA context is unavailable; context failure must not
   clear odds or block the slate.

Exit gate: frontend tests cover fresh, stale, manual, empty, and unavailable context,
plus responsive and keyboard-visible rendering.

### 8. Documentation and controlled enablement

1. Document provider setup, quota math, private-use licensing boundary, source
   freshness, manual overrides, scheduler cadence, and rollback.
2. Run a user-approved live smoke test against one completed ESPN summary. Compare
   IDs, minutes, team totals, and null/DNP behavior with parser output.
3. Keep the job disabled if coverage or data quality is insufficient. The rollback is
   configuration-only: return to fixture/disabled context while preserving stored
   observations.
4. Have a second agent independently review the meaningful implementation changes,
   resolve confirmed findings, and rerun affected checks before any PR or enablement.

Exit gate: documentation matches observed behavior, the live sample is recorded, and
all enablement remains an explicit later decision.

## Verification

- Automated backend: focused adapter/parser, persistence, cutoff/leakage, request-cap,
  injury-snapshot, ratings, star-picker, API, and ops-health tests.
- Automated frontend: affected Vitest tests, `npm run lint`, and `npm run build`.
- Full regression: `pytest` and `ruff check .` from the repository worktree.
- Manual fixture smoke: NBA day slate, matchup context, Injuries tab, star marks, bet
  badges, unavailable-provider state, and stale-provider state.
- Live smoke: one completed game only; no bulk backfill during the first check.

## Risks and decisions

- ESPN remains an undocumented best-effort dependency for context and injuries.
  Strict parsing, manual overrides, visible freshness, and disabled-by-default
  configuration are required safeguards.
- Endpoint shape or availability can change. Parser failures must remain visible in
  job and ops health rather than becoming empty successful refreshes.
- Public/commercial deployment needs a separate provider/licensing decision.
- If required possession or four-factor inputs are unavailable, ship accurately named
  basic team context or no rating; do not present a substitute as offensive or
  defensive rating.
- The five-game star-switch threshold is the proposed default. Change it only with a
  documented product decision and regression tests.

