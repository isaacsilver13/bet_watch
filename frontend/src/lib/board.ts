import { errorDetail } from "./http";
import type { BetDraft, Market, Selection } from "./bets";
import type { BestView, BookLine, ConsensusView } from "./lines";
import { formatLine, formatPrice } from "./lines";
import type { SlateGame, WeekRef } from "./slate";

export type MarketId = Market;
export const MARKETS: { id: MarketId; label: string }[] = [
  { id: "spread", label: "Spread" },
  { id: "total", label: "Total" },
  { id: "moneyline", label: "Moneyline" },
];
export const DEFAULT_MARKET: MarketId = "spread";

export interface Coverage { quoting: number; at_line: number }
export interface BookRef { book: string; venue: string | null }
export interface BoardMovement { first_at: string; spread_home: number | null; total: number | null; snapshots: number }

/** What the price tables need; both the week board and the game page's lines response carry it. */
export interface PriceData {
  books: BookLine[];
  best: Record<string, BestView>;
  consensus: ConsensusView | null;
  coverage?: Record<string, Coverage>;
}

export interface BoardMarketData extends PriceData {
  snapshot_at: string | null;
  snapshot_books: BookRef[];
  coverage: Record<string, Coverage>;
  movement: BoardMovement | null;
}

export interface ContextFlag { kind: "qb" | "short_week" | "off_bye"; team: string; text: string; as_of: string | null }
export interface BoardGame { game: SlateGame; market: BoardMarketData; flags: ContextFlag[] }

export interface BoardResponse {
  sport: string;
  season: number;
  season_type: string;
  week: number;
  weeks: WeekRef[];
  snapshot_at: string | null;
  books_available: BookRef[];
  books_selected: string[];
  can_refresh: boolean;
  games: BoardGame[];
}

export interface BoardRequest {
  season?: string;
  seasonType?: string;
  week?: string;
  books?: string[];
  signal?: AbortSignal;
}

export async function fetchBoard({ season, seasonType, week, books, signal }: BoardRequest = {}): Promise<BoardResponse> {
  const params = new URLSearchParams({ sport: "nfl" });
  if (season && week) {
    params.set("season", season);
    params.set("week", week);
    params.set("season_type", (seasonType ?? "reg").toUpperCase());
  }
  if (books?.length) params.set("books", books.join(","));
  const response = await fetch(`/api/v1/board?${params.toString()}`, { signal });
  if (!response.ok) throw new Error(await errorDetail(response, `Board request failed with status ${response.status}`));
  return response.json() as Promise<BoardResponse>;
}

/** One side of a market, in the order the board shows them (away first, over first). */
export interface SideSpec {
  name: "spread_away" | "spread_home" | "over" | "under" | "ml_away" | "ml_home";
  market: MarketId;
  selection: Selection;
}

export const MARKET_SIDES: Record<MarketId, [SideSpec, SideSpec]> = {
  spread: [
    { name: "spread_away", market: "spread", selection: "away" },
    { name: "spread_home", market: "spread", selection: "home" },
  ],
  total: [
    { name: "over", market: "total", selection: "over" },
    { name: "under", market: "total", selection: "under" },
  ],
  moneyline: [
    { name: "ml_away", market: "moneyline", selection: "away" },
    { name: "ml_home", market: "moneyline", selection: "home" },
  ],
};

/** The number all books are compared at for this side, from that side's point of view. */
export function comparisonLine(spec: SideSpec, consensus: ConsensusView | null): number | null {
  if (!consensus) return null;
  if (spec.market === "total") return consensus.total;
  if (spec.market === "moneyline") return null;
  if (consensus.spread_home === null) return null;
  return spec.selection === "home" ? consensus.spread_home : -consensus.spread_home;
}

