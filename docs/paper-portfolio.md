# Paper portfolio prototype

Date: 2026-10-22. Scope: local, deterministic allocation arithmetic in
`backend/app/analytics/portfolio.py`. It does not create bets or call a provider.

The owner approved provisional defaults of 3% net EV and quarter Kelly. The
prototype computes eleven independent paper allocations: all eligible bets
and top 1 through top 10. Each variant starts at $1,000 unless a separate
bankroll is supplied. The personal bet log is separate and records actual
stakes; no stake is automatically placed.

For now, a candidate must be a targeted moneyline from a mapped book, with
nonempty forecast and quote IDs. New immutable `nba_reports` snapshots now
carry a canonical forecast hash only when the forecast is in immutable
`nba_forecasts`, plus the exact selected offer-observation ID and the unrounded
win probability. `candidates_from_report` rejects targeted
moneyline picks when those fields are missing or ambiguous. This protects
pre-upgrade `model_picks` rows, which still lack an exact source-offer ID;
they must not be silently converted into portfolio candidates. A file-only
forecast can still produce the ordinary report, but is ineligible for paper
allocation until delivered to immutable intake. The pure parser trusts a
saved report; a future ledger writer must verify referenced database rows
before persisting an allocation. Spread watchlist
rows, alternate lines, and curve gaps are ineligible until a validated
distribution and quote-provenance path exist.

The rank is net EV at a fixed $25 reference stake, breaking ties by game and
candidate ID. Every selected bet is then sized as 25% of its no-fee full
Kelly fraction, capped at 3% of opening bankroll per bet, 5% per game, and
10% per day. Stake plus fixed book fee consumes each cap. The fee and net EV
are recalculated at the actual cent-rounded stake; bets below 3% at that
stake are excluded. All of these parameters are saved in the returned policy
snapshot. A fixed fee makes the no-fee Kelly calculation an approximation;
the actual-stake net-EV gate prevents a fee-driven false positive, but can
miss a worthwhile stake near a fee-waiver threshold.

The protected `GET /api/v1/nba/reports/{date}/paper-preview` now applies this
allocator to a saved daily report and returns all eleven variants. The report
page shows candidate counts, selected bets, and the day's reserved stake plus
fees. It refuses a targeted pick with missing market identity or missing
forecast/quote provenance, and it checks the report date against its database
key before applying date-specific fees. The preview starts each variant at $1,000 every day:
it is a one-day illustration, distinct from the recorded ledger.

## Local paper ledger (2026-10-22)

The ledger stores one allocation batch per profile and slate, quote-linked
wagers, and separate settlement rows. The daily job records all eleven variants
from the saved report before the first tip, after matching the delivered
forecast and fresh paired quote observations to each candidate. A repeated job
returns the saved decision. It rejects a late first allocation and a date older
than an already recorded slate, so it cannot use later scores or quotes to
backfill a decision. Each variant starts with $1,000 and carries its own cash
forward after stakes, fees, and settled payouts. The personal bet log stays
separate. Final-score moneylines settle once; score and game-update timestamps
are frozen with each settlement. The owner-only status endpoint and report page
show cash, open stake, equity at cost, and settled net/ROI. Open bets are not
valued at live market prices.

This is local paper tracking. No historical decisions are backfilled, and no
shared database migration, scheduled deployment, or real bet is activated.
Manual voids, corrections after an official score change, and personal-account
deposits/withdrawals remain future work. Test policy changes on forward paper
results before changing thresholds or selecting a displayed top-N variant. The
ten top-N variants share games and must not be treated as independent evidence.

## Local decision audit

The read-only owner view for each recorded slate returns the frozen
allocation policy and opening cash for every profile, plus each wager's game,
selection, quoted price and observation time, model probability, stake, fee,
and settlement evidence. The saved ledger is the source; the report page labels
the one-day preview separately. An empty allocation is a recorded decision
with zero wagers. Local tests cover open, settled, and empty slates without
calling a live data provider. The audit compares the currently saved report
and delivered forecast hash with each frozen decision; the page warns when
the current report and recorded paper decision no longer describe the same
snapshot.
