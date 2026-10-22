# Design overview

A multi-sport matchup hub (local UI; jobs on a jobs-only Fly app) built on top of the odds board: browse a week's slate,
open a matchup, and see schedule, reference lines and (later) context stats.

- **Ingest:** free public sources only, normalized into Postgres (`backend/app/ingest/`).
- **Jobs:** idempotent time-slot jobs run by `python -m app.jobs run-due` (`backend/app/jobs/`).
  They are the only scheduled spenders of Odds API credits; pages read stored data, and a paid
  refresh happens only from a Refresh button on the owner's machine.
- **API/UI:** read-only `/api/v1/slate` and a React slate page.
- **Privacy:** the database and anything personal stay on the owner's machine; detailed design
  notes are kept out of this repository.

Phases: 0+1a (this slice: NFL schedule, lines, slate), then context stats, odds snapshots, other sports.