export function sideLabel(spec: SideSpec, game: Pick<SlateGame, "home" | "away">, line: number | null): string {
  if (spec.market === "total") return `${spec.selection === "over" ? "Over" : "Under"} ${line ?? "—"}`;
  const team = spec.selection === "home" ? game.home.abbr : game.away.abbr;
  return spec.market === "moneyline" ? `${team} ML` : `${team} ${formatLine(line)}`;
}

export interface QuoteCell {
  book: string;
  venue: string | null;
  observedAt: string;
  line: number | null;
  price: number | null;
  /** Same market, side and number as the consensus line, so its price can be ranked against the others. */
  comparable: boolean;
}

export function quoteCell(book: BookLine, spec: SideSpec, consensus: ConsensusView | null): QuoteCell {
  let line: number | null = null;
  let price: number | null = null;
  if (spec.market === "spread") {
    line = book.spread_home === null ? null : spec.selection === "home" ? book.spread_home : -book.spread_home;
    price = spec.selection === "home" ? book.spread_home_price : book.spread_away_price;
  } else if (spec.market === "total") {
    line = book.total;
    price = spec.selection === "over" ? book.over_price : book.under_price;
  } else {
    price = spec.selection === "home" ? book.ml_home : book.ml_away;
  }
  const wanted = comparisonLine(spec, consensus);
  const comparable = price !== null && (spec.market === "moneyline" || (wanted !== null && line === wanted));
  return { book: book.book, venue: book.venue, observedAt: book.observed_at, line, price, comparable };
}

/** The best entry for a side is only ever a quote at the comparison line. */
export function isBestQuote(cell: QuoteCell, best: BestView | undefined): boolean {
  return Boolean(best) && cell.comparable && cell.book === best!.book && cell.price === best!.price;
}

export function quoteDraft(cell: QuoteCell, spec: SideSpec, actionable: boolean): BetDraft {
  return {
    venue: cell.venue ?? "other",
    market: spec.market,
    selection: spec.selection,
    line: cell.line,
    price: cell.price,
    quote: { book: cell.book, observed_at: cell.observedAt, actionable },
  };
}

export const quoteText = (line: number | null, price: number | null, market: MarketId): string =>
  market === "moneyline" ? formatPrice(price) : `${market === "total" ? (line ?? "—") : formatLine(line)} ${formatPrice(price)}`;

export const STALE_AFTER_HOURS = 24;

export function isStale(snapshotAt: string | null | undefined, nowMs = Date.now()): boolean {
  return Boolean(snapshotAt) && nowMs - Date.parse(snapshotAt!) > STALE_AFTER_HOURS * 3_600_000;
}

/** Only a game that has not started can be bet at a price we saved. */
export function isActionable(game: Pick<SlateGame, "status" | "start_time">, nowMs = Date.now()): boolean {
  return game.status === "scheduled" && Date.parse(game.start_time) > nowMs;
}

/** "Thu 7:15 PM CDT": local time with its zone, so a kickoff is never ambiguous. */
export function whenZoned(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
}

export function ageText(iso: string, nowMs = Date.now()): string {
  const hours = Math.max(0, Math.round((nowMs - Date.parse(iso)) / 3_600_000));
  if (hours < 1) return "under an hour old";
  return hours < 48 ? `${hours} hour${hours === 1 ? "" : "s"} old` : `${Math.round(hours / 24)} days old`;
}

/** Local calendar day of kickoff, the board's day separator. */
export function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA");
}

export function dayHeading(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

export function parseMarket(value: string | null): MarketId {
  return MARKETS.some((market) => market.id === value) ? (value as MarketId) : DEFAULT_MARKET;
}

export function weekLabel(ref: WeekRef): string {
  if (ref.season_type === "POST") return `Playoffs round ${ref.week}`;
  return ref.season_type === "PRE" ? `Preseason week ${ref.week}` : `Week ${ref.week}`;
}

export function weekPath(season: number, ref: WeekRef): string {
  return `/nfl/${season}/${ref.season_type.toLowerCase()}/${ref.week}`;
}
