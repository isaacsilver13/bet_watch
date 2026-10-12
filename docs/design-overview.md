# Design overview

A local-only, multi-sport matchup hub built on top of the odds board: browse a week's slate,
open a matchup, and see schedule, reference lines and (later) context stats.

- **Ingest:** free public sources only, normalized into Postgres (`backend/app/ingest/`).
- **Jobs:** idempotent time-slot jobs run by `python -m app.jobs run-due` (`backend/app/jobs/`).
- **API/UI:** read-only `/api/v1/slate` and a React slate page.
- **Privacy:** the database and anything personal stay on the owner's machine; detailed design
  notes are kept out of this repository.

Phases: 0+1a (this slice: NFL schedule, lines, slate), then context stats, odds snapshots, other sports.
