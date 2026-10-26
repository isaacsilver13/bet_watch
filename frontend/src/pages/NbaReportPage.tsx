import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchPaperLedgerDay, fetchPaperLedgerStatus, fetchSavedNbaPaperPreview, fetchSavedNbaReport, type SavedNbaPick } from "../lib/reports";

type SortKey = "ev" | "game" | "book";
type TierFilter = "targeted" | "watchlist";

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function price(value: number | null): string {
  return value === null ? "—" : `${value > 0 ? "+" : ""}${value}`;
}

function dollars(value: number): string {
  return `$${value.toFixed(2)}`;
}

function profileLabel(profile: string): string {
  return profile === "all" ? "All eligible" : `Top ${profile.slice(4)}`;
}

function pickLabel(pick: SavedNbaPick): string {
  if (pick.market === "moneyline") return `${pick.team} moneyline`;
  return `${pick.team} ${pick.line !== null && pick.line > 0 ? "+" : ""}${pick.line}`;
}

function comparableModelProb(pick: SavedNbaPick): number | null {
  if (pick.market === "moneyline") return pick.model_prob;
  if (pick.model_prob_conditional !== undefined) return pick.model_prob_conditional;
  return pick.line !== null && !Number.isInteger(pick.line) ? pick.model_prob : null;
}

