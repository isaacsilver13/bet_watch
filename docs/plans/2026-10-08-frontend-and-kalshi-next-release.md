# Betting Aggregator — frontend refresh and Kalshi productionization

Status: in progress

Scope: `repos/betting_aggregator` (React/Vite frontend, FastAPI prediction-market API, and single-app Fly rollout)

Date: 2026-10-08

## Goal

Ship a clearer, faster odds-comparison experience and make the existing read-only Kalshi prototype safe to enable in production. The sportsbook board remains the primary product. Kalshi appears as a distinct prediction-market lane attached to a game only when the match is conservative and reviewable.

Success means a user can:

- scan moneyline, spread, and total prices across 3–8 sportsbooks without fighting nested horizontal scrolling;
- filter by league, date, team, and market, with useful loading, empty, stale, and error states;
- see Kalshi's native last trade, bid, ask, spread, available size, volume, close time, freshness, and settlement rules without mistaking them for sportsbook odds; and
- continue using sportsbook data when Kalshi is disabled, empty, stale, rate-limited, or unavailable.

## Current state

- The branch `feat/sportsbooks-prediction-markets` already contains a Kalshi MVP: an unauthenticated client, normalization and conservative teams-plus-date matching, a separately cached `GET /api/v1/prediction-markets` endpoint, committed NFL/NBA fixtures, and per-event prediction cards.
- Keeping prediction contracts outside `/odds` is the correct boundary. It prevents them from entering sportsbook best-price selection and lets either source fail independently.
- The current Kalshi client uses `https://api.elections.kalshi.com/trade-api/v2`; current official documentation names `https://external-api.kalshi.com/trade-api/v2` as the production Trade API. This must be corrected or made configurable and contract-tested before rollout.
- `PredictionContract.probability` currently comes from `last_price_dollars`. The frontend labels it "market probability," which overstates what it is. Last trade, midpoint, bid, and ask must have distinct names and labels.
- The frontend drops Kalshi `provider_status` and `provider_details`; a Kalshi failure is silently converted to an empty contract list. Users therefore cannot distinguish disabled, loading, no markets, stale fallback, and failed states.
- `App.tsx` owns fetching, filters, refresh, merging, tables, and page composition. `styles.css` contains the original theme plus a second override theme and an appended prediction-market block. There is no frontend test runner beyond TypeScript/build checks.
- The game view renders three independent tables side by side. This is dense on desktop and creates nested scrolling at narrower widths. Prediction contracts render as independent cards even though the two team outcomes belong together.
- The browser does not send `X-Admin-Token`, so production can answer a manual refresh or prop request with `200` plus `X-Refresh-Ignored: unauthorized|throttled`. The current client does not inspect that header and can imply a refresh succeeded when it did not. The admin secret must never be shipped to the browser.
- Kalshi matching is intentionally narrow but brittle: it depends on a rules-text regex, city-prefix aliases, a ticker-encoded date, and exactly two contracts. Unmatched and ambiguous markets are preserved, which should remain the safe default.
- Kalshi polling is bounded, but exhausting `MAX_PAGES` with a remaining cursor currently returns a partial market set. Because pairing expects exactly two contracts, truncation must become a typed failure rather than being normalized as valid unmatched data.
- The live deployment is currently a single Fly app built by `docker/Dockerfile` and configured by `fly.toml`. Repository docs that still describe two Fly apps should be repaired as part of this release.

## Constraints and non-goals

- Keep the application informational and read-only. Do not add order placement, balances, accounts, API keys, WebSockets, or automated trading.
- Keep fixture mode as the local default. No live Kalshi request, paid sportsbook request, deployment, or production flag change occurs without explicit approval.
- Do not combine Kalshi contracts with sportsbook `Offer` records, sportsbook best-price logic, or sportsbook history.
- Preserve native Kalshi decimal-dollar/cents data. Do not silently convert it to American odds or treat last trade as an executable price.
- Limit this release to pre-game, two-sided NFL and NBA game-winner contracts. Props, totals, spreads, futures, parlays/combos, live-only markets, and Polymarket remain out of scope.
- Do not add prediction-market persistence yet. Revisit it only after a concrete contract-history use case exists.
- Do not weaken the existing paid-provider quota protections, stale last-known-good behavior, admin gates, or production safety switches.

## Product and interaction design

### Information architecture

