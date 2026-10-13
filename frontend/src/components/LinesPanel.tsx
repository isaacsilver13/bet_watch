import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { BetForm } from "./BetForm";
import { LineChart } from "./LineChart";
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

const STALE_AFTER_HOURS = 24;

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);

function PriceButton({
  book, text, draft, onPick,
}: { book: BookLine; text: string; draft: Omit<BetDraft, "venue">; onPick: (draft: BetDraft) => void }) {
  return (
    <button
      aria-label={`Log ${book.book} ${text}`}
      className="price-button"
      onClick={() => onPick({ ...draft, venue: book.venue ?? "other" })}
      type="button"
    >
      {text}
    </button>
  );
}

function BookRow({ book, game, onPick }: { book: BookLine; game: SlateGame; onPick: (draft: BetDraft) => void }) {
  const { home, away } = game;
  const side = (market: Market, selection: Selection, line: number | null, price: number | null, text: string) =>
    price === null ? null : (
      <PriceButton book={book} draft={{ market, selection, line, price }} onPick={onPick} text={text} />
    );
  const spreadAway = book.spread_home === null ? null : -book.spread_home;
  return (
    <tr>
      <th scope="row">{book.book}</th>
      <td>
        {side("spread", "away", spreadAway, book.spread_away_price, `${away.abbr} ${formatLine(spreadAway)} ${formatPrice(book.spread_away_price)}`)}
        {side("spread", "home", book.spread_home, book.spread_home_price, `${home.abbr} ${formatLine(book.spread_home)} ${formatPrice(book.spread_home_price)}`)}
      </td>
      <td>
        {side("total", "over", book.total, book.over_price, `Over ${book.total} ${formatPrice(book.over_price)}`)}
        {side("total", "under", book.total, book.under_price, `Under ${book.total} ${formatPrice(book.under_price)}`)}
      </td>
      <td>
        {side("moneyline", "away", null, book.ml_away, `${away.abbr} ML ${formatPrice(book.ml_away)}`)}
        {side("moneyline", "home", null, book.ml_home, `${home.abbr} ML ${formatPrice(book.ml_home)}`)}
      </td>
    </tr>
  );
}

function bestText(name: string, best: BestView, game: SlateGame): string {
  const label: Record<string, string> = {
    spread_home: `${game.home.abbr} ${formatLine(best.line)}`,
    spread_away: `${game.away.abbr} ${formatLine(best.line)}`,
    over: `Over ${best.line}`,
    under: `Under ${best.line}`,
    ml_home: `${game.home.abbr} ML`,
    ml_away: `${game.away.abbr} ML`,
  };
  return `${label[name] ?? name} ${formatPrice(best.price)} (${best.book})`;
}

function Summary({ lines, game }: { lines: LinesResponse; game: SlateGame }) {
  const { consensus } = lines;
  if (!consensus) return null;
  const spread = consensus.spread_home === null ? "—" : `${game.home.abbr} ${formatLine(consensus.spread_home)}`;
  return (
    <>
      <p className="lines-consensus">
        Consensus of {consensus.books} books: {spread} · O/U {consensus.total ?? "—"} ·{" "}
        {game.home.abbr} win {percent(consensus.home_win_prob)} (vig removed)
      </p>
      <ul className="best-prices" aria-label="Best prices">
        {["spread_away", "spread_home", "over", "under", "ml_away", "ml_home"]
          .filter((name) => lines.best[name])
          .map((name) => <li key={name}>{bestText(name, lines.best[name], game)}</li>)}
      </ul>
    </>
  );
}

export function LinesPanel({ game }: { game: SlateGame }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<BetDraft | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const lines = useQuery({ queryKey: ["lines", game.id], queryFn: ({ signal }) => fetchLines(game.id, signal) });
  const eventId = lines.data?.event_id ?? null;
  const kalshi = useQuery({
    queryKey: ["kalshi", "nfl"],
    queryFn: ({ signal }) => fetchPredictionMarkets("nfl", signal),
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
          No sportsbook prices for this game yet. They arrive with the Tuesday, Thursday, Saturday and
          Sunday snapshots and shortly before kickoff{data.can_refresh ? ", or use Refresh prices" : ""}.
        </p>
      ) : (
        <>
          <Summary game={game} lines={data} />
          <div className="table-scroll">
            <table className="lines-table">
              <thead>
                <tr>
                  <th scope="col">Book</th><th scope="col">Spread</th><th scope="col">Total</th><th scope="col">Moneyline</th>
                </tr>
              </thead>
              <tbody>
                {data.books.map((book) => <BookRow book={book} game={game} key={book.book} onPick={pick} />)}
              </tbody>
            </table>
          </div>
        </>
      )}

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
