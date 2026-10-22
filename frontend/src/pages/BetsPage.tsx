import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { GlassCard } from "../components/GlassCard";
import {
  describeBet,
  describePick,
  fetchBets,
  fetchModelPicks,
  fetchSummary,
  formatMoney,
  formatPoints,
  formatSigned,
  venueLabel,
  voidBet,
  type Bet,
  type BetFilter,
  type GroupBy,
  type ModelPicks,
  type SummaryRow,
  type TrackRecord,
} from "../lib/bets";

const FILTERS: Array<{ id: BetFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "settled", label: "Settled" },
];
const GROUPS: Array<{ id: GroupBy; label: string }> = [
  { id: "venue", label: "Book" },
  { id: "sport", label: "Sport" },
  { id: "market", label: "Market" },
];
const RESULT_LABELS: Record<Bet["status"], string> = {
  open: "Open", won: "Won", lost: "Lost", push: "Push", void: "Void",
};

const TIER_LABELS: Record<TrackRecord["tier"], string> = { targeted: "Targeted", watchlist: "Watchlist" };
const PICK_RESULTS = { won: "Won", lost: "Lost", push: "Push" };
// Real money waits for targeted picks to average this CLV over this many closes.
const GATE_CLV = 0.01;
const GATE_PICKS = 200;

const tone = (value: number | null) => (value === null || value === 0 ? "" : value > 0 ? "positive" : "negative");

function Stat({ label, value, note, amount }: { label: string; value: string; note?: string; amount?: number | null }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd className={tone(amount ?? null)}>{value}</dd>
      {note && <small>{note}</small>}
    </div>
  );
}

function Totals({ total }: { total: SummaryRow }) {
  return (
    <dl className="summary-cards">
      <Stat amount={total.profit} label="Profit after fees" value={formatMoney(total.profit)} />
      <Stat amount={total.roi} label="ROI" note={`on ${formatMoney(total.staked)} staked`} value={formatSigned(total.roi)} />
      <Stat label="Fees paid" value={formatMoney(total.fees)} />
      <Stat label="Bets" note={`${total.open} open`} value={String(total.bets)} />
      <Stat
        amount={total.avg_clv_points}
        label="Average CLV"
        note={`${total.clv_samples} bets with a close`}
        value={total.avg_clv_points === null ? "—" : `${formatPoints(Number(total.avg_clv_points.toFixed(2)))} pts`}
      />
      <Stat label="Beat the close" value={total.beat_close_pct === null ? "—" : `${total.beat_close_pct.toFixed(0)}%`} />
    </dl>
  );
}

