import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { BetForm } from "./BetForm";
import { BookComparison, QuotePrice, coverageText } from "./BookComparison";
import { ResearchBrief } from "./ResearchBrief";
import type { BetDraft } from "../lib/bets";
import {
  MARKET_SIDES,
  dayHeading,
  dayKey,
  isActionable,
  isBestQuote,
  isStale,
  quoteCell,
  sideLabel,
  whenZoned,
  type BoardGame,
  type MarketId,
  type QuoteCell,
} from "../lib/board";
import { fetchContext } from "../lib/matchup";
import { collapsedFlags } from "../lib/research";
import { formatSpread, type SlateGame } from "../lib/slate";

export type Panel = "books" | "research";
export interface Expanded { game: number; panel: Panel }

const STATUS_LABELS: Record<string, string> = {
  in_progress: "Live", final: "Final", postponed: "Postponed", canceled: "Canceled",
};

function statusText(game: SlateGame): string {
  const label = STATUS_LABELS[game.status];
  if (!label) return whenZoned(game.start_time);
  const score = game.home_score !== null && game.away_score !== null
    ? ` · ${game.away.abbr} ${game.away_score}–${game.home_score} ${game.home.abbr}` : "";
  return `${label}${score}`;
}

const percent = (value: number | null | undefined) => (value == null ? "—" : `${(value * 100).toFixed(1)}%`);

/** Reference lines and book consensus stay in separate labelled lanes: they are different sources. */
function movementLanes(entry: BoardGame, market: MarketId): { label: string; text: string }[] {
  const { game, market: data } = entry;
  const open = game.lines.open;
  const latest = game.lines.close ?? game.lines.current;
  const lanes: { label: string; text: string }[] = [];
  if (market === "moneyline") {
    const prob = data.consensus?.home_win_prob;
    return [{ label: "Market estimate (vig removed)", text: prob == null ? "—" : `${game.home.abbr} win ${percent(prob)}` }];
  }
  const show = (spread: number | null | undefined, total: number | null | undefined) =>
    market === "spread" ? formatSpread(game, spread) : total ?? "—";
  if (open && latest) {
    lanes.push({ label: `Reference · ${latest.source}`, text: `${show(open.spread_home, open.total)} → ${show(latest.spread_home, latest.total)}` });
  } else {
    lanes.push({ label: "Reference", text: "No open line loaded" });
  }
  const first = data.movement;
  if (first && data.consensus && first.snapshots > 1) {
    lanes.push({ label: "Book consensus", text: `${show(first.spread_home, first.total)} → ${show(data.consensus.spread_home, data.consensus.total)}` });
  }
  return lanes;
}

