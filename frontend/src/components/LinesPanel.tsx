import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { BetForm } from "./BetForm";
import { LineChart } from "./LineChart";
import { MarketContext } from "./MarketContext";
import { PredictionPanel } from "./PredictionMarkets";
import { fetchPredictionMarkets } from "../lib/api";
import type { BetDraft, Market, Selection } from "../lib/bets";
import {
  REFRESH_REASONS,
  fetchLines,
  formatLine,
  formatPrice,
  refreshGame,
  type BestView,
  type BookLine,
  type LinesResponse,
} from "../lib/lines";
import type { SlateGame } from "../lib/slate";
import type { ContextResponse } from "../lib/matchup";

const STALE_AFTER_HOURS = 24;

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);

function PriceButton({
  book, venue, label, price, draft, onPick,
}: { book: string; venue: string | null; label: string; price: number; draft: Omit<BetDraft, "venue">; onPick: (draft: BetDraft) => void }) {
  return (
    <button
      aria-label={`Log ${book} ${label} ${formatPrice(price)}`}
      className="price-button"
      onClick={() => onPick({ ...draft, venue: venue ?? "other" })}
      type="button"
    >
      {formatPrice(price)}
    </button>
  );
}

function sideLabel(name: string, line: number | null, game: SlateGame): string {
  const label: Record<string, string> = {
    spread_home: `${game.home.abbr} ${formatLine(line)}`,
    spread_away: `${game.away.abbr} ${formatLine(line)}`,
    over: `Over ${line ?? "—"}`,
    under: `Under ${line ?? "—"}`,
    ml_home: `${game.home.abbr} ML`,
    ml_away: `${game.away.abbr} ML`,
  };
  return label[name] ?? name;
}

const SIDES: { name: string; market: Market; selection: Selection }[] = [
  { name: "spread_away", market: "spread", selection: "away" },
  { name: "spread_home", market: "spread", selection: "home" },
  { name: "over", market: "total", selection: "over" },
  { name: "under", market: "total", selection: "under" },
  { name: "ml_away", market: "moneyline", selection: "away" },
  { name: "ml_home", market: "moneyline", selection: "home" },
];

function fanduelPrice(book: BookLine | undefined, name: string, line: number | null): number | null {
  if (!book) return null;
  if (name !== "ml_home" && name !== "ml_away" && line === null) return null;
  if (name === "spread_home") return book.spread_home === line ? book.spread_home_price : null;
  if (name === "spread_away") return book.spread_home === (line === null ? null : -line) ? book.spread_away_price : null;
  if (name === "over") return book.total === line ? book.over_price : null;
  if (name === "under") return book.total === line ? book.under_price : null;
  if (name === "ml_home") return book.ml_home;
  return book.ml_away;
}

