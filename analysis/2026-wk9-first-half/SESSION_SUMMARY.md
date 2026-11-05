# Session summary: NFL Week 9 first-half analysis and the bet_watch First half feature

Written 2026-11-05. Paste this into a new session to continue. Repos are under github.com/isaacsilver13/.

## Goal
Compare NFL Week 9 matchups on typical stats and on first-half performance (leading at the half, winning the game), building on the existing repos.

## What the repos already had
- `bet_watch` (public): the right base. FastAPI + React app with a week board, `/games/{id}/context` (records, EPA ranks, situational spots, injuries, h2h), nflverse ingest, and `backend/scripts/week_matchups_html.py`. It had **no** first-half or halftime data.
- `nfl_ats` (private): historical ATS analysis since 2000, `data/processed/team_games.parquet|csv`. Untouched. Useful later for first-half ATS angles.
- `NFL_Confidence`: a pick'em app. Not relevant. Untouched.

## Week 9 analysis (one-off, in this folder)
Data: 2026 REG through week 8 (all 121 games final), from nflverse play-by-play and schedule (CC-BY-4.0). Week 9 is 15 games, Thu JAX@BAL through Mon BUF@MIN; PIT and TEN on bye. No Week 9 spreads were posted in nflverse yet.
- `week9_matchups.md` / `.csv`: per game, full-game stats, first-half stats, halftime-lead-to-final records, scored-first records, league ranks.
- League baselines: 23.1 PPG, 11.2 first-half PPG, teams leading at half won 76% (84 of 111), scoring first won 62%.
- Largest first-half mismatches (net EPA/play): LV@SF (favors SF), LA@WAS (LA), HOU@LAC (HOU). Closest: TB@CHI, ARI@SEA, JAX@BAL, NYG@PHI, CLE@NO.
- JAX@BAL: BAL is +7.0 first-half margin but -10.0 second half, only 3-3 when leading at half. JAX is +10.0 first half (#1) with the #1 first-half defense (EPA -0.215).
- Samples are 7-8 games per team: directional only.
- Scripts: `build_week9.py <raw_dir> <out_dir> [week]` (pandas), `render_week9.py <out_dir> <week>`. `raw_dir` needs `games.csv` (nflverse schedules) and `pbp.csv` (nflverse play_by_play_2026.csv).

## Feature built and deployed: First half tab (bet_watch PR #25, merged as c738ce9)
- `backend/app/ingest/nflverse_pbp.py`: reduces play-by-play to a `HalfRecord` per team and game. Halftime score is read from the first Half2 row, because nflverse `total_home_score/total_away_score` is the score at the START of a play. Also scored-first and first-half offense (plays, EPA, success, pass/rush split, turnovers, sacks). Defense = opponent's offense in the same game.
- Turnovers count scrimmage plays only (matches `analytics/efficiency.py`). The earlier pandas report also counted special-teams turnovers, so a few first-half turnover counts differ by one.
- `game_half_stats` table, migration `0012_game_half_stats`. Separate from `team_game_stats` because the daily team-stats job rewrites that JSON wholesale.
- Jobs in `backend/app/jobs/context.py`: `half_stats` (daily) and `half_stats_history` (manual-only, 2015 to current season). They stream the ~40 MB season file to `data/raw/nflverse/` and parse from disk because the Fly VM has 512 MB.
- `backend/app/analytics/first_half.py` (pure, `before` cutoff, final games only, so a game never sees its own half) and `TeamContext.first_half` plus `data_as_of.first_half` on the context endpoint (null for the NBA and until loaded).
- Frontend: "First half" tab (NFL only) in `MatchupPage.tsx`, helpers in `lib/matchup.ts`. Also a First half section in `week_matchups_html.py`.
- `ContextRepository` now shares one game-matching helper (`game_index`) between `save_team_stats` and `save_half_stats`.
- Docs updated: `CLAUDE.md`, `docs/operations-guide.md`.

### Verification done
- Backend: ruff clean, 575 tests pass (550 before, 25 new). Frontend: tsc clean, 107 tests pass (101 before, 6 new), production build OK. Mutation checks confirmed the new frontend tests fail when the output is wrong.
- Migration: upgrade, `alembic check` (no drift), downgrade, re-upgrade on SQLite. **Not run on Postgres by me**; Fly's `release_command` ran it on Neon and reported success.
- Real data end to end (`e2e_real_data.py`): 272 games loaded, 242 half rows saved, 0 unmatched; API values for JAX@BAL matched the independent pandas numbers exactly.
- CI on the PR and on master: backend-test, frontend-build, browser-smoke, deploy all succeeded. Fly deploy log: release_command completed successfully, machine in a good state, DNS verified.

### Not verified
- Live `https://bet-watch.fly.dev` could not be reached from the sandbox (egress proxy). The Fly service is jobs-only anyway; the UI runs locally.
- That the scheduled `half_stats` job has actually run on Fly. Check `job_slots` or the Ops page.
- The jobs against Neon: no one has run `half_stats_history` yet.

## Open items
1. **Backfill (user's PC, Windows, PowerShell)**: repo is at `C:\Users\justj\codebase\repos\bet_watch`. Needs a clean Python 3.12 venv (an earlier attempt failed with `cannot import name 'ArgsKwargs'` from `pydantic_core`: `py -3.12 -m venv .venv` was run on top of an existing 3.14 venv, leaving 3.14-built packages). Fix: `deactivate`, delete `.venv`, `py -3.12 -m venv .venv`, activate, `python --version`, `python -m pip install -e ".[dev]"`. A fresh clone has no `.env` and the default `DATABASE_URL` is a local Docker Postgres at 127.0.0.1:5433, so `.env` at the repo root must hold the Neon URL (`postgresql+asyncpg://...`, with `ssl=require`, not `sslmode`). Then `python -m app.jobs half_stats_history`. Safe to re-run (upserts).
2. Pull `master` locally only after the deploy has migrated Neon (it has), then run the local UI.
3. Possible next work: with history loaded, test whether first-half stats predict halftime leads better than full-game stats; join `nfl_ats` closing spreads for first-half ATS angles once Week 9 lines post; add a "halftime lead rarely converts" angle to `week_matchups_html.py`.

## Environment and workflow notes
- Sandbox limits seen: ESPN is blocked by the egress proxy (nflverse and GitHub work); no Neon credentials; Neon MCP connector not authorized; `gh pr` fails because GraphQL is blocked, so use `gh api repos/...` (REST).
- Merging PR #25 was refused by the permission layer until the user explicitly said "merge it"; deploys are push-to-master via `.github/workflows/ci-deploy.yml` (`flyctl deploy --remote-only`).
- Do not run `run-due` on the PC as well as on Fly: the in-process lock only covers the one Fly machine.
- Never paste or commit `DATABASE_URL` or other secrets.
- This analysis branch, `chore/week9-first-half-analysis`, is NOT merged to master (so it does not deploy). Open a PR only if it should live on master.