function BestSide({
  entry, market, index, actionable, onPick,
}: { entry: BoardGame; market: MarketId; index: 0 | 1; actionable: boolean; onPick: (draft: BetDraft) => void }) {
  const spec = MARKET_SIDES[market][index];
  const best = entry.market.best[spec.name];
  const label = sideLabel(spec, entry.game, best?.line ?? null);
  if (!best) {
    return (
      <div className="best-side">
        <span className="side-name">{sideLabel(spec, entry.game, null)}</span>
        <span className="missing-price">{entry.market.books.length ? "No common line" : "—"}</span>
      </div>
    );
  }
  const book = entry.market.books.find((b) => b.book === best.book);
  const observedAt = book?.observed_at ?? entry.market.snapshot_at ?? "";
  const cell: QuoteCell = {
    book: best.book, venue: best.venue, observedAt, line: best.line, price: best.price, comparable: true,
  };
  return (
    <div className="best-side">
      <span className="side-name">{label}</span>
      <QuotePrice actionable={actionable} best cell={cell} label={label} onPick={onPick} spec={spec} />
      <small>{best.book}{observedAt ? ` · ${new Date(observedAt).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : ""}</small>
    </div>
  );
}

function BookStrip({
  entry, market, actionable, onPick,
}: { entry: BoardGame; market: MarketId; actionable: boolean; onPick: (draft: BetDraft) => void }) {
  const specs = MARKET_SIDES[market];
  return (
    <div className="book-strip" role="group" aria-label="Prices by book">
      {entry.market.books.map((book) => (
        <div className="book-column" key={book.book}>
          <span className="book-name">{book.book}</span>
          {specs.map((spec) => {
            const cell = quoteCell(book, spec, entry.market.consensus);
            return (
              <QuotePrice
                actionable={actionable} best={isBestQuote(cell, entry.market.best[spec.name])} cell={cell}
                key={spec.name} label={sideLabel(spec, entry.game, cell.line)} onPick={onPick} spec={spec}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

function ResearchPanel({ entry }: { entry: BoardGame }) {
  const id = String(entry.game.id);
  const context = useQuery({ queryKey: ["context", id, 5], queryFn: ({ signal }) => fetchContext(id, 5, signal) });
  return (
    <ResearchBrief
      bookMove={entry.market.movement}
      bookNow={entry.market.consensus}
      context={context.data ?? null}
      contextError={context.error instanceof Error ? context.error : null}
      contextLoading={context.isLoading}
      game={entry.game}
    />
  );
}

interface RowProps {
  entry: BoardGame;
  market: MarketId;
  expanded: Expanded | null;
  onToggle: (game: number, panel: Panel) => void;
  snapshotStale: boolean;
}

function GameMarketRow({ entry, market, expanded, onToggle, snapshotStale }: RowProps) {
  const { game } = entry;
  const location = useLocation();
  const [draft, setDraft] = useState<BetDraft | null>(null);
  const actionable = isActionable(game) && entry.market.books.length > 0;
  const open = expanded?.game === game.id ? expanded.panel : null;
  const flags = collapsedFlags(game, entry.flags);
  const coverage = coverageText(entry.market, market, game);
  const started = !isActionable(game);

  return (
    <article className="game-row" data-status={game.status}>
      <div className="game-identity">
        <h3>
          <Link state={{ from: location.pathname + location.search }} to={`/game/${game.id}`}>
            {game.away.abbr} @ {game.home.abbr}
          </Link>
        </h3>
        <p className="game-time">{statusText(game)}</p>
        <p className="slate-venue">{game.venue ?? "Venue TBD"}{game.neutral ? " · neutral site" : ""}</p>
      </div>

      <div className="game-move">
        {movementLanes(entry, market).map((lane) => (
          <p key={lane.label}><span className="lane-label">{lane.label}</span><span className="lane-value">{lane.text}</span></p>
        ))}
      </div>

      <div className="game-best" aria-label={snapshotStale ? "Best in saved snapshot" : "Best price"}>
        <span className="lane-label">{snapshotStale ? "Best in saved snapshot" : "Best price"}</span>
        <BestSide actionable={actionable} entry={entry} index={0} market={market} onPick={setDraft} />
        <BestSide actionable={actionable} entry={entry} index={1} market={market} onPick={setDraft} />
        <small className="coverage">{entry.market.books.length ? coverage : "No sportsbook snapshot for this game yet."}</small>
        {started && entry.market.books.length > 0 && <small className="coverage">Saved pregame prices, not current.</small>}
      </div>

      <BookStrip actionable={actionable} entry={entry} market={market} onPick={setDraft} />

      {flags.length > 0 && (
        <ul className="game-flags" aria-label="Research flags">
          {flags.map((flag) => <li className={`flag flag-${flag.kind}`} key={flag.key}>{flag.text}</li>)}
        </ul>
      )}

      <div className="game-actions">
        <button aria-expanded={open === "books"} className="text-button" onClick={() => onToggle(game.id, "books")} type="button">
          Compare books
        </button>
        <button aria-expanded={open === "research"} className="text-button" onClick={() => onToggle(game.id, "research")} type="button">
          Research matchup
        </button>
      </div>

      {draft && <BetForm draft={draft} game={game} key={JSON.stringify(draft)} onClose={() => setDraft(null)} />}
      {open === "books" && (
        <div className="game-panel">
          <BookComparison
            actionable={actionable} data={entry.market} game={game} market={market}
            onPick={setDraft} snapshotAt={entry.market.snapshot_at}
          />
        </div>
      )}
      {open === "research" && (
        <div className="game-panel">
          <ResearchPanel entry={entry} />
          <p><Link state={{ from: location.pathname + location.search }} to={`/game/${game.id}`}>Open full matchup</Link></p>
        </div>
      )}
    </article>
  );
}

export function NflBoard({
  games, market, expanded, onToggle,
}: { games: BoardGame[]; market: MarketId; expanded: Expanded | null; onToggle: (game: number, panel: Panel) => void }) {
  const days: { key: string; heading: string; games: BoardGame[] }[] = [];
  for (const entry of games) {
    const key = dayKey(entry.game.start_time);
    const last = days[days.length - 1];
    if (last?.key === key) last.games.push(entry);
    else days.push({ key, heading: dayHeading(entry.game.start_time), games: [entry] });
  }
  return (
    <div className="nfl-board" aria-live="polite">
      {days.map((day) => (
        <section className="game-day" key={day.key} aria-label={day.heading}>
          <h2 className="day-heading">{day.heading}</h2>
          {day.games.map((entry) => (
            <GameMarketRow
              entry={entry} expanded={expanded} key={entry.game.id} market={market} onToggle={onToggle}
              snapshotStale={isStale(entry.market.snapshot_at)}
            />
          ))}
        </section>
      ))}
    </div>
  );
}
