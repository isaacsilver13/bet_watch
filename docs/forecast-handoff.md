# NBA forecast delivery contract

The NBA model producer writes one JSON artifact per Eastern slate date. The
betting app can store it through `POST /api/v1/internal/nba-predictions`. For
the isolated local paper trial, the NBA workflow also attaches the exact
bet-watch-accepted JSON as `predictions-YYYY-MM-DD.json` to its daily release
in the **private** NBA data repository. The code repository is public, so it
does not host the artifact. The bet-watch POST remains in place.

After the next daily release includes the forecast asset, run from
`backend/` with an authenticated `gh` session:

```powershell
..\.venv\Scripts\python.exe -m app.reports.forecast_artifact --date YYYY-MM-DD
```

The importer reads the private release asset, validates its date and the
loaded pre-tip regular-season slate, and writes only to the named isolated
Neon trial branch. A duplicate import is idempotent; conflicting input fails.
No Fly endpoint, scheduler, or email sender has been activated for the trial.

## Version 1 payload

The existing `tools/predict_today.py` JSON is version 1. Its required fields are
`slate_date`, `model_version`, timezone-aware `generated_at`, `data_through`,
and 1–20 `games` with `game_date`, `home`, `away`, `pred_margin`, `p_home_win`,
and positive `sigma`. `schema_version: 1` is accepted; its absence also means
version 1 while the existing bet-watch consumer remains in place. Game dates
must equal the slate date; the data-through date must precede it. This schema
still contains one global sigma and does not claim rotation-aware variance.
The intake compares the game list with the app's loaded regular-season slate,
rejects the NBA producer's stale-data gaps, and requires receipt before the
first tip. A replay of the already stored artifact remains idempotent after
tip. New or conflicting versions are never silently substituted.

Set `NBA_PREDICTION_INGEST_SECRET` on the app and send the same value in
`X-Internal-Secret`. The route is unavailable when the secret is unset. It
requires `PERSISTENCE_ENABLED=true` and migration `0007_nba_forecasts`. A
first delivery returns HTTP 201; a byte-equivalent normalized payload retry
returns 200; a different artifact for that date returns 409. The original
payload, SHA-256 digest, generation time, and receipt time remain in the DB.
There is no overwrite endpoint. Use a new, reviewed versioned correction
contract if same-day replacement becomes necessary.

For a local smoke test with a fixture or already generated JSON file:

```powershell
$headers = @{ 'X-Internal-Secret' = $env:NBA_PREDICTION_INGEST_SECRET }
$body = Get-Content -Raw -LiteralPath '<path-to-predictions-json>'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/internal/nba-predictions' -Headers $headers -ContentType 'application/json' -Body $body
```

The `model_picks` job reads the stored artifact first. Its existing neighboring
repo file path remains a local fallback. On a scheduled NBA game day, a missing
forecast leaves the job slot pending for the next tick; a day with no regular
season NBA game is empty. A delivered forecast needs to arrive before the
10:15 Central job or before the slate's scheduled games begin. Missing
forecasts, unavailable schedules, and missing post-forecast quotes use a
`waiting` slot state without consuming the job's attempt limit; the slot
expires as missed on the next scheduler run after the Central day ends. The
job needs a synchronized two-sided moneyline or mirrored spread quote for
**every** scheduled game before it stores picks or sends a report. Quotes must
be within one hour of the decision and have matching provider update times
within each pair. A total-only or one-sided snapshot leaves the slot waiting.
Migration `0008_nba_reports` stores the exact report payload in the same
transaction as its picks. A failed AIsaac push retries that payload without
repricing, even after tip, through 06:00 Central the next morning. The
migration cannot recover the original payload of a pre-upgrade pending push;
reconcile any such slot at cutover before enabling the new scheduler.

## Activation and cutover gates

1. Decide which service owns the NBA producer handoff: existing bet-watch,
   this app, or both temporarily. Confirm bet-watch accepts an explicit
   `schema_version` before adding it to the producer artifact.
2. Configure durable app database storage and a separate intake secret; apply
   migrations 0007 and 0008 in an isolated environment first. Verify owner access and
   backups before any shared or Fly migration.
3. Add a producer delivery step with bounded retries and a recorded response.
   Do not advance its finished-day marker until required recipients acknowledge
   the same artifact. Replaying a request must return 200, not make new picks.
4. Reconcile the AIsaac report push before enabling the newsletter, so one
   report version has one sender and one idempotency key.

The NBA research repo also has an SGO/Odds API collector, while this app has
its own Odds API snapshot job. Before changing either schedule, audit which
are active, their shared API quota, and whether research snapshots contain
usable alternate lines. App quote IDs and retained raw payload policy still
need a separate design; this forecast migration does not solve odds replay.