1. Keep a compact page header with overall connection state and separate sportsbook/Kalshi provider chips.
2. Make league, date, team, and market first-class filters. Store them in the URL so a board can be bookmarked and browser navigation works.
3. Use one unified odds matrix per game: books are columns; market and selection are rows. On wide screens, moneyline, spread, and total groups remain visible together. On small screens, switch between those groups with an accessible segmented control while preserving the side-by-side book comparison.
4. Keep alternate lines and player props in a secondary expandable section. Fetch props only after the user opens a prop market so the existing paid-request controls remain meaningful.
5. Place a compact "Kalshi prediction market" section below the sportsbook matrix. Group both team outcomes in one panel instead of two unrelated cards.
6. Keep unmatched and ambiguous contracts out of game cards. Put them in a collapsed diagnostics section, hidden by default, with the match reason visible.

### Kalshi presentation

For each outcome show:

- outcome/team and contract status;
- last traded price, explicitly labeled "Last trade";
- best bid and best ask with size, plus the bid–ask spread;
- volume, open interest or liquidity when the source supplies it;
- quote freshness from the local fetch/observation time, plus any upstream timestamp explicitly labeled as metadata-updated time rather than quote time;
- close/expiration time;
- a collapsed settlement-rules view and a deep link to the specific contract or event; and
- a disclosure that prediction contracts have different fees, depth, and settlement rules from sportsbook wagers.

Do not show a synthesized "edge" in this release. A future comparison could use a named methodology such as de-vigged sportsbook consensus versus Kalshi midpoint, but only after fees, quote side, liquidity, and timestamps are modeled and tested.

### Visual and accessibility standards

- Consolidate color, spacing, typography, elevation, and status colors into one token layer; remove the duplicate theme cascade.
- Use skeletons for initial loads and reserved space for provider notices to reduce layout shift.
- Use color plus text/icon for best price, stale, suspended, error, and matched states.
- Provide visible focus styles, keyboard-operable controls and disclosures, semantic table headers, polite live regions, and no hover-only information.
- Verify at 360 px, 768 px, 1280 px, and a large desktop viewport. At 360 px, the table may scroll horizontally but page content must not overflow or overlap.

## Delivery plan

### Slice 0 — reconcile and lock the baseline

1. Decide whether the existing feature branch will be the implementation base or whether its commits should be rebased/cherry-picked onto the current default branch. Do not reimplement the same Kalshi prototype in parallel.
2. Update the stale deployment description in `CLAUDE.md`, `README.md`, and the older 2026-10-07 plan to reflect the single Fly app and this plan's superseding scope.
3. Record a fixture-only baseline: backend suite, frontend type check/build, API snapshots for `/odds` and `/prediction-markets`, and desktop/mobile screenshots.
4. Fix the local Python interpreter/test-runner issue and the sandbox-only Vite `realpath` issue before using local results as a release gate. CI remains the authoritative clean-environment check until then.

Exit gate: the implementation base is explicit, CI is green, and the current fixture behavior is captured for regression comparison.

### Slice 1 — stabilize the Kalshi data contract

1. Move the base URL into validated configuration with the documented `external-api.kalshi.com/trade-api/v2` default. Reject insecure or malformed overrides in production.
2. Keep bounded cursor pagination and `status=open`. If the page budget is exhausted while a cursor remains, raise a typed incomplete-response error and use stale last-known-good data; never normalize a partial set. Add tests for multiple pages, a repeated cursor, page-budget exhaustion, non-JSON bodies, 429s, timeouts, 4xx/5xx responses, and an empty successful response.
3. Rename or extend the API model so `last_price`, `best_bid`, `best_ask`, and optional `midpoint` are unambiguous. If compatibility requires retaining `probability`, deprecate it and document that it is the last trade.
4. Carry local `observed_at`, contract/event URL, primary and secondary rules, source event ID, price precision, and available liquidity fields without inventing missing values. If Kalshi `updated_time` is carried, expose it as `metadata_updated_at`; do not use it as quote freshness because it can reflect non-trading metadata changes.
5. Make cache metadata per sport and expose cache age, stale/error state, last successful refresh, and contract counts. Preserve stale last-known-good data and retry backoff.
6. Keep matching deterministic: normalized league/team identities, event date, exactly two opposing outcomes, supported market type, and settlement-language classification must all agree. Add an audited alias/override table for provider-specific team labels; never promote fuzzy matches automatically.
7. Return matched, unmatched, ambiguous, and rejected/malformed counts so data-quality drift is observable.
8. Extend the existing admin-gated `/api/v1/health/metrics` payload and structured fetch log with per-sport Kalshi request count, success/error count, duration, cache age, contract count, and match-state counts. Add tests for the metric increments and redaction boundary.

Exit gate: fixture and API-contract tests pass, a payload/schema change fails loudly, and one venue failure cannot change `/odds` output or status.

