import { useEffect, useState } from "react";

import { PredictionPanel } from "./components/PredictionMarkets";
import { fetchOdds, fetchPredictionMarkets } from "./lib/api";
import { GlassCard } from "./components/GlassCard";
import {
  isPlayerPropMarket,
  type EventComparison,
  type MarketType,
  type Offer,
  type PredictionContract,
  type ProviderDetails,
  type Sport,
} from "./types";

const REFRESH_ALL_KEY = "__all__";

// Odds older than this get a warning even if the backend didn't flag them.
const VERY_OLD_AFTER_MS = 36 * 60 * 60 * 1000;

function describeFreshness(
  details: ProviderDetails | undefined,
  hasEvents: boolean,
  now: number,
): { updated: string | null; warning: string | null } {
  if (!details) {
    return { updated: null, warning: null };
  }
  const refreshedAt = details.last_refreshed_at ? new Date(details.last_refreshed_at) : null;
  const updated = refreshedAt ? refreshedAt.toLocaleString() : null;
  const reason = details.error_type ? details.error_type.replaceAll("_", " ") : "unknown error";
  if (details.status === "error" && !hasEvents) {
    return {
      updated,
      warning: `Odds are unavailable right now (${reason}). No saved odds to show yet.`,
    };
  }
  if (details.stale) {
    return {
      updated,
      warning: `Showing saved odds${updated ? ` from ${updated}` : ""}. The latest refresh failed (${reason}), so lines may have moved.`,
    };
  }
  if (refreshedAt && now - refreshedAt.getTime() > VERY_OLD_AFTER_MS) {
    return { updated, warning: `These odds are over 36 hours old (last updated ${updated}).` };
  }
  return { updated, warning: null };
}

const SPORTS: Array<{ id: Sport; label: string }> = [
  { id: "nfl", label: "NFL" },
  { id: "nba", label: "NBA" },
];

const TOP_LINE_MARKETS: Array<{ id: MarketType; label: string }> = [
  { id: "moneyline", label: "Moneyline" },
  { id: "spread", label: "Spread" },
  { id: "total", label: "Totals" },
];

const SECONDARY_MARKETS_BY_SPORT: Record<Sport, Array<{ id: MarketType; label: string }>> = {
  nfl: [
    { id: "alternate_spread", label: "Alternate spread" },
    { id: "player_passing_yards", label: "Passing yards" },
    { id: "player_rushing_yards", label: "Rushing yards" },
    { id: "player_passing_rushing_yards", label: "Pass + rush yards" },
    { id: "player_receiving_yards", label: "Receiving yards" },
    { id: "player_anytime_touchdown", label: "Anytime TD" },
  ],
  nba: [
    { id: "alternate_spread", label: "Alternate spread" },
    { id: "player_points", label: "Player points" },
    { id: "player_rebounds", label: "Rebounds" },
    { id: "player_assists", label: "Assists" },
    { id: "player_threes", label: "Threes" },
    { id: "player_points_rebounds_assists", label: "Pts + reb + ast" },
  ],
};

function formatStartTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatOffer(offer: Offer): string {
  if (offer.status !== "available") {
    return offer.status;
  }

  if (offer.price_american === null) {
    return "Unavailable";
  }

  return `${offer.price_american > 0 ? "+" : ""}${offer.price_american}`;
}

function formatImpliedProbability(offer: Offer): string | null {
  if (offer.price_decimal === null) {
    return null;
  }

  return `${(100 / offer.price_decimal).toFixed(1)}% implied`;
}

function offerKey(offer: Offer): string {
  return [offer.selection, offer.line ?? "", offer.player_name ?? ""].join("|");
}

