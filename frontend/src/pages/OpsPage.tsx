import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { GlassCard } from "../components/GlassCard";
import { fetchHealth, type OpsHealth } from "../lib/ops";

const MARKETS = ["moneyline", "spread", "total"];

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) : "never";
}

function Problems({ health }: { health: OpsHealth }) {
  const items = [
    health.run_due_stale && `run-due has not fired since ${when(health.run_due_last_at)}. Is the Task Scheduler job registered and the PC on?`,
    health.stuck_raw_saved.length > 0 && `${health.stuck_raw_saved.length} paid response(s) saved but not parsed: ${health.stuck_raw_saved.join(", ")}`,
    health.unlinked_events.length > 0 && `${health.unlinked_events.length} odds event(s) match no game: ${health.unlinked_events.join(", ")}`,
    health.finals_missing_close > 0 && `${health.finals_missing_close} finished game(s) still have no ESPN close.`,
  ].filter((item): item is string => typeof item === "string");
  if (!items.length) return <div className="notice info-notice" role="status">Nothing needs attention.</div>;
  return (
    <div className="notice error-notice" role="alert">
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}

export function OpsPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["ops-health"], queryFn: ({ signal }) => fetchHealth(signal) });
  const coverage = data?.snapshot_coverage;
  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">System</p>
          <h1>Health</h1>
        </div>
        <nav className="app-nav" aria-label="Sections"><Link to="/nfl">Slate</Link> · <Link to="/bets">Bets</Link></nav>
      </header>

      {error && <div className="notice error-notice" role="alert">{error.message}</div>}
      {isLoading && <div className="skeleton-card" aria-label="Loading health"><span /><span /><span /></div>}
      {data && (
        <>
          <Problems health={data} />
          <dl className="summary-cards">
            <div className="stat"><dt>Last run-due</dt><dd>{when(data.run_due_last_at)}</dd></div>
            <div className="stat"><dt>Odds credits left</dt><dd>{data.credits_remaining ?? "—"}</dd></div>
            <div className="stat">
              <dt>Slots today</dt>
              <dd>{Object.entries(data.slots_today).map(([state, count]) => `${count} ${state}`).join(" · ") || "none"}</dd>
            </div>
          </dl>

          <GlassCard className="slate-card">
            <h3>Latest slot of each job</h3>
            <div className="table-scroll">
              <table>
                <thead><tr><th scope="col">Job</th><th scope="col">State</th><th scope="col">Slot</th><th scope="col">Updated</th></tr></thead>
                <tbody>
                  {data.jobs.map((job) => (
                    <tr key={job.job}>
                      <th scope="row">{job.job}</th><td>{job.state}</td><td>{job.slot_key}</td><td>{when(job.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </GlassCard>

          {coverage && (
            <GlassCard className="slate-card">
              <h3>Last snapshot: {coverage.complete} of {coverage.targeted} games fully covered</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr><th scope="col">Book</th>{MARKETS.map((market) => <th key={market} scope="col">{market}</th>)}</tr>
                  </thead>
                  <tbody>
                    {Object.entries(coverage.books).map(([book, markets]) => (
                      <tr key={book}>
                        <th scope="row">{book}</th>
                        {MARKETS.map((market) => <td key={market}>{markets[market] ?? 0} games</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </GlassCard>
          )}
        </>
      )}
    </main>
  );
}
