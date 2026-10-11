# Sportsbook and prediction-market expansion plan

**Repository:** `repos/betting_aggregator`

**Date:** 2026-10-07
**Status:** Slices A, C and D implemented 2026-10-07 on
`feat/sportsbooks-prediction-markets`; Slice E (Polymarket) dropped from this plan.

**Implementation rulings (deviations from the design below):**
- Superseded for the next release by
  `docs/plans/2026-10-08-frontend-and-kalshi-next-release.md`; keep this file as historical design
  context for the implemented prototype.
- Prediction contracts live behind a separate `/prediction-markets` endpoint and
  `PredictionContract` model, not `venue_kind` on `Offer`. This makes "never affects sportsbook
  best price" true by construction and avoids rewiring `/odds` around `ProviderRegistry`.
  Slice B (registry as top-level provider, migration) was therefore not built.
- No persistence or migration for contracts in v1: raw markets are TTL-cached in memory with
  stale fallback. Add storage when contract price history is actually wanted.
- No order-book call: Kalshi's market list already carries yes bid/ask and sizes.
- Kalshi gives only the local game date, so matching is teams + date (0-1 days vs UTC start).
- Real payload check: NFL ties resolve to $0.50 per team; rules text is carried on every contract.

## Outcome

Expand the NFL/NBA comparison board in two clearly distinguished lanes:

1. More conventional sportsbooks from the existing The Odds API integration,
   selected by an explicit, reviewable allowlist rather than a hard-coded `us`
   region.
2. Read-only prediction-market data from Kalshi and Polymarket, displayed as
   contracts and probabilities with their own liquidity, spread, rules, and
   freshness information.

The product remains informational and read-only. It must never place orders,
manage an exchange account, or imply that a prediction-market contract is the
same product as a sportsbook wager.

## Current-state findings

- The Odds API is the sole active live provider. Its client hard-codes
  `regions=us` in `backend/app/providers/the_odds_api/client.py`; it already
  returns every book available to that request, but cannot target a curated
  broader selection.
- The domain already preserves `provider`, `bookmaker`, raw offer IDs, deep
  links, timestamps, historical observations, and canonical event IDs. This is
  a useful base for additional sources.
- `ProviderRegistry` can merge and canonicalize multiple providers, but the
  request route currently constructs one `CachedOddsProvider`. Multi-provider
  operation needs to become the real composition path before adding adapters.
- The current comparison table assumes every offer is a sportsbook price and
  identifies the “best” price across all offers. Prediction contracts require
  separate presentation and must not participate in that calculation.
- The data model has no representation for a prediction contract’s rules,
  outcome price in cents, bid/ask, liquidity, volume, or match confidence.

## Recommended provider scope

### Sportsbooks — first increment

Keep **The Odds API** as the sportsbook source. Add configurable
`ODDS_REGIONS` and (preferred) `ODDS_BOOKMAKERS` settings, with a committed
allowlist of the books the product wants to show. Pass the `bookmakers`
parameter when it is set; otherwise pass `regions`. The provider supports both
patterns, considers every ten selected books a region for quota purposes, and
can provide deep links. Start with an approved US list such as DraftKings,
FanDuel, BetMGM, Caesars, BetRivers, Fanatics, bet365, and Pinnacle *only where
each is available from the provider and permitted for the intended audience*.

Do not add one scraper or direct integration per book. That duplicates event
matching, credentials, rate limits, terms review, and failure handling without
adding coverage that the current upstream can already provide.

### Prediction markets — staged adapters

1. **Kalshi first.** Its documented public REST market-data endpoints can be
   read without an API key. Implement only sports-game winner contracts for the
   initial release, using public event/market discovery and order-book/quote
   data. Persist the exact contract title, rules URL/text reference, ticker,
   status, and expiry.
2. **Polymarket second, behind `POLYMARKET_ENABLED=false` by default.** Use
   Gamma for discovery/metadata and the CLOB public price/order-book endpoint
   for executable bid/ask information. Perform a terms, geographic-availability,
   display/redistribution, and legal review immediately before enabling it.
