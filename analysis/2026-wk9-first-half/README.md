# 2026 Week 9 first-half analysis

One-off analysis scripts and outputs from the session that produced the First half tab. The
production implementation lives in `backend/app` (see `SESSION_SUMMARY.md`); these are kept for
reproducibility and are not imported by the app.

| File | What |
|---|---|
| `SESSION_SUMMARY.md` | What was done, how it was verified, open items. Written to be pasted into a new session. |
| `build_week9.py` | pandas: team-game and season tables plus the week's matchups from nflverse files |
| `render_week9.py` | Renders `week9_matchups.md` from the tables |
| `e2e_real_data.py` | Loads real nflverse files through the real parser, repository and `/context` API into a scratch SQLite DB |
| `week9_matchups.md/.csv`, `team_summary_*.csv`, `team_games_*.csv` | Outputs through week 8 (as of 2026-11-04) |

## Reproduce
Download `games.csv` (nflverse `schedules` release) and `play_by_play_2026.csv` (nflverse `pbp`
release, saved as `pbp.csv`) into a folder `raw/`, then:

```bash
python build_week9.py raw out 9            # needs pandas
python render_week9.py out 9
# from the repo root, with the backend installed (pip install -e ".[dev]"):
PYTHONPATH=backend python analysis/2026-wk9-first-half/e2e_real_data.py raw scratch
```

Data: nflverse, CC-BY-4.0. Historical analysis, not betting advice.