function PriceComparison({ lines, game, onPick }: { lines: LinesResponse; game: SlateGame; onPick: (draft: BetDraft) => void }) {
  const { consensus } = lines;
  if (!consensus) return null;
  const spread = consensus.spread_home === null ? "—" : `${game.home.abbr} ${formatLine(consensus.spread_home)}`;
  const fanduel = lines.books.find((book) => book.venue === "fanduel");
  return (
    <>
      <p className="lines-consensus">
        Consensus of {consensus.books} books: {spread} · O/U {consensus.total ?? "—"} ·{" "}
        {game.home.abbr} win {percent(consensus.home_win_prob)} (vig removed)
      </p>
      <p className="price-comparison-note">Spread and total prices are compared at the same consensus line. Click a price to log a bet.</p>
      <div className="table-scroll">
        <table className="lines-table" aria-label="Best and FanDuel prices">
          <thead><tr><th scope="col">Selection</th><th scope="col">Best tracked book</th><th scope="col">FanDuel at this line</th></tr></thead>
          <tbody>
            {SIDES.map(({ name, market, selection }) => {
              const best: BestView | undefined = lines.best[name];
              const line = best?.line ?? (market === "spread"
                ? (consensus.spread_home === null ? null : selection === "home" ? consensus.spread_home : -consensus.spread_home)
                : market === "total" ? consensus.total : null);
              const label = sideLabel(name, line, game);
              const fdPrice = fanduelPrice(fanduel, name, line);
              const bestBook = best && lines.books.find((book) => book.book === best.book);
              return (
                <tr key={name}>
                  <th scope="row">{label}</th>
                  <td>
                    {best ? (
                      <>
                        <PriceButton book={best.book} venue={best.venue} label={label} price={best.price}
                          draft={{ market, selection, line, price: best.price }} onPick={onPick} />
                        <small>{best.book} · {bestBook ? when(bestBook.observed_at) : "Time unavailable"}</small>
                      </>
                    ) : <span className="missing-price">No tracked quote at this line</span>}
                  </td>
                  <td>
                    {fdPrice === null ? <span className="missing-price">No quote at this line</span> : (
                      <PriceButton book="FanDuel" venue="fanduel" label={label} price={fdPrice}
                        draft={{ market, selection, line, price: fdPrice }} onPick={onPick} />
                    )}
                    {fanduel && fdPrice !== null && <small>FanDuel · {when(fanduel.observed_at)}</small>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function LinesPanel({
  game, context, contextLoading = false, contextError = null,
}: { game: SlateGame; context?: ContextResponse | null; contextLoading?: boolean; contextError?: Error | null }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<BetDraft | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const lines = useQuery({ queryKey: ["lines", game.id], queryFn: ({ signal }) => fetchLines(game.id, signal) });
  const eventId = lines.data?.event_id ?? null;
  const sport = game.sport === "nba" ? "nba" : "nfl";
  const kalshi = useQuery({
    queryKey: ["kalshi", sport],
    queryFn: ({ signal }) => fetchPredictionMarkets(sport, signal),
    enabled: eventId !== null,
  });
  const refresh = useMutation({
    mutationFn: () => refreshGame(game.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["lines", game.id] }),
  });

  if (lines.isLoading) return <div className="skeleton-card" aria-label="Loading prices"><span /><span /><span /></div>;
  if (lines.error) return <div className="notice error-notice" role="alert">{lines.error.message}</div>;
  const data = lines.data!;
  const stale = data.snapshot_at !== null && Date.now() - Date.parse(data.snapshot_at) > STALE_AFTER_HOURS * 3_600_000;
  const outcome = refresh.data;
  const contracts = (kalshi.data?.contracts ?? []).filter((contract) => contract.event_id === eventId);
  const pick = (next: BetDraft) => { setDraft(next); setFormOpen(true); };

  return (
    <div className="lines-panel">
      <div className="odds-toolbar">
        <p className={stale ? "freshness stale" : "freshness"}>
          {data.snapshot_at ? `Prices as of ${when(data.snapshot_at)}${stale ? " · stale" : ""}` : "No sportsbook prices yet"}
          {data.quota_remaining !== null ? ` · ${data.quota_remaining} credits left` : ""}
        </p>
        <div className="bet-actions">
          {data.can_refresh && (
            <button className="refresh-button" disabled={refresh.isPending} onClick={() => refresh.mutate()} type="button">
              {refresh.isPending ? "Refreshing…" : "Refresh prices (3 credits)"}
            </button>
          )}
          <button className="text-button" onClick={() => { setDraft(null); setFormOpen(true); }} type="button">Log a bet</button>
        </div>
      </div>
      {refresh.error && <div className="notice error-notice" role="alert">{refresh.error.message}</div>}
      {outcome && (
        <div className="notice info-notice" role="status">
          {outcome.refreshed
            ? `Prices refreshed${outcome.state === "partial" ? " (some books are still missing markets)" : ""}.`
            : REFRESH_REASONS[outcome.reason ?? ""] ?? "Nothing was refreshed."}
        </div>
      )}

      {formOpen && <BetForm draft={draft} game={game} key={JSON.stringify(draft)} onClose={() => setFormOpen(false)} />}

      {data.books.length === 0 ? (
        <p className="empty-grid">
          No sportsbook prices for this game yet. They arrive with{" "}
          {game.sport === "nba"
            ? "the 10am snapshot and shortly before the first and last tips of the night"
            : "the Tuesday, Thursday, Saturday and Sunday snapshots and shortly before kickoff"}
          {data.can_refresh ? ", or use Refresh prices" : ""}.
        </p>
      ) : (
        <PriceComparison game={game} lines={data} onPick={pick} />
      )}

      {context !== undefined && <MarketContext context={context} error={contextError} loading={contextLoading} />}

      <div className="chart-row">
        <LineChart
          format={(value) => formatLine(value)}
          label={`${game.home.abbr} spread`}
          points={data.history.filter((p) => p.spread_home !== null).map((p) => ({ at: p.observed_at, value: p.spread_home! }))}
        />
        <LineChart
          label="Total"
          points={data.history.filter((p) => p.total !== null).map((p) => ({ at: p.observed_at, value: p.total! }))}
        />
      </div>

      <dl className="reference-lines">
        {(["open", "current", "close"] as const).filter((kind) => data.reference[kind]).map((kind) => {
          const line = data.reference[kind]!;
          return (
            <div key={kind}>
              <dt>{kind}</dt>
              <dd>{line.spread_home === null ? "—" : `${game.home.abbr} ${formatLine(line.spread_home)}`} · {line.total ?? "—"} <small>({line.source})</small></dd>
            </div>
          );
        })}
      </dl>

      <PredictionPanel contracts={contracts} details={kalshi.data?.provider_details?.kalshi} heading="Kalshi" />
    </div>
  );
}