3. Treat both feeds as independently optional. A provider failure must leave
   sportsbook data and the other prediction venue usable.

The upstream docs must be rechecked at implementation time: The Odds API
charges by region/bookmaker group and market; Kalshi public-data endpoints and
Polymarket read endpoints may change; neither venue’s contract semantics should
be inferred from a title alone.

## Data and API design

### 1. Make provider composition real

- Replace the single-provider `build_provider` path with a `build_providers`
  factory in `api/routes.py` and put each live source behind its own
  `CachedOddsProvider`.
- Activate `ProviderRegistry` as the top-level provider. Preserve per-source
  statuses, timestamps, errors, cache hits, and quota where applicable in
  `ComparisonResponse` rather than collapsing them into one status.
- Make cached/saved snapshot lookup provider-aware per source. A Kalshi cache
  hit must never suppress a fresh sportsbook fetch or vice versa.
- Add explicit per-provider TTL, timeout, retry/backoff, and request-budget
  settings. Public endpoints still get bounded polling; only paid sources use
  quota controls.

### 2. Model contract data explicitly

Add a `venue_kind` field (`sportsbook` or `prediction_market`) to offers and a
separate `PredictionContract` payload attached to an offer or event comparison.
It should include:

- venue, contract/event ID, title, URL, rules URL or immutable rules text/hash;
- outcome label (`Yes`, `No`, or named team), probability/last-trade price,
  best bid, best ask, bid-ask spread, available depth, volume, liquidity, and
  source update time;
- contract status and close/settlement time;
- raw source event ID plus `match_status` (`matched`, `unmatched`,
  `ambiguous`) and a numeric/enum match-confidence rationale.

Represent a probability as a decimal probability, not an invented American
odds conversion. The UI may show an optional, clearly labeled implied-odds
conversion for comparison, but stored source values remain native and precise.

Add an Alembic migration for the new fields/tables and indexes on venue,
contract ID, event ID, observed time, and contract status. Preserve the
existing immutable `offer_observations`; do not overwrite rules or quotes.

### 3. Match only like-for-like markets

- Start with pre-game, two-sided game-winner contracts. Match only after sport,
  normalized teams, scheduled start, and settlement semantics agree.
- Require an adapter-declared `includes_overtime`/rules assessment before
  mapping a contract to `MarketType.MONEYLINE`. If it cannot be proved,
  store it as an unmatched prediction contract rather than comparing it.
- Do not auto-map spreads, totals, player props, futures, parlays, live-only
  contracts, or multi-outcome markets in v1. Do not fabricate a `No` price
  from `1 - Yes` where both sides have independent executable quotes.
- Surface unmatched/ambiguous contracts in an “Other prediction markets” list
  with their full title and rules link, never in the sportsbook grid.
- Maintain reviewable provider-specific aliases and a small manual override
  mapping with an audit field; no fuzzy match may silently become canonical.

### 4. Build adapters and fixtures

- Add `providers/kalshi/client.py` and `adapter.py`, plus static fixtures for
  matched, unmatched, suspended, stale, and malformed payloads.
- Add equivalent Polymarket modules only after the pre-enable review passes.
  Keep any token/order-book details inside the adapter, not the domain layer.
- Normalize source response failures to existing typed provider errors. Record
  a provider refresh failure without treating an empty source response as a
  successful board.
- Add a fixture mode for each prediction source so local development and tests
  never contact a live venue.

### 5. Product presentation

- Keep the existing “Sportsbooks” price grid and best-price highlighting
  strictly sportsbook-only.
- Add a per-event “Prediction markets” panel with one card per contract:
  venue badge, contract question, outcome/price, bid–ask, liquidity/volume,
  freshness, close time, settlement-rules link, and source link.
- Label prices as “market probability” or “contract price,” not odds; show a
  concise disclosure that it is not a sportsbook line and can have limited
  depth, a wider spread, or different settlement rules.
- Add filters for venue and “show unmatched”; use stale/error badges per
  provider, not one page-level freshness indicator.
- Preserve accessible table/card labels and ensure external links use
  `target="_blank"` and `rel="noreferrer"` as the existing app does.

