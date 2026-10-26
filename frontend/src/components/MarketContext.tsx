import { useState } from "react";

import {
  formatMetric, formatTally, isStale, metricRowsFor,
  type ContextResponse, type RecordSet, type TeamContext,
} from "../lib/matchup";

type Category = "spread" | "total" | "moneyline";
const CATEGORIES: { id: Category; label: string; record: keyof RecordSet }[] = [
  { id: "spread", label: "Spread", record: "ats" },
  { id: "total", label: "Total", record: "ou" },
  { id: "moneyline", label: "Moneyline", record: "su" },
];

function asOf(context: ContextResponse, key: string): string {
  const date = context.data_as_of[key];
  if (!date) return "No data timestamp";
  return `As of ${new Date(date).toLocaleString()}${isStale(date, key) ? " · stale" : ""}`;
}

function recordFor(team: TeamContext, group: string, category: Category): string {
  const record = team.records[group];
  if (!record) return "—";
  const field = CATEGORIES.find((entry) => entry.id === category)!.record;
  return formatTally(record[field]);
}

function availability(team: TeamContext, sport: string): string {
  if (sport === "nba") {
    const flag = team.star_flag;
    if (!flag) return `${team.team.abbr}: no tracked star alert`;
    const parts = [
      ...(flag.out.length ? [`out: ${flag.out.join(", ")}`] : []),
      ...(flag.questionable.length ? [`questionable: ${flag.questionable.join(", ")}`] : []),
    ];
    return `${team.team.abbr}: ${parts.length ? parts.join("; ") : "no tracked star alert"}`;
  }
  return team.qb_flag
    ? `${team.team.abbr}: last starting QB ${team.qb_flag.player} is ${team.qb_flag.status}`
    : `${team.team.abbr}: no starting-QB alert`;
}

function metricKeys(category: Category, sport: string): string[] {
  if (sport === "nba") return category === "total" ? ["pace", "off_rtg"] : ["net_rtg"];
  return category === "total" ? ["points_for_pg"] : ["off_epa_play", "def_epa_play"];
}

export function MarketContext({
  context, loading = false, error = null,
}: { context: ContextResponse | null; loading?: boolean; error?: Error | null }) {
  const [category, setCategory] = useState<Category>("spread");
  const injuryTime = context?.data_as_of.injuries;
  const freshInjuries = Boolean(injuryTime && !isStale(injuryTime, "injuries"));
  return (
    <section className="market-context" aria-label="Historical context by market">
      <div className="market-context-heading">
        <h3>Historical context</h3>
        <div className="bet-actions" aria-label="Bet category">
          {CATEGORIES.map((entry) => (
            <button aria-pressed={category === entry.id} className="text-button" key={entry.id}
              onClick={() => setCategory(entry.id)} type="button">{entry.label}</button>
          ))}
        </div>
      </div>
      {loading ? <p className="empty-grid">Loading historical context…</p>
        : error ? <p className="notice error-notice" role="alert">Historical context unavailable: {error.message}</p>
          : !context ? <p className="empty-grid">Historical context unavailable.</p> : (
        <>
          <p className="freshness">Records {asOf(context, "records")} · Ratings {asOf(context, "efficiency")}
            {" · "}Injuries {asOf(context, "injuries")}</p>
          <div className="table-scroll">
            <table aria-label={`${category} historical records`}>
              <thead><tr><th scope="col">{category === "spread" ? "ATS" : category === "total" ? "Over / under" : "Straight up"}</th>
                <th scope="col">{context.away.team.abbr}</th><th scope="col">{context.home.team.abbr}</th></tr></thead>
              <tbody>
                <tr><th scope="row">This season</th><td>{recordFor(context.away, "season", category)}</td><td>{recordFor(context.home, "season", category)}</td></tr>
                <tr><th scope="row">Last {context.last_n} games</th><td>{recordFor(context.away, "last_n", category)}</td><td>{recordFor(context.home, "last_n", category)}</td></tr>
              </tbody>
            </table>
          </div>
          <p className="price-comparison-note">Recent games can cross a season boundary. {category === "moneyline"
            ? "Straight-up records use final scores."
            : "ATS and O/U records use historical closing lines."}</p>
          <div className="market-metrics">
            {metricKeys(category, context.sport).map((key) => {
              const row = metricRowsFor(context.sport).find((entry) => entry.key === key);
              if (!row) return null;
              return <p key={key}>{row.label}: {context.away.team.abbr} {formatMetric(context.away.efficiency[key], row)}
                {" · "}{context.home.team.abbr} {formatMetric(context.home.efficiency[key], row)}</p>;
            })}
          </div>
          <p className="market-availability">{freshInjuries
            ? `Current availability: ${availability(context.away, context.sport)}; ${availability(context.home, context.sport)}.`
            : "Current availability unavailable: injury data is missing or stale."}</p>
          {context.sport === "nba" && <p className="price-comparison-note">Earlier head-to-head results do not account for which players appeared. No historical lineup adjustment is applied here.</p>}
        </>
      )}
    </section>
  );
}