export function NbaReportPage() {
  const { date = "" } = useParams();
  const [tier, setTier] = useState<TierFilter>("targeted");
  const [sort, setSort] = useState<SortKey>("ev");
  const [paperProfile, setPaperProfile] = useState("all");
  const [showMarket, setShowMarket] = useState(true);
  const { data, error, isLoading } = useQuery({
    queryKey: ["nba-report", date],
    queryFn: ({ signal }) => fetchSavedNbaReport(date, signal),
    enabled: /^\d{4}-\d{2}-\d{2}$/.test(date),
    staleTime: Infinity, // saved reports are immutable
  });
  const paper = useQuery({
    queryKey: ["nba-paper-preview", date],
    queryFn: ({ signal }) => fetchSavedNbaPaperPreview(date, signal),
    enabled: Boolean(data),
    staleTime: Infinity,
    retry: false,
  });
  const ledger = useQuery({
    queryKey: ["nba-paper-ledger-status"],
    queryFn: ({ signal }) => fetchPaperLedgerStatus(signal),
    enabled: Boolean(data),
    staleTime: 60_000,
    retry: false,
  });
  const recordedDay = useQuery({
    queryKey: ["nba-paper-ledger-day", date],
    queryFn: ({ signal }) => fetchPaperLedgerDay(date, signal),
    enabled: Boolean(data),
    staleTime: 60_000,
    retry: false,
  });
  const recordedProfile = recordedDay.data?.profiles.find((profile) => profile.profile === paperProfile);
  const picks = useMemo(() => {
    const selected = (data?.picks ?? []).filter((pick) => pick.tier === tier);
    return selected.sort((a, b) => {
      if (sort === "ev") return b.ev - a.ev || a.game_id - b.game_id;
      if (sort === "book") return a.book.localeCompare(b.book) || b.ev - a.ev;
      return `${a.away} @ ${a.home}`.localeCompare(`${b.away} @ ${b.home}`) || b.ev - a.ev;
    });
  }, [data?.picks, tier, sort]);

  return (
    <main className="app-shell nba-report">
      <header className="topbar">
        <div><p className="eyebrow">Saved NBA report</p><h1>{date}</h1></div>
        <nav className="app-nav" aria-label="Sections"><Link to={`/nba/${date}`}>NBA slate</Link> · <Link to="/bets">Bets</Link> · <a href={`/api/v1/nba/reports/${date}/digest-preview`} rel="noopener noreferrer" target="_blank">Email preview</a></nav>
      </header>
      {isLoading && <div className="skeleton-card" aria-label="Loading report"><span /><span /><span /></div>}
      {error && <p className="notice error-notice" role="alert">{error.message} <Link to="/login">Sign in</Link></p>}
      {data && <>
        <section className="report-meta" aria-label="Report provenance">
          <span>Model: {data.model_version}</span>
          <span>Results through: {data.data_through}</span>
          <span>Latest odds capture: {data.odds_as_of ? new Date(data.odds_as_of).toLocaleString() : "unavailable"}</span>
          <span>Generated: {new Date(data.generated_at).toLocaleString()}</span>
        </section>
        <section aria-labelledby="outlook-heading">
          <div className="section-heading"><div><p className="eyebrow">All games</p><h2 id="outlook-heading">Game outlook</h2></div>
            <label className="report-toggle"><input checked={showMarket} onChange={(event) => setShowMarket(event.target.checked)} type="checkbox" />Compare market</label>
          </div>
          <div className="report-games">
            {data.games.map((game) => <article className="report-game" key={game.game_id}>
              <h3>{game.away} @ {game.home}</h3>
              <p>Projected home margin <strong>{game.pred_margin > 0 ? "+" : ""}{game.pred_margin.toFixed(1)}</strong></p>
              <div className="report-prob-row"><span>Model home win</span><strong>{percent(game.p_home_win)}</strong></div>
              <div aria-label={`${game.home} model home win probability ${percent(game.p_home_win)}`} className="report-prob-track"><span style={{ width: `${game.p_home_win * 100}%` }} /></div>
              {showMarket && <><div className="report-prob-row"><span>Market home win</span><strong>{percent(game.market_home_win_prob)}</strong></div>
                <div aria-label={`${game.home} market home win probability ${percent(game.market_home_win_prob)}`} className="report-prob-track market"><span style={{ width: `${(game.market_home_win_prob ?? 0) * 100}%` }} /></div></>}
              <p className="report-detail">{game.books} paired books · market home spread {game.market_spread_home === null ? "—" : game.market_spread_home > 0 ? `+${game.market_spread_home}` : game.market_spread_home}{game.pred_sigma !== undefined ? ` · margin SD ${game.pred_sigma.toFixed(1)}` : ""}{game.odds_as_of ? ` · odds captured ${new Date(game.odds_as_of).toLocaleString()}` : ""}</p>
            </article>)}
          </div>
          <p className="report-caveat">Margin uncertainty is the current model's global estimate; it is not yet adjusted for expected lineups. Historical head-to-head results do not establish who played tonight.</p>
        </section>
        <section aria-labelledby="report-picks-heading">
          <div className="section-heading"><div><p className="eyebrow">Saved decisions</p><h2 id="report-picks-heading">Picks</h2></div></div>
          <div className="report-controls">
            <label>Show <select onChange={(event) => setTier(event.target.value as TierFilter)} value={tier}><option value="targeted">Targeted value picks</option><option value="watchlist">Spread watchlist</option></select></label>
            <label>Sort <select onChange={(event) => setSort(event.target.value as SortKey)} value={sort}><option value="ev">Net EV</option><option value="game">Game</option><option value="book">Book</option></select></label>
          </div>
          {picks.length === 0 ? <p className="notice info-notice">No {tier === "targeted" ? "value picks cleared the current threshold" : "spread watchlist rows were saved"}.</p> :
            <div className="report-table-scroll"><table className="report-table"><thead><tr><th>Game</th><th>Selection</th><th>Book</th><th>Selected price</th><th>FanDuel</th><th>Model (no push)</th><th>Market (no push)</th><th>Push</th><th>Net EV</th></tr></thead>
              <tbody>{picks.map((pick) => <tr key={`${pick.game_id}-${pick.market}-${pick.side}`}><td>{pick.away} @ {pick.home}</td><td>{pickLabel(pick)}</td><td>{pick.book}</td><td>{price(pick.price_american)}</td><td>{price(pick.fanduel_price_american)}</td><td>{percent(comparableModelProb(pick))}</td><td>{percent(pick.market_prob)}</td><td>{percent(pick.push_prob ?? null)}</td><td>{percent(pick.ev)}</td></tr>)}</tbody></table></div>}
          <p className="report-caveat">Spread model and market percentages are conditional on no push; EV includes push outcomes. Older integer-spread reports without a saved push estimate show no comparable model percentage. Prices and EV reflect the saved snapshot. Check live availability and price before placing any bet. Watchlist rows are exploratory.</p>
        </section>
        <section aria-labelledby="paper-heading">
          <div className="section-heading"><div><p className="eyebrow">Paper study</p><h2 id="paper-heading">$1,000 profile preview</h2></div></div>
          <p className="report-caveat">Each variant starts independently at $1,000, selects saved targeted moneylines above 3% net EV, and uses quarter Kelly with exposure caps. The preview recalculates this saved report with a $1,000 opening balance; the recorded ledger below shows actual carried-forward paper balances.</p>
          {paper.isLoading && <p>Calculating paper allocations…</p>}
          {paper.error && <p className="notice info-notice">Paper preview unavailable: {paper.error.message}</p>}
          {paper.data && <div className="report-table-scroll"><table className="report-table"><thead><tr><th>Variant</th><th>Quote-linked candidates assessed</th><th>Selected bets</th><th>Stake + fees</th><th>Opening bankroll</th></tr></thead>
            <tbody>{paper.data.allocations.map((allocation) => <tr key={allocation.profile}><td>{profileLabel(allocation.profile)}</td><td>{paper.data.candidate_count}</td><td>{allocation.bets.length}</td><td>{dollars(allocation.bets.reduce((sum, bet) => sum + bet.stake + bet.fee, 0))}</td><td>{dollars(allocation.opening_bankroll)}</td></tr>)}</tbody></table></div>}
          <h3>Recorded decision for {date}</h3>
          {recordedDay.isLoading && <p>Loading recorded paper decisions…</p>}
          {recordedDay.error && <p className="notice info-notice">Recorded paper decisions unavailable: {recordedDay.error.message}</p>}
          {recordedDay.data === null && <p className="notice info-notice">No paper decision was recorded for this slate. The preview above is not a historical bet record.</p>}
          {recordedDay.data && <>
            {(!recordedDay.data.report_matches_recorded || !recordedDay.data.forecast_matches_recorded) &&
              <p className="notice error-notice" role="alert">The current saved report or forecast differs from the frozen paper decision. Use the recorded wager table below for the paper record; do not compare it with the preview or picks above as if they came from the same snapshot.</p>}
            <label>Recorded profile <select value={paperProfile} onChange={(event) => setPaperProfile(event.target.value)}>
              {recordedDay.data.profiles.map((profile) => <option key={profile.profile} value={profile.profile}>{profileLabel(profile.profile)}</option>)}
            </select></label>
            {recordedProfile && <>
              <p className="report-caveat">Decision frozen {new Date(recordedProfile.decision_at).toLocaleString()} · opening cash {dollars(recordedProfile.opening_cash)} · policy {recordedProfile.policy.version} ({percent(recordedProfile.policy.min_net_ev)} minimum net EV, {percent(recordedProfile.policy.kelly_fraction)} Kelly).</p>
              {recordedProfile.bets.length === 0 ? <p className="notice info-notice">This profile recorded no wagers for the slate.</p> :
                <div className="report-table-scroll"><table className="report-table"><thead><tr><th>Game</th><th>Selection</th><th>Book</th><th>Saved price</th><th>Model win</th><th>Net EV</th><th>Stake</th><th>Fee</th><th>Quote capture / ID</th><th>Result / saved score</th><th>Payout</th><th>Net return</th></tr></thead>
                  <tbody>{recordedProfile.bets.map((bet) => <tr key={bet.wager_id}><td>{bet.away} @ {bet.home}</td><td>{bet.side === "home" ? bet.home : bet.away} moneyline</td><td>{bet.book}</td><td>{price(bet.price_american)}</td><td>{percent(bet.win_prob)}</td><td>{percent(bet.net_ev)}</td><td>{dollars(bet.stake)}</td><td>{dollars(bet.fee)}</td><td>{new Date(bet.quote_observed_at).toLocaleString()} · #{bet.quote_observation_id}</td><td>{bet.result ? `${bet.result} · ${bet.away} ${bet.away_score}–${bet.home} ${bet.home_score}` : "Open"}</td><td>{bet.payout === null ? "—" : dollars(bet.payout)}</td><td>{bet.net_return === null ? "—" : dollars(bet.net_return)}</td></tr>)}</tbody></table></div>}
              <details><summary>Decision provenance</summary><p>Report SHA-256: <code>{recordedProfile.report_sha256}</code><br />Forecast SHA-256: <code>{recordedProfile.forecast_sha256}</code></p></details>
            </>}
          </>}
          <h3>Current paper ledger across all recorded slates</h3>
          <p className="report-caveat">These are current balances, not results for the selected report date. Cash is available for the next allocation. Equity at cost adds open stakes back to cash; it is not a market value or projected payout. Settled net and ROI include fees and exclude open bets. These are paper results, not evidence of a profitable strategy.</p>
          {ledger.isLoading && <p>Loading paper balances…</p>}
          {ledger.error && <p className="notice info-notice">Paper ledger unavailable: {ledger.error.message}</p>}
          {ledger.data && <div className="report-table-scroll"><table className="report-table"><thead><tr><th>Variant</th><th>Cash</th><th>Open stake</th><th>Equity at cost</th><th>Open / settled bets</th><th>Settled net</th><th>Settled ROI</th><th>Recorded days</th><th>Latest slate</th></tr></thead>
            <tbody>{ledger.data.profiles.map((profile) => <tr key={profile.profile}><td>{profileLabel(profile.profile)}</td><td>{dollars(profile.cash)}</td><td>{dollars(profile.open_stake)}</td><td>{dollars(profile.equity_at_cost)}</td><td>{profile.open_bets} / {profile.settled_bets}</td><td>{dollars(profile.settled_net)}</td><td>{percent(profile.settled_roi)}</td><td>{profile.recorded_days}</td><td>{profile.last_slate_date ?? "—"}</td></tr>)}</tbody></table></div>}
        </section>
      </>}
    </main>
  );
}
