import type { BetDraft } from "../lib/bets";
import {
  MARKET_SIDES,
  comparisonLine,
  isBestQuote,
  quoteCell,
  quoteDraft,
  quoteText,
  sideLabel,
  whenZoned,
  type MarketId,
  type PriceData,
  type QuoteCell,
  type SideSpec,
} from "../lib/board";
import { formatPrice } from "../lib/lines";
import type { SlateGame } from "../lib/slate";

type Teams = Pick<SlateGame, "home" | "away">;
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

/** "3 of 5 books at +4.5": what the highlighted best price was actually ranked against. */
export function coverageText(data: PriceData, market: MarketId, game: Teams): string {
  const line = comparisonLine(MARKET_SIDES[market][0], data.consensus);
  const count = data.coverage?.[market];
  if (!count || count.quoting === 0) return "No book quotes this market yet.";
  if (market === "moneyline") return `${count.quoting} book${count.quoting === 1 ? "" : "s"} quoting.`;
  if (line === null) return "No common line to compare.";
  const where = market === "total" ? `${line}` : sideLabel(MARKET_SIDES[market][0], game, line);
  return `${count.at_line} of ${count.quoting} book${count.quoting === 1 ? "" : "s"} at ${where}.`;
}

interface PriceProps {
  cell: QuoteCell;
  spec: SideSpec;
  label: string;
  best: boolean;
  actionable: boolean;
  onPick?: (draft: BetDraft) => void;
}

/** A line and its price together. Clicking logs the quote in the bet journal; it never places a bet. */
export function QuotePrice({ cell, spec, label, best, actionable, onPick }: PriceProps) {
  const text = quoteText(cell.line, cell.price, spec.market);
  const className = ["price-button", best ? "best-price-cell" : "", cell.comparable ? "" : "other-number"].join(" ").trim();
  const content = (
    <>
      <span className="price-text">{text}</span>
      {best && <span className="price-tag">Best</span>}
      {!cell.comparable && cell.price !== null && <span className="price-tag muted">Other number</span>}
    </>
  );
  if (cell.price === null) return <span className="missing-price">—</span>;
  if (!onPick || !actionable) {
    return <span className={`${className} price-static`} title={`${cell.book} ${label} ${text}`}>{content}</span>;
  }
  return (
    <button
      aria-label={`Log ${cell.book} ${label} ${formatPrice(cell.price)}`}
      className={className}
      onClick={() => onPick(quoteDraft(cell, spec, actionable))}
      type="button"
    >
      {content}
    </button>
  );
}

interface Props {
  data: PriceData;
  game: Teams;
  market: MarketId;
  actionable: boolean;
  snapshotAt: string | null;
  onPick?: (draft: BetDraft) => void;
  /** Wording for the scope the best price was taken over, e.g. "Best among 3 selected books". */
  scope?: string;
}

/** Every book's quote for each side of one market, ranked only where the number is the same. */
export function BookComparison({ data, game, market, actionable, snapshotAt, onPick, scope }: Props) {
  const specs = MARKET_SIDES[market];
  const { consensus } = data;
  if (data.books.length === 0) return <p className="empty-grid">No sportsbook quotes for this game in the saved snapshot.</p>;

  return (
    <div className="book-comparison">
      <p className="comparison-note">
        {coverageText(data, market, game)} {scope ? `${scope} ` : ""}
        {market === "moneyline" ? "Moneylines compare the same team directly." : "Prices are ranked only at the same number; other numbers are shown, not ranked."}
      </p>
      <div className="table-scroll">
        <table className="lines-table book-table" aria-label={`${market} prices by book`}>
          <thead>
            <tr>
              <th className="sticky-selection" scope="col">Selection</th>
              {data.books.map((book) => (
                <th key={book.book} scope="col">
                  {book.book}
                  <small>{clock(book.observed_at)}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {specs.map((spec) => {
              const line = comparisonLine(spec, consensus);
              const label = sideLabel(spec, game, line);
              const best = data.best[spec.name];
              return (
                <tr key={spec.name}>
                  <th className="sticky-selection" scope="row">
                    {label}
                    {best && <small>Best {formatPrice(best.price)} · {best.book}</small>}
                  </th>
                  {data.books.map((book) => {
                    const cell = quoteCell(book, spec, consensus);
                    const rowLabel = sideLabel(spec, game, cell.line);
                    return (
                      <td key={book.book}>
                        <QuotePrice
                          actionable={actionable}
                          best={isBestQuote(cell, best)}
                          cell={cell}
                          label={rowLabel}
                          onPick={onPick}
                          spec={spec}
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {snapshotAt && <p className="comparison-note">Snapshot {whenZoned(snapshotAt)}. Click a price to log it in your bet journal.</p>}
    </div>
  );
}
