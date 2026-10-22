import { errorDetail } from "./http";
import { formatLine, formatPrice } from "./lines";

export type Market = "spread" | "total" | "moneyline";
export type Selection = "home" | "away" | "over" | "under";
export const VENUES: Array<{ id: string; label: string }> = [
  { id: "draftkings", label: "DraftKings" },
  { id: "fanduel", label: "FanDuel" },
  { id: "betmgm", label: "BetMGM" },
  { id: "betrivers", label: "BetRivers" },
  { id: "espnbet", label: "theScore Bet" },
  { id: "other", label: "Other" },
];

/** What a clicked price pre-fills in the bet form. */
export interface BetDraft {
  venue: string;
  market: Market;
  selection: Selection;
  line: number | null;
  price: number | null;
}

export interface BetGame {
  id: number;
  away: string;
  home: string;
  season_type: string;
  week: number | null;
  start_time: string;
  status: string;
  away_score: number | null;
  home_score: number | null;
}

export interface Bet {
  id: number;
  game: BetGame;
  placed_at: string;
  venue: string;
  market: Market;
  selection: Selection;
  line: number | null;
  price_american: number;
  stake: number;
  fee: number;
  status: "open" | "won" | "lost" | "push" | "void";
  payout: number | null;
  profit: number | null;
  settled_at: string | null;
  close_line: number | null;
  close_price: number | null;
  close_source: string | null;
  clv_points: number | null;
  clv_prob: number | null;
  clv_pct: number | null;
  note: string | null;
}

export interface NewBet {
  game_id: number;
  venue: string;
  market: Market;
  selection: Selection;
  line: number | null;
  price_american: number;
  stake: number;
  note?: string;
}

export interface SummaryRow {
  key: string;
  bets: number;
  open: number;
  settled: number;
  staked: number;
  fees: number;
  profit: number;
  roi: number | null;
  avg_clv_points: number | null;
  avg_clv_prob: number | null;
  clv_samples: number;
  beat_close_pct: number | null;
}

export interface Summary { group_by: string; total: SummaryRow; groups: SummaryRow[] }
export type BetFilter = "all" | "open" | "settled";
export type GroupBy = "venue" | "sport" | "market";

/** "DEN -2.5 -110", "Over 44.5 -105", "SEA ML +150". */
export function describeBet(bet: Bet): string {
  const side = bet.selection === "home" ? bet.game.home : bet.selection === "away" ? bet.game.away : bet.selection;
  const label = bet.selection === "over" ? "Over" : bet.selection === "under" ? "Under" : side;
  const number = bet.market === "total" ? ` ${bet.line}` : bet.market === "spread" ? ` ${formatLine(bet.line)}` : " ML";
  return `${label}${number} ${formatPrice(bet.price_american)}`;
}

export const venueLabel = (id: string): string => VENUES.find((v) => v.id === id)?.label ?? id;

export function formatMoney(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const text = `$${Math.abs(amount).toFixed(2)}`;
  return amount < 0 ? `-${text}` : text;
}

/** A fraction as a signed percentage: 0.0649 -> "+6.5%". */
export function formatSigned(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined) return "—";
  const value = (fraction * 100).toFixed(digits);
  return fraction > 0 ? `+${value}%` : `${value}%`;
}

export function formatPoints(points: number | null | undefined): string {
  if (points === null || points === undefined) return "—";
  return points > 0 ? `+${points}` : `${points}`;
}

async function send<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await errorDetail(response, `${fallback} (status ${response.status})`));
  return response.json() as Promise<T>;
}

export function fetchBets(status: BetFilter, signal?: AbortSignal): Promise<Bet[]> {
  return send(`/api/v1/bets?status=${status}`, { signal }, "Could not load bets");
}

export function fetchSummary(groupBy: GroupBy, signal?: AbortSignal): Promise<Summary> {
  return send(`/api/v1/bets/summary?group_by=${groupBy}`, { signal }, "Could not load the summary");
}

export function createBet(bet: NewBet): Promise<Bet> {
  return send(
    "/api/v1/bets",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(bet) },
    "Could not log the bet",
  );
}

export function voidBet(id: number): Promise<Bet> {
  return send(
    `/api/v1/bets/${id}`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "void" }) },
    "Could not void the bet",
  );
}

/** One of the NBA model's daily picks. `targeted` = moneyline EV over 3%; `watchlist` = spread, tracking only. */
export interface ModelPick {
  id: number;
  slate_date: string;
  game: BetGame;
  model_version: string;
  tier: "targeted" | "watchlist";
  market: "moneyline" | "spread";
  side: "home" | "away";
  line: number | null;
  book: string;
  price_american: number;
  model_prob: number;
  market_prob_novig: number | null;
  ev: number;
  kelly_frac: number;
  close_line: number | null;
  close_price: number | null;
  close_source: string | null;
  clv_points: number | null;
  clv: number | null;
  result: "won" | "lost" | "push" | null;
}

export interface TrackRecord {
  tier: ModelPick["tier"];
  picks: number;
  graded: number;
  won: number;
  lost: number;
  push: number;
  flat_roi: number | null;
  avg_clv: number | null;
  clv_samples: number;
  beat_close_pct: number | null;
}

export interface ModelPicks { record: TrackRecord[]; picks: ModelPick[] }

/** "GSW ML -295", "GSW -7.5 -105". */
export function describePick(pick: ModelPick): string {
  const team = pick.side === "home" ? pick.game.home : pick.game.away;
  const number = pick.market === "spread" ? formatLine(pick.line) : "ML";
  return `${team} ${number} ${formatPrice(pick.price_american)}`;
}

export function fetchModelPicks(signal?: AbortSignal): Promise<ModelPicks> {
  return send("/api/v1/model-picks", { signal }, "Could not load the model picks");
}