## Delivery slices

### Slice A — sportsbook coverage configuration

1. Confirm the intended jurisdictions and named book allowlist.
2. Add validated region/bookmaker configuration and pass it to The Odds API.
3. Expose selected coverage in `/status` without exposing credentials.
4. Add fixtures/tests proving allowlist precedence, invalid configuration
   rejection, quota calculations/headers, and unchanged default fixture mode.
5. Manually verify one fixture board and one authorized, bounded live request
   only after a key and quota budget are approved.

### Slice B — multi-provider foundation and schema

1. Turn `ProviderRegistry` into the real top-level provider with isolated
   caches and provider details.
2. Add domain/API/schema support for venue kind, contracts, native quote
   metrics, rules, and match state.
3. Add migration upgrade/downgrade tests and ensure existing sportsbook history
   and API response compatibility continue to work.

### Slice C — Kalshi read-only MVP

1. Run a no-write discovery spike against documented public data, capture
   sanitized fixtures, and prove five representative NFL/NBA contracts can be
   classified correctly.
2. Implement discovery, quote/order-book retrieval, normalization, conservative
   game-winner matching, caching, and persistence.
3. Release behind `KALSHI_ENABLED=false`; enable only after a staging smoke test
   demonstrates correct event matching, stale fallback, and no unintended live
   polling.

### Slice D — prediction-market UI

1. Render venue-separated prediction cards and unmatched contracts.
2. Add provider-specific freshness/error states, rules/source links, and the
   semantics disclosure.
3. Test that prediction contracts never affect sportsbook “best price” badges.

### Slice E — Polymarket pilot and rollout

1. Reconfirm API, data-display/redistribution terms, geographic requirements,
   and current product/legal constraints; record the result with date and
   source links before enabling the adapter.
2. Implement the adapter and fixtures using the same contract/matching model.
3. Enable in staging first, then production only with an agreed refresh budget,
   alerting, and provider-specific kill switch.

## Verification and acceptance criteria

- Unit tests cover each adapter’s normalization, timestamp handling, quote
  precision, contract rule capture, and all match states.
- Registry tests prove one source timing out does not remove data from another
  and that status/freshness remains source-specific.
- API contract tests prove existing sportsbook clients remain compatible and
  prediction data carries `venue_kind` plus the required contract metadata.
- Persistence tests cover migration upgrade/downgrade, immutable observations,
  history filtering by venue, and stale last-known-good recovery per provider.
- Frontend tests/build confirm sportsbook best-price comparison excludes every
  prediction-market offer and that unmatched contracts cannot appear in that
  grid.
- A staging smoke check validates a manually reviewed NFL and NBA matchup
  against each enabled venue, including overtime/settlement semantics.
- Monitoring records request counts, provider latency/errors, cache age, match
  counts by state, and source freshness. Alerts are operational only and never
  trigger trading actions.

## Decisions required before implementation

1. Which jurisdictions and sportsbooks should the first allowlist cover?
2. Is Kalshi-only acceptable for the first production prediction-market release,
   with Polymarket held behind the later compliance/terms gate?
3. Should unmatched prediction contracts be visible by default or behind a
   filter?
4. What maximum refresh interval/request budget is acceptable for each source?

## Source notes (checked 2026-10-07)

- [The Odds API v4 documentation](https://the-odds-api.com/liveapi/guides/v4/index.html)
  documents region/bookmaker selection, per-market/region quota behavior, deep
  links, and the endpoint shape used by the existing adapter.
- [The Odds API bookmaker directory](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)
  is the implementation-time source of truth for available books and regions.
- [Kalshi’s market-data quick start](https://docs.kalshi.com/getting_started/quick_start_market_data)
  documents unauthenticated public market-data access; implementation must
  verify the currently required endpoints and applicable data terms.
- [Kalshi’s API overview](https://help.kalshi.com/en/articles/13823854-kalshi-api)
  confirms market and order-book data availability through its API.
- Polymarket API access and display terms need an official-source recheck at
  the Slice E gate; this plan intentionally does not rely on a third-party
  wrapper as the production source of truth.
