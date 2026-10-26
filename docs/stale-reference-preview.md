# NBA stale-reference preview

The paper trial may inspect saved ESPN `current` odds when The Odds API quota is
exhausted. This is a **research-only comparison**, not a set of available bets.
ESPN's stored `captured_at` is when this app fetched the scoreboard; the source
quote update time is unavailable. The app does not verify which sportsbook
offered the reference price. It cannot show a five-book best price, FanDuel
comparison, alternate-spread curve, or a credible net-EV threshold from it.

After importing a same-day forecast from the private release, run from
`backend/` before the first tip:

```powershell
..\.venv\Scripts\python.exe -m app.reports.stale_reference --date YYYY-MM-DD
```

The JSON lists each game's forecast and the saved reference price's capture
age. Moneyline and spread `gross_ev_at_reference` are hypothetical returns at
that recorded price, before Illinois fees; whole-number spreads include push
probability. Missing, future-captured, or post-tip prices produce no quotes.
Every row is marked `eligible_for_paper: false`, and the report is marked
`prices_are_actionable: false`. The command only reads the isolated trial
branch. It never creates paper wagers or changes the eleven portfolio balances.