### Slice 2 — refactor the frontend foundation

1. Split `App.tsx` into page composition, URL-backed filters, sportsbook query state, Kalshi query state, event cards, odds matrix, provider status, and notices. Keep the data-fetch layer dependency-light unless a cache library solves a demonstrated problem.
2. Replace the duplicated CSS cascade with one tokenized responsive system and reusable primitives for buttons, pills, notices, cards, tables, skeletons, and empty states.
3. Build the unified game matrix and responsive small-screen market switcher. Preserve best-price highlighting by exact selection/line and ensure missing books remain visible.
4. Add date/team search, useful sort order (start time by default), clear-filter behavior, and URL persistence.
5. Add explicit initial loading, background refresh, stale saved data, disabled provider, empty schedule, no filter results, and request-failure states.
6. Make refresh capability honest. Parse `X-Refresh-Ignored`; surface unauthorized and throttled outcomes; and hide or disable public controls that cannot work. Never embed `ADMIN_REFRESH_SECRET` in frontend code. Define a server-side scheduled/admin refresh path before treating player props as reliably populated in production.
7. Key request state by sport and cancel or ignore superseded requests so rapid NFL↔NBA switching cannot display or commit results from the prior sport.
8. Add a frontend test stack (Vitest + React Testing Library is the smallest fit) and browser-level smoke coverage for the core board. Keep `tsc -b` and the production Vite build as required checks.

Exit gate: the sportsbook experience meets the original side-by-side comparison requirements on desktop and mobile with fixture data, and existing refresh/quota behavior is unchanged.

### Slice 3 — integrate Kalshi into the redesigned game view

1. Fetch sportsbook and Kalshi data independently and retain each source's loading/error/freshness metadata. A failed Kalshi request must render a local notice, not clear a previously good sportsbook board.
2. Group matched contracts by canonical event and source event ID, then render paired outcomes in one panel with the native fields defined above.
3. Add clear badges for live/fresh, stale, suspended/closed, ambiguous, and unavailable states. Never use the sportsbook best-price treatment for Kalshi.
4. Add the collapsed unmatched/ambiguous diagnostics section with match reasons and counts. It remains hidden by default.
5. Add component tests proving last trade is not mislabeled as bid/ask or sportsbook odds, stale data stays visible with a warning, empty is distinct from error, and Kalshi contracts cannot affect sportsbook best-price selection.
6. Add request-race tests proving a rapid league switch cannot flash or retain contracts from the previous sport and a failed replacement request does not erase last-known-good Kalshi data for its own sport.

Exit gate: fixture-mode end-to-end tests cover successful, empty, stale, error, unmatched, ambiguous, suspended, and mobile states.

### Slice 4 — controlled live validation and rollout

1. With explicit approval, run a read-only live discovery check against the documented public endpoint. Capture only sanitized fixtures and aggregate diagnostics; do not commit raw user/account data or credentials.
2. Manually review available representative NFL and NBA markets against sportsbook events. Verify teams, local/UTC date handling, settlement/tie language, status, prices, sizes, and deep links. If one league has no active markets, record that limitation instead of weakening matching.
3. Deploy with `KALSHI_ENABLED=false`, confirm the sportsbook regression smoke test, then enable Kalshi in staging or a reviewed production window only after the live sample passes.
4. Monitor the new health metrics and structured logs. Alert when an enabled sport has no cache after two consecutive failures, cache age exceeds twice `KALSHI_CACHE_TTL_SECONDS`, pagination/schema rejection recurs twice, or two consecutive refreshes have sportsbook events plus unmatched Kalshi contracts but zero matches. Define a one-step rollback: set `KALSHI_ENABLED=false`; no database rollback is required.
5. Update operator docs with the current endpoint, TTL, feature flag, expected statuses, manual smoke commands, and rollback procedure.

Exit gate: production shows source-specific status and fresh or explicitly stale Kalshi data while sportsbook availability, latency, and paid-provider quota use remain within their prior bounds.

## Verification matrix

### Backend automated checks

- Unit tests for price precision, timestamp semantics, complete pagination, status handling, rule capture, alias normalization, date boundaries, all match states, cache isolation, retry/backoff, and stale fallback.
- API tests for disabled, configured, empty, stale, and error responses; response-model compatibility; count/freshness metadata; and `/odds` isolation.
- A schema fixture based on the current official response fields so upstream drift is detected intentionally.
- Full `pytest` and `ruff check .` before merge.

### Frontend automated checks

