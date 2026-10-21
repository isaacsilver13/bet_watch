import { useEffect, useMemo, useRef, useState } from "react";

import { GlassCard } from "./components/GlassCard";
import { OddsMatrix, SecondaryMarketTable, TOP_LINE_MARKETS } from "./components/OddsMatrix";
import { PredictionPanel } from "./components/PredictionMarkets";
import { fetchOdds, fetchPredictionMarkets, fetchStatus } from "./lib/api";
import {
  isPlayerPropMarket,
  type ComparisonResponse,
  type MarketType,
  type PredictionMarketsResponse,
  type ProviderDetails,
  type Sport,
  type StatusResponse,
} from "./types";

const SPORTS: Array<{ id: Sport; label: string }> = [
  { id: "nfl", label: "NFL" },
  { id: "nba", label: "NBA" },
];

const SECONDARY_MARKETS_BY_SPORT: Record<Sport, Array<{ id: MarketType; label: string }>> = {
  nfl: [
    { id: "alternate_spread", label: "Alternate spread" },
    { id: "player_passing_yards", label: "Passing yards" },
    { id: "player_rushing_yards", label: "Rushing yards" },
    { id: "player_passing_rushing_yards", label: "Pass + rush" },
    { id: "player_receiving_yards", label: "Receiving yards" },
    { id: "player_anytime_touchdown", label: "Anytime TD" },
  ],
  nba: [
    { id: "alternate_spread", label: "Alternate spread" },
    { id: "player_points", label: "Points" },
    { id: "player_rebounds", label: "Rebounds" },
    { id: "player_assists", label: "Assists" },
    { id: "player_threes", label: "Threes" },
    { id: "player_points_rebounds_assists", label: "Pts + reb + ast" },
  ],
};

const VERY_OLD_AFTER_MS = 36 * 60 * 60 * 1000;

function initialSport(): Sport {
  return new URLSearchParams(window.location.search).get("sport") === "nba" ? "nba" : "nfl";
}

function urlFilters() {
  const next = new URLSearchParams(window.location.search);
  const requestedMarket = next.get("market");
  return {
    sport: next.get("sport") === "nba" ? "nba" as Sport : "nfl" as Sport,
    date: next.get("date") ?? "",
    team: next.get("team") ?? "",
    market: TOP_LINE_MARKETS.some(({ id }) => id === requestedMarket)
      ? requestedMarket as MarketType
      : "moneyline" as MarketType,
  };
}

function providerTone(state: string): string {
  if (state === "error") return "error";
  if (state === "stale") return "warning";
  if (["disabled", "waiting", "loading", "not_configured"].includes(state)) return "muted";
  return "healthy";
}

function formatStartTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function localDate(value: string): string {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function freshness(details: ProviderDetails | undefined, hasEvents: boolean) {
  if (!details) return { updated: null, warning: null };
  const refreshedAt = details.last_refreshed_at ? new Date(details.last_refreshed_at) : null;
  const updated = refreshedAt ? refreshedAt.toLocaleString() : null;
  const reason = details.error_type?.replaceAll("_", " ") ?? "unknown error";
  if (details.status === "error" && !hasEvents) {
    return { updated, warning: `Sportsbook odds are unavailable (${reason}).` };
  }
  if (details.stale) {
    return { updated, warning: `Showing saved sportsbook odds${updated ? ` from ${updated}` : ""}; refresh failed (${reason}).` };
  }
  if (refreshedAt && Date.now() - refreshedAt.getTime() > VERY_OLD_AFTER_MS) {
    return { updated, warning: `Sportsbook odds are over 36 hours old (last updated ${updated}).` };
  }
  return { updated, warning: null };
}

function mergeEvent(current: ComparisonResponse, refreshed: ComparisonResponse, eventId: string) {
  if (Object.values(refreshed.provider_status).some((status) => status === "error")) {
    return { ...current, provider_status: refreshed.provider_status, provider_details: refreshed.provider_details };
  }
  const replacement = refreshed.events.find((entry) => entry.event.id === eventId);
  return {
    ...current,
    generated_at: refreshed.generated_at,
    provider_status: refreshed.provider_status,
    provider_details: refreshed.provider_details,
    events: current.events.map((entry) => entry.event.id === eventId && replacement ? replacement : entry),
  };
}

function App() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const [sport, setSport] = useState<Sport>(initialSport);
  const [dateFilter, setDateFilter] = useState(params.get("date") ?? "");
  const [teamFilter, setTeamFilter] = useState(params.get("team") ?? "");
  const [mobileMarket, setMobileMarket] = useState<MarketType>(
    TOP_LINE_MARKETS.some((market) => market.id === params.get("market"))
      ? params.get("market") as MarketType
      : "moneyline",
  );
  const [secondaryMarket, setSecondaryMarket] = useState<MarketType | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [oddsBySport, setOddsBySport] = useState<Partial<Record<Sport, ComparisonResponse>>>({});
  const [predictionsBySport, setPredictionsBySport] = useState<Partial<Record<Sport, PredictionMarketsResponse>>>({});
  const [oddsLoading, setOddsLoading] = useState(true);
  const [predictionLoading, setPredictionLoading] = useState(false);
  const [oddsError, setOddsError] = useState<string | null>(null);
  const [predictionError, setPredictionError] = useState<string | null>(null);
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null);
  const [refreshingKey, setRefreshingKey] = useState<string | null>(null);
  const [showUnmatched, setShowUnmatched] = useState(false);
  const firstHistoryWrite = useRef(true);
  const restoringHistory = useRef(false);

  const data = oddsBySport[sport] ?? null;
  const predictionData = predictionsBySport[sport] ?? null;
  const contracts = predictionData?.contracts ?? [];
  const canExpensiveRefresh = status?.capabilities.public_expensive_refresh ?? false;
  const needsPlayerProps = secondaryMarket !== null && isPlayerPropMarket(secondaryMarket);

  useEffect(() => {
    const controller = new AbortController();
    fetchStatus(controller.signal).then(setStatus).catch(() => setStatus(null));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const restore = () => {
      const filters = urlFilters();
      restoringHistory.current = true;
      setSport(filters.sport);
      setDateFilter(filters.date);
      setTeamFilter(filters.team);
      setMobileMarket(filters.market);
      setSecondaryMarket(null);
      setShowUnmatched(false);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  useEffect(() => {
    const next = new URLSearchParams();
    next.set("sport", sport);
    if (dateFilter) next.set("date", dateFilter);
    if (teamFilter) next.set("team", teamFilter);
    next.set("market", mobileMarket);
    if (restoringHistory.current) {
      restoringHistory.current = false;
      return;
    }
    const nextUrl = `${window.location.pathname}?${next.toString()}`;
    if (firstHistoryWrite.current) {
      firstHistoryWrite.current = false;
      window.history.replaceState(null, "", nextUrl);
    } else if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.pushState(null, "", nextUrl);
    }
  }, [sport, dateFilter, teamFilter, mobileMarket]);

  useEffect(() => {
    const controller = new AbortController();
    setOddsLoading(true);
    setOddsError(null);
    // Opening the board only reads the stored snapshot; player props arrive with "Refresh odds".
    fetchOdds(sport, { signal: controller.signal }).then(({ data: nextData, refreshIgnored }) => {
      if (controller.signal.aborted) return;
      setOddsBySport((current) => ({ ...current, [sport]: nextData }));
      setRefreshNotice(refreshIgnored ? `Provider ignored the request: ${refreshIgnored}.` : null);
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setOddsError(error instanceof Error ? error.message : "Unable to load sportsbook odds");
    }).finally(() => {
      if (!controller.signal.aborted) setOddsLoading(false);
    });
    return () => controller.abort();
  }, [sport]);

  useEffect(() => {
    if (!data) return;
    const controller = new AbortController();
    setPredictionLoading(true);
    setPredictionError(null);
    fetchPredictionMarkets(sport, controller.signal).then((next) => {
      if (!controller.signal.aborted) {
        setPredictionsBySport((current) => {
          const previous = current[sport];
          const replacement = next.provider_status.kalshi === "error" && previous?.contracts.length
            ? {
                ...next,
                provider_status: { ...next.provider_status, kalshi: "stale" },
                provider_details: {
                  ...next.provider_details,
                  kalshi: {
                    ...previous.provider_details.kalshi,
                    ...next.provider_details.kalshi,
                    status: "stale",
                    stale: true,
                    last_refreshed_at: previous.provider_details.kalshi?.last_refreshed_at
                      ?? next.provider_details.kalshi?.last_refreshed_at
                      ?? null,
                  },
                },
                contracts: previous.contracts,
                contract_counts: previous.contract_counts,
              }
            : next;
          return { ...current, [sport]: replacement };
        });
      }
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setPredictionError(error instanceof Error ? error.message : "Prediction markets are unavailable");
    }).finally(() => {
      if (!controller.signal.aborted) setPredictionLoading(false);
    });
    return () => controller.abort();
  }, [sport, data?.generated_at]);

  const visibleEvents = useMemo(() => {
    const team = teamFilter.trim().toLocaleLowerCase();
    return [...(data?.events ?? [])]
      .filter(({ event }) => !dateFilter || localDate(event.start_time) === dateFilter)
      .filter(({ event }) => !team || `${event.away_team} ${event.home_team}`.toLocaleLowerCase().includes(team))
      .sort((a, b) => new Date(a.event.start_time).getTime() - new Date(b.event.start_time).getTime());
  }, [data, dateFilter, teamFilter]);

  const sportsbookDetails = Object.values(data?.provider_details ?? {})[0];
  const sportsbookFreshness = freshness(sportsbookDetails, (data?.events.length ?? 0) > 0);
  const kalshiDetails = predictionData?.provider_details.kalshi;
  const kalshiStatus = predictionData?.provider_status.kalshi ?? (predictionLoading ? "loading" : "waiting");

  async function refreshAll() {
    setRefreshingKey("all");
    try {
      const result = await fetchOdds(sport, { forceRefresh: true, includeProps: needsPlayerProps });
      setOddsBySport((current) => ({ ...current, [sport]: result.data }));
      setRefreshNotice(result.refreshIgnored ? `Refresh was ${result.refreshIgnored}. Cached odds remain visible.` : "Odds refreshed.");
    } catch (error) {
      setOddsError(error instanceof Error ? error.message : "Unable to refresh odds");
    } finally {
      setRefreshingKey(null);
    }
  }

  async function refreshEvent(eventId: string) {
    setRefreshingKey(eventId);
    try {
      const result = await fetchOdds(sport, { eventId, forceRefresh: true, includeProps: needsPlayerProps });
      setOddsBySport((current) => {
        const existing = current[sport];
        return { ...current, [sport]: existing ? mergeEvent(existing, result.data, eventId) : result.data };
      });
      setRefreshNotice(result.refreshIgnored ? `Event refresh was ${result.refreshIgnored}. Cached odds remain visible.` : "Event refreshed.");
    } catch (error) {
      setOddsError(error instanceof Error ? error.message : "Unable to refresh event");
    } finally {
      setRefreshingKey(null);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div><p className="eyebrow">NFL + NBA market desk</p><h1>Lineup</h1></div>
        <div className="connection-state"><span className={`status-dot ${oddsError ? "error" : ""}`} /><span>{oddsError ? "Sportsbook issue" : "API connected"}</span></div>
      </header>

      <section className="control-band" aria-label="Board filters">
        <div className="control-group"><span className="control-label">League</span><div className="segmented-control">
          {SPORTS.map((option) => <button className={sport === option.id ? "active" : ""} key={option.id} onClick={() => { setSport(option.id); setSecondaryMarket(null); setShowUnmatched(false); }} type="button">{option.label}</button>)}
        </div></div>
        <label className="control-group"><span className="control-label">Game date</span><input onChange={(event) => setDateFilter(event.target.value)} type="date" value={dateFilter} /></label>
        <label className="control-group team-filter"><span className="control-label">Team</span><input onChange={(event) => setTeamFilter(event.target.value)} placeholder="Filter teams" type="search" value={teamFilter} /></label>
        {(dateFilter || teamFilter) && <button className="text-button" onClick={() => { setDateFilter(""); setTeamFilter(""); }} type="button">Clear filters</button>}
      </section>

      <section className="intro-row">
        <div><p className="eyebrow">{sport.toUpperCase()} board</p><h2>Compare the market, not just the headline.</h2><p className="lede">Sportsbook prices stay side by side. Prediction contracts keep their native quotes and rules.</p></div>
        <div className="provider-strip" aria-label="Provider status">
          {Object.entries(data?.provider_status ?? {}).map(([name, providerState]) => <span className={`provider-pill provider-${providerTone(providerState)}`} key={name}><span className="status-dot" />{name}: {providerState.replaceAll("_", " ")}</span>)}
          <span className={`provider-pill provider-${providerTone(kalshiStatus)}`}><span className="status-dot" />kalshi: {kalshiStatus.replaceAll("_", " ")}{predictionData?.contract_counts ? ` · ${predictionData.contract_counts.matched ?? 0} matched` : ""}</span>
        </div>
      </section>

      {oddsError && <div className="notice error-notice" role="alert">{oddsError}</div>}
      {sportsbookFreshness.warning && <div className="notice stale-notice" role="alert">{sportsbookFreshness.warning}</div>}
      {predictionError && <div className="notice prediction-notice" role="status">Kalshi: {predictionError}. Existing sportsbook data is unaffected.</div>}
      {kalshiDetails?.stale && <div className="notice prediction-notice" role="status">Kalshi refresh failed; showing the last successful snapshot.</div>}
      {refreshNotice && <div className="notice info-notice" role="status">{refreshNotice}</div>}

      <section className="market-toolbar" aria-label="Market controls">
        <div><span className="control-label">Main lines</span><div className="market-tabs" role="tablist" aria-label="Main market">
          {TOP_LINE_MARKETS.map((market) => <button aria-selected={mobileMarket === market.id} className={mobileMarket === market.id ? "active" : ""} key={market.id} onClick={() => setMobileMarket(market.id)} role="tab" type="button">{market.label}</button>)}
        </div></div>
        <div><span className="control-label">Secondary market</span><div className="market-tabs secondary-tabs">
          {SECONDARY_MARKETS_BY_SPORT[sport].map((market) => <button className={secondaryMarket === market.id ? "active" : ""} key={market.id} onClick={() => setSecondaryMarket((current) => current === market.id ? null : market.id)} type="button">{market.label}</button>)}
        </div></div>
      </section>

      <div className="odds-toolbar">
        <p className="odds-updated">{sportsbookFreshness.updated ? `Sportsbook odds observed ${sportsbookFreshness.updated}` : oddsLoading ? "Loading sportsbook odds…" : "No sportsbook snapshot yet"}</p>
        {canExpensiveRefresh ? <button className="refresh-button" disabled={refreshingKey !== null} onClick={() => void refreshAll()} type="button">{refreshingKey === "all" ? "Refreshing…" : "Refresh odds"}</button> : <span className="refresh-policy">Public board updates from the server cache</span>}
      </div>

      {needsPlayerProps && !canExpensiveRefresh && <div className="notice info-notice" role="status">Player props appear when an operator has populated the protected provider cache. The browser never receives the admin refresh secret.</div>}

      <section className="events-grid" aria-busy={oddsLoading} aria-live="polite">
        {oddsLoading && !data && <div className="skeleton-card" aria-label="Loading games"><span /><span /><span /></div>}
        {visibleEvents.map((comparison) => (
          <GlassCard className="event-block" key={comparison.event.id}>
            <div className="event-heading">
              <div><p className="event-meta">{formatStartTime(comparison.event.start_time)}</p><h3>{comparison.event.away_team} <span>@</span> {comparison.event.home_team}</h3></div>
              <div className="event-actions"><span className="event-league">{comparison.event.league}</span>{canExpensiveRefresh && <button className="refresh-button" disabled={refreshingKey !== null} onClick={() => void refreshEvent(comparison.event.id)} type="button">{refreshingKey === comparison.event.id ? "Refreshing…" : "Refresh event"}</button>}</div>
            </div>
            <OddsMatrix comparison={comparison} mobileMarket={mobileMarket} />
            <PredictionPanel contracts={contracts.filter((contract) => contract.match_status === "matched" && contract.event_id === comparison.event.id)} details={kalshiDetails} heading="Game winner contracts" />
            {secondaryMarket && <section className="secondary-market-panel"><div className="section-heading"><div><p className="eyebrow">Secondary market</p><h4>{SECONDARY_MARKETS_BY_SPORT[sport].find((market) => market.id === secondaryMarket)?.label}</h4></div></div><SecondaryMarketTable comparison={comparison} market={secondaryMarket} /></section>}
          </GlassCard>
        ))}
        {!oddsLoading && data && visibleEvents.length === 0 && <div className="empty-state"><span className="empty-index">00</span><div><h3>No games match these filters.</h3><p>Clear the date or team filter to restore the full board.</p></div></div>}
      </section>

      {contracts.some((contract) => contract.match_status !== "matched") && <section className="other-predictions"><button aria-expanded={showUnmatched} className="text-button" onClick={() => setShowUnmatched((current) => !current)} type="button">{showUnmatched ? "Hide" : "Show"} other prediction markets ({contracts.filter((contract) => contract.match_status !== "matched").length})</button>{showUnmatched && <PredictionPanel contracts={contracts.filter((contract) => contract.match_status !== "matched")} details={kalshiDetails} heading="Unmatched and ambiguous contracts" showMatch />}</section>}

      <footer><span>Informational only · no bet placement</span><span>{predictionLoading ? "Refreshing Kalshi…" : data ? `Board generated ${new Date(data.generated_at).toLocaleTimeString()}` : "No snapshot"}</span></footer>
    </main>
  );
}

export default App;