function GroupTable({ rows, by }: { rows: SummaryRow[]; by: GroupBy }) {
  if (!rows.length) return null;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">{GROUPS.find((g) => g.id === by)?.label}</th>
            <th scope="col">Bets</th><th scope="col">Staked</th><th scope="col">Profit</th>
            <th scope="col">ROI</th><th scope="col">Avg CLV</th><th scope="col">Beat close</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{by === "venue" ? venueLabel(row.key) : row.key}</th>
              <td>{row.bets}</td>
              <td>{formatMoney(row.staked)}</td>
              <td className={tone(row.profit)}>{formatMoney(row.profit)}</td>
              <td className={tone(row.roi)}>{formatSigned(row.roi)}</td>
              <td>{row.avg_clv_points === null ? "—" : `${formatPoints(Number(row.avg_clv_points.toFixed(2)))} pts`}</td>
              <td>{row.beat_close_pct === null ? "—" : `${row.beat_close_pct.toFixed(0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function clvText(bet: Bet): string {
  if (bet.close_source === null) return "—";
  const parts = [];
  if (bet.clv_points !== null) parts.push(`${formatPoints(bet.clv_points)} pts`);
  if (bet.clv_prob !== null) parts.push(`${formatSigned(bet.clv_prob)} price`);
  return `${parts.join(" · ") || "—"} (${bet.close_source})`;
}

function BetRow({ bet, onVoid, busy }: { bet: Bet; onVoid: (id: number) => void; busy: boolean }) {
  const score = bet.game.home_score !== null && bet.game.away_score !== null
    ? ` · ${bet.game.away} ${bet.game.away_score}–${bet.game.home_score} ${bet.game.home}`
    : "";
  return (
    <tr>
      <th scope="row">
        <Link to={`/game/${bet.game.id}`}>{bet.game.away} @ {bet.game.home}</Link>
        <small>{bet.game.season_type} wk {bet.game.week ?? "—"}{score}</small>
      </th>
      <td>{describeBet(bet)}<small>{venueLabel(bet.venue)}</small></td>
      <td>{formatMoney(bet.stake)}<small>fee {formatMoney(bet.fee)}</small></td>
      <td>{RESULT_LABELS[bet.status]}</td>
      <td className={tone(bet.profit)}>{formatMoney(bet.profit)}</td>
      <td>{clvText(bet)}</td>
      <td>
        {bet.status === "open" && (
          <button
            aria-label={`Void ${describeBet(bet)}`}
            className="text-button"
            disabled={busy}
            onClick={() => {
              // A voided bet cannot be reopened, so ask first.
              if (window.confirm(`Void ${describeBet(bet)} (${formatMoney(bet.stake)})? This cannot be undone.`)) onVoid(bet.id);
            }}
            type="button"
          >
            Void
          </button>
        )}
      </td>
    </tr>
  );
}

function gateText(targeted: TrackRecord | undefined): string {
  if (!targeted || targeted.avg_clv === null) return `Gate: CLV of +1% or better over ${GATE_PICKS} targeted picks. No closes yet.`;
  const met = targeted.clv_samples >= GATE_PICKS && targeted.avg_clv >= GATE_CLV;
  return `Gate ${met ? "met" : "not met"}: ${formatSigned(targeted.avg_clv)} CLV over ${targeted.clv_samples} of ${GATE_PICKS} targeted picks.`;
}

function ModelPicksSection({ data }: { data: ModelPicks }) {
  return (
    <GlassCard className="slate-card">
      <div className="control-band" aria-label="Model picks">
        <h3>Model picks</h3>
      </div>
      <div className="table-scroll">
        <table aria-label="Model pick record">
          <thead>
            <tr>
              <th scope="col">Tier</th><th scope="col">Picks</th><th scope="col">W-L-P</th>
              <th scope="col">Flat ROI</th><th scope="col">Avg CLV</th><th scope="col">Beat close</th>
            </tr>
          </thead>
          <tbody>
            {data.record.map((row) => (
              <tr key={row.tier}>
                <th scope="row">{TIER_LABELS[row.tier]}</th>
                <td>{row.picks}</td>
                <td>{row.won}-{row.lost}-{row.push}</td>
                <td className={tone(row.flat_roi)}>{formatSigned(row.flat_roi)}</td>
                <td className={tone(row.avg_clv)}>{formatSigned(row.avg_clv)}<small>{row.clv_samples} closes</small></td>
                <td>{row.beat_close_pct === null ? "�" : `${row.beat_close_pct.toFixed(0)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="prediction-disclosure">
        {gateText(data.record.find((row) => row.tier === "targeted"))} Targeted: NBA moneylines over 3% EV after
        fees at the 10:15 CT price. Watchlist: spreads, unproven, tracking only. Flat ROI is one unit a pick,
        before fees.
      </p>
      {data.picks.length === 0 ? (
        <div className="notice info-notice" role="status">No model picks yet.</div>
      ) : (
        <div className="table-scroll">
          <table aria-label="Model picks">
            <thead>
              <tr>
                <th scope="col">Game</th><th scope="col">Pick</th><th scope="col">Model</th><th scope="col">EV</th>
                <th scope="col">Kelly</th><th scope="col">Result</th><th scope="col">CLV</th>
              </tr>
            </thead>
            <tbody>
              {data.picks.map((pick) => (
                <tr key={pick.id}>
                  <th scope="row">
                    <Link to={`/game/${pick.game.id}`}>{pick.game.away} @ {pick.game.home}</Link>
                    <small>{pick.slate_date} � {TIER_LABELS[pick.tier]}</small>
                  </th>
                  <td>{describePick(pick)}<small>{venueLabel(pick.book)}</small></td>
                  <td>{(pick.model_prob * 100).toFixed(1)}%<small>market {pick.market_prob_novig === null ? "�" : `${(pick.market_prob_novig * 100).toFixed(1)}%`}</small></td>
                  <td className={tone(pick.ev)}>{formatSigned(pick.ev)}</td>
                  <td>{(pick.kelly_frac * 100).toFixed(1)}%</td>
                  <td>{pick.result === null ? "Open" : PICK_RESULTS[pick.result]}</td>
                  <td className={tone(pick.clv)}>
                    {formatSigned(pick.clv)}
                    {pick.clv_points !== null && <small>{formatPoints(pick.clv_points)} pts</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </GlassCard>
  );
}

export function BetsPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<BetFilter>("all");
  const [groupBy, setGroupBy] = useState<GroupBy>("venue");
  const bets = useQuery({ queryKey: ["bets", filter], queryFn: ({ signal }) => fetchBets(filter, signal) });
  const summary = useQuery({ queryKey: ["bets-summary", groupBy], queryFn: ({ signal }) => fetchSummary(groupBy, signal) });
  const modelPicks = useQuery({ queryKey: ["model-picks"], queryFn: ({ signal }) => fetchModelPicks(signal) });
  const voiding = useMutation({
    mutationFn: voidBet,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["bets"] });
      void queryClient.invalidateQueries({ queryKey: ["bets-summary"] });
    },
  });
  const error = bets.error ?? summary.error ?? voiding.error ?? modelPicks.error;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Bet log</p>
          <h1>Bets</h1>
        </div>
        <nav className="app-nav" aria-label="Sections">
          <Link to="/nfl">NFL</Link> · <Link to="/nba">NBA</Link> · <Link to="/odds">Odds board</Link> · <Link to="/ops">System health</Link>
        </nav>
      </header>

      {error && <div className="notice error-notice" role="alert">{error.message}</div>}
      {summary.data && <Totals total={summary.data.total} />}

      <GlassCard className="slate-card">
        <div className="control-band" aria-label="Breakdown">
          <h3>Breakdown</h3>
          <div className="segmented-control" role="group" aria-label="Group by">
            {GROUPS.map((entry) => (
              <button
                aria-pressed={groupBy === entry.id}
                className={groupBy === entry.id ? "active" : ""}
                key={entry.id}
                onClick={() => setGroupBy(entry.id)}
                type="button"
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>
        {summary.data && <GroupTable by={groupBy} rows={summary.data.groups} />}
      </GlassCard>

      <section className="control-band" aria-label="Bet list filter">
        <div className="segmented-control" role="group" aria-label="Show bets">
          {FILTERS.map((entry) => (
            <button
              aria-pressed={filter === entry.id}
              className={filter === entry.id ? "active" : ""}
              key={entry.id}
              onClick={() => setFilter(entry.id)}
              type="button"
            >
              {entry.label}
            </button>
          ))}
        </div>
      </section>

      {bets.isLoading && <div className="skeleton-card" aria-label="Loading bets"><span /><span /><span /></div>}
      {bets.data && bets.data.length === 0 && (
        <div className="notice info-notice" role="status">
          No bets here yet. Open a game, pick a price on its Lines tab and log it.
        </div>
      )}
      {bets.data && bets.data.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Game</th><th scope="col">Bet</th><th scope="col">Stake</th>
                <th scope="col">Result</th><th scope="col">Profit</th><th scope="col">CLV</th><th scope="col" />
              </tr>
            </thead>
            <tbody>
              {bets.data.map((bet) => <BetRow bet={bet} busy={voiding.isPending} key={bet.id} onVoid={(id) => voiding.mutate(id)} />)}
            </tbody>
          </table>
        </div>
      )}
      {modelPicks.data && <ModelPicksSection data={modelPicks.data} />}
      <p className="prediction-disclosure">
        Profit is after the fee each book charged. CLV compares your number and price with the closing
        line: the pre-kickoff Odds API consensus, else ESPN's close. Context, not betting advice.
      </p>
    </main>
  );
}