- Unit/component tests for filters, URL state, exact-line best-price selection, loading/error/empty/stale states, ignored/throttled refreshes, cross-sport request races, grouped Kalshi outcomes, rules disclosure, and safe external links.
- Accessibility assertions for names, roles, table headers, focus order, live regions, and non-color status cues.
- Browser smoke tests at desktop and mobile sizes for NFL, NBA, props expansion, event refresh, Kalshi success, and Kalshi failure.
- `npm run lint` and `npm run build` before merge.

### Manual checks

- Verify one dense game with 8 books, missing offers, tied best prices, long team/book names, and a stale snapshot.
- Verify one matched, one unmatched, one ambiguous, one suspended, and one no-quote Kalshi contract set.
- Confirm all external links open the intended specific source page with `rel="noreferrer"`.
- Confirm fixture mode produces zero external provider traffic.
- Confirm disabling Kalshi removes only prediction-market UI and leaves the sportsbook board unchanged.

## Acceptance criteria

- Moneyline, spread, and total comparisons remain available together per game across the configured sportsbooks; the layout is usable at 360 px and desktop widths.
- League, date, team, and market filters are bookmarkable and have distinct loading, empty, stale, and error behavior.
- Kalshi last trade, bid, ask, size, spread, volume/liquidity, locally observed freshness, close time, status, rules, and source link are labeled accurately when supplied; metadata timestamps are not presented as quote times.
- Only conservatively matched two-sided game-winner contracts appear inside a sportsbook game. Unmatched and ambiguous contracts never leak into a game card or best-price calculation.
- Kalshi disabled/error/stale states are visible and source-specific; they never make `/odds` fail or erase good sportsbook data.
- Fixture mode remains the default, no credentials are added, and production enablement is controlled by `KALSHI_ENABLED` with a documented rollback.
- Backend tests, frontend tests, type checking, production build, accessibility smoke tests, and fixture-mode browser smoke tests pass in CI.
- Public refresh/prop controls cannot falsely report success when the backend ignores or throttles a request, and no admin credential is present in the frontend bundle.

## Risks and decisions needed

1. **Display basis:** recommended default is last trade as the headline value, with executable bid/ask directly beside it. Do not headline a derived midpoint without labeling it.
2. **Responsive matrix:** recommended design is one unified matrix on desktop and a market-group switcher on small screens; validate this against the requirement that main lines remain easy to compare together.
3. **Unmatched visibility:** recommended default is hidden behind diagnostics, with a count near the Kalshi provider status.
4. **History:** recommended deferral. In-memory TTL plus stale fallback is sufficient for the first read-only release; persistence deserves its own schema/retention plan.
5. **Rollout:** recommended order is disabled deploy, live read-only validation, then flag enablement. Enabling or deploying requires a separate explicit approval.

## Implementation record — 2026-10-08

Slices 0–3 are implemented on `feat/sportsbooks-prediction-markets` and validated with fixture data. The delivered scope includes the corrected and configurable Kalshi public endpoint, bounded complete pagination, per-sport cache and metrics, explicit native contract price fields, a responsive sportsbook matrix, URL-backed filters, honest refresh capability handling, source-isolated loading/error state, grouped matched Kalshi panels, and frontend regression coverage.

Verification completed:

- backend: `ruff check backend` and 94 passing pytest tests;
- frontend: ESLint, 6 Vitest/React Testing Library tests, 2 Playwright desktop/mobile smoke tests,
  and the production Vite build;
- dependency audit: zero reported npm vulnerabilities after the Vitest upgrade; and
- fixture-mode browser smoke checks at 1440 px and 500 px viewports, covering the sportsbook board and a matched two-sided Kalshi panel.

Slice 4 remains intentionally pending. No live Kalshi request, Fly deployment, feature-flag enablement, external write, or production change was performed. Those steps retain the explicit-approval gate described above.

## Source notes

- Kalshi's official market-data quick start, checked 2026-10-08, documents unauthenticated requests to `https://external-api.kalshi.com/trade-api/v2`, the markets endpoint, cursor pagination, and public order-book access: <https://docs.kalshi.com/getting_started/quick_start_market_data>.
- Kalshi's current Get Markets reference, checked 2026-10-08, documents open-market filtering and native dollar-price, fixed-point size, volume, open-interest, rules, status, and timestamp fields: <https://docs.kalshi.com/api-reference/market/get-markets>.
- The approved Second Brain note `wikis/sports-modelling/odds-provider-quota-protection.md` records the fixture-default, single-flight, stale-fallback, and daily paid-refresh protections that this plan must preserve.
- The earlier repository plan `docs/plans/2026-10-07-sportsbooks-and-prediction-markets.md` records the implemented prototype and is historical context; this document governs the next release.