function ComparisonTable({ comparison, market }: { comparison: EventComparison; market: MarketType }) {
  const offers = comparison.offers.filter((offer) => offer.market_type === market);
  const bookmakers = Array.from(new Set(offers.map((offer) => offer.bookmaker)));
  const rows = Array.from(new Map(offers.map((offer) => [offerKey(offer), offer])).values());

  if (rows.length === 0) {
    return <div className="empty-grid">No {market.replaceAll("_", " ")} lines are available for this event.</div>;
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Selection</th>
            {bookmakers.map((bookmaker) => (
              <th scope="col" key={bookmaker}>
                {bookmaker}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={offerKey(row)}>
              <th scope="row">
                <span>{row.player_name ?? row.selection}</span>
                {row.line !== null && <small>{row.line > 0 ? `+${row.line}` : row.line}</small>}
              </th>
              {bookmakers.map((bookmaker) => {
                const offer = offers.find(
                  (candidate) => candidate.bookmaker === bookmaker && offerKey(candidate) === offerKey(row),
                );

                return (
                  <td key={bookmaker}>
                    {offer?.deep_link ? (
                      <a
                        className={isBestOffer(offers, offer) ? "best-price" : undefined}
                        href={offer.deep_link}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {formatOffer(offer)}
                        {formatImpliedProbability(offer) && (
                          <small>{formatImpliedProbability(offer)}</small>
                        )}
                      </a>
                    ) : (
                      <span
                        className={
                          offer
                            ? `${`offer-${offer.status}`} ${isBestOffer(offers, offer) ? "best-price" : ""}`
                            : "offer-missing"
                        }
                      >
                        {offer ? formatOffer(offer) : "Missing"}
                        {offer && formatImpliedProbability(offer) && (
                          <small>{formatImpliedProbability(offer)}</small>
                        )}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MarketPanel({
  comparison,
  market,
  label,
}: {
  comparison: EventComparison;
  market: MarketType;
  label: string;
}) {
  return (
    <section className="market-panel">
      <div className="market-panel-heading">
        <h4>{label}</h4>
        <span>Top line</span>
      </div>
      <ComparisonTable comparison={comparison} market={market} />
    </section>
  );
}

function isBestOffer(offers: Offer[], offer: Offer): boolean {
  if (offer.status !== "available" || offer.price_decimal === null) {
    return false;
  }

  const matchingOffers = offers.filter(
    (candidate) =>
      candidate.status === "available" &&
      candidate.price_decimal !== null &&
      offerKey(candidate) === offerKey(offer),
  );
  return matchingOffers.length > 1 && matchingOffers.every(
    (candidate) => candidate.price_decimal! <= offer.price_decimal!,
  );
}

function mergeScopedSnapshot(
  current: Awaited<ReturnType<typeof fetchOdds>>,
  refreshed: Awaited<ReturnType<typeof fetchOdds>>,
  eventId: string,
): Awaited<ReturnType<typeof fetchOdds>> {
  const hasProviderError = Object.values(refreshed.provider_status).some((status) => status === "error");
  if (hasProviderError) {
    return {
      ...current,
      generated_at: refreshed.generated_at,
      provider_status: refreshed.provider_status,
      provider_details: refreshed.provider_details,
    };
  }

  const refreshedEvent = refreshed.events.find((event) => event.event.id === eventId);
  const events = current.events.some((event) => event.event.id === eventId)
    ? current.events.map((event) =>
        event.event.id === eventId
          ? {
              ...event,
              offers: refreshedEvent?.offers ?? event.offers,
            }
          : event,
      )
    : refreshedEvent
      ? [...current.events, refreshedEvent]
      : current.events;

  return {
    ...current,
    generated_at: refreshed.generated_at,
    provider_status: refreshed.provider_status,
    provider_details: refreshed.provider_details,
    events,
  };
}

function App() {
  const [sport, setSport] = useState<Sport>("nfl");
  const [secondaryMarket, setSecondaryMarket] = useState<MarketType | null>(null);
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchOdds>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contracts, setContracts] = useState<PredictionContract[]>([]);
  const [showUnmatched, setShowUnmatched] = useState(false);
  const [refreshingKey, setRefreshingKey] = useState<string | null>(null);
  const needsPlayerProps = secondaryMarket !== null && isPlayerPropMarket(secondaryMarket);

  useEffect(() => {
    let cancelled = false;

    async function loadOdds() {
      setError(null);
      try {
        const nextData = await fetchOdds(sport, { includeProps: needsPlayerProps });
        if (!cancelled) {
          setData(nextData);
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : "Unable to load odds");
        }
      }
    }

    void loadOdds();
    return () => {
      cancelled = true;
    };
  }, [sport, needsPlayerProps]);

  // Prediction markets load independently: a failure here never affects the sportsbook board.
  // They wait for odds so the backend has sportsbook events cached to match against.
  useEffect(() => {
    let cancelled = false;
    if (data) {
      fetchPredictionMarkets(sport)
        .then((next) => !cancelled && setContracts(next.contracts))
        .catch(() => !cancelled && setContracts([]));
    }
    return () => {
      cancelled = true;
    };
  }, [sport, data?.generated_at]);

  const providerStatus = Object.entries(data?.provider_status ?? {});
  const freshness = describeFreshness(
    Object.values(data?.provider_details ?? {})[0],
    (data?.events.length ?? 0) > 0,
    Date.now(),
  );
  const secondaryMarkets = SECONDARY_MARKETS_BY_SPORT[sport];

  async function refreshVisibleLines(eventId: string) {
    const refreshKey = eventId;
    setRefreshingKey(refreshKey);
    setError(null);
    try {
      const refreshed = await fetchOdds(sport, {
        eventId,
        forceRefresh: true,
        includeProps: needsPlayerProps,
      });
      setData((current) =>
        current ? mergeScopedSnapshot(current, refreshed, eventId) : refreshed,
      );
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to refresh lines");
    } finally {
      setRefreshingKey(null);
    }
  }

  // Odds refresh automatically at most once a day; this is the manual override.
  // It spends provider quota (about 3 credits), so it is a deliberate click.
  async function refreshAllOdds() {
    setRefreshingKey(REFRESH_ALL_KEY);
    setError(null);
    try {
      setData(await fetchOdds(sport, { forceRefresh: true, includeProps: needsPlayerProps }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to refresh odds");
    } finally {
      setRefreshingKey(null);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Market desk / live comparison</p>
          <h1>Lineup</h1>
        </div>
        <div className="connection-state">
          <span className="status-dot" />
          <span>{error ? "API unavailable" : "API connected"}</span>
        </div>
      </header>

      <section className="control-band" aria-label="Odds filters">
        <div className="control-group">
          <span className="control-label">League</span>
          <div className="segmented-control">
            {SPORTS.map((option) => (
              <button
                className={sport === option.id ? "active" : ""}
                key={option.id}
                onClick={() => {
                  setSport(option.id);
                  setSecondaryMarket(null);
                }}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="control-group market-group">
          <span className="control-label">Secondary market</span>
          <div className="market-tabs" role="tablist" aria-label="Market type">
            {secondaryMarkets.map((option) => (
              <button
                aria-selected={secondaryMarket === option.id}
                className={secondaryMarket === option.id ? "active" : ""}
                key={option.id}
                onClick={() =>
                  setSecondaryMarket((current) => (current === option.id ? null : option.id))
                }
                role="tab"
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="intro-row">
        <div>
          <p className="eyebrow">{sport.toUpperCase()} board</p>
          <h2>Shop the line before it moves.</h2>
          <p className="lede">One event, every available book, with the gaps left visible.</p>
        </div>
        <div className="provider-strip" aria-label="Provider status">
          {providerStatus.length > 0 ? (
            providerStatus.map(([name, status]) => (
              <span className="provider-pill" key={name}>
                <span className="status-dot" />
                {name}: {status.replaceAll("_", " ")}
                {data?.provider_details[name]?.error_type && (
                  <small>({data.provider_details[name].error_type.replaceAll("_", " ")})</small>
                )}
                {data?.provider_details[name]?.stale && <small>(stale snapshot)</small>}
              </span>
            ))
          ) : (
            <span className="provider-pill muted">Waiting for provider data</span>
          )}
        </div>
      </section>

      {error && <div className="notice error-notice">{error}</div>}
      {freshness.warning && (
        <div className="notice stale-notice" role="alert">
          {freshness.warning}
        </div>
      )}
      <div className="odds-toolbar">
        {freshness.updated && !freshness.warning && (
          <p className="odds-updated">Odds last updated {freshness.updated}</p>
        )}
        <button
          className="refresh-button"
          disabled={refreshingKey !== null}
          onClick={() => void refreshAllOdds()}
          type="button"
        >
          {refreshingKey === REFRESH_ALL_KEY ? "Refreshing odds..." : "Refresh odds"}
        </button>
      </div>

      <section className="events-grid" aria-live="polite">
        {data?.events.length ? (
          data.events.map((comparison) => (
            <GlassCard className="event-block" key={comparison.event.id}>
              <div className="event-heading">
                <div>
                  <p className="event-meta">{formatStartTime(comparison.event.start_time)}</p>
                  <h3>
                    {comparison.event.away_team} <span>@</span> {comparison.event.home_team}
                  </h3>
                </div>
                <div className="event-actions">
                  <span className="event-league">{comparison.event.league}</span>
                  <button
                    className="refresh-button"
                    disabled={refreshingKey === comparison.event.id}
                    onClick={() => void refreshVisibleLines(comparison.event.id)}
                    type="button"
                  >
                    {refreshingKey === comparison.event.id ? "Refreshing event..." : "Refresh event"}
                  </button>
                </div>
              </div>
              <div className="top-line-grid">
                {TOP_LINE_MARKETS.map((option) => (
                  <MarketPanel
                    comparison={comparison}
                    key={option.id}
                    label={option.label}
                    market={option.id}
                  />
                ))}
              </div>
              <PredictionPanel
                contracts={contracts.filter(
                  (c) => c.match_status === "matched" && c.event_id === comparison.event.id,
                )}
                heading="Prediction markets"
              />
              {secondaryMarket && (
                <section className="secondary-market-panel">
                  <div className="secondary-market-heading">
                    <p className="eyebrow">Secondary market</p>
                    <h4>
                      {secondaryMarkets.find((option) => option.id === secondaryMarket)?.label ??
                        secondaryMarket}
                    </h4>
                  </div>
                  <ComparisonTable comparison={comparison} market={secondaryMarket} />
                </section>
              )}
            </GlassCard>
          ))
        ) : (
          <div className="empty-state">
            <span className="empty-index">01</span>
            <div>
              <h3>No odds loaded yet.</h3>
              <p>The comparison board is ready for a configured provider adapter.</p>
            </div>
          </div>
        )}
      </section>

      {contracts.some((c) => c.match_status !== "matched") && (
        <section className="other-predictions">
          <label>
            <input
              checked={showUnmatched}
              onChange={(event) => setShowUnmatched(event.target.checked)}
              type="checkbox"
            />{" "}
            Show other prediction markets (not matched to a sportsbook game)
          </label>
          {showUnmatched && (
            <PredictionPanel
              contracts={contracts.filter((c) => c.match_status !== "matched")}
              heading="Other prediction markets"
              showMatch
            />
          )}
        </section>
      )}

      <footer>
        <span>Prices are informational only.</span>
        <span>{data ? `Snapshot ${new Date(data.generated_at).toLocaleTimeString()}` : "No snapshot"}</span>
      </footer>
    </main>
  );
}

export default App;
