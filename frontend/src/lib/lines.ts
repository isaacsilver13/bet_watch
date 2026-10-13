import { errorDetail } from "./http";
import type { LineKind, SlateLine } from "./slate";

export interface BookLine {
  book: string;
  venue: string | null;
  observed_at: string;
  spread_home: number | null;
  spread_home_price: number | null;
  spread_away_price: number | null;
  total: number | null;
  over_price: number | null;
  under_price: number | null;
  ml_home: number | null;
  ml_away: number | null;
}

export interface BestView { book: string; venue: string | null; price: number; line: number | null }

export interface ConsensusView {
  spread_home: number | null;
  total: number | null;
  home_cover_prob: number | null;
  over_prob: number | null;
  home_win_prob: number | null;
  books: number;
}

export interface HistoryPoint {
  observed_at: string;
  spread_home: number | null;
  total: number | null;
  home_win_prob: number | null;
}

export interface LinesResponse {
  game_id: number;
  event_id: string | null;
  reference: Partial<Record<LineKind, SlateLine>>;
  books: BookLine[];
  best: Record<string, BestView>;
  consensus: ConsensusView | null;
  history: HistoryPoint[];
  snapshot_at: string | null;
  quota_remaining: number | null;
  can_refresh: boolean;
}

export interface RefreshResponse {
  refreshed: boolean;
  reason: string | null;
  state: string | null;
  snapshot_at: string | null;
  quota_remaining: number | null;
}

export const REFRESH_REASONS: Record<string, string> = {
  recent: "Prices were fetched in the last 10 minutes, so nothing new was requested.",
  quota: "The Odds API credits are at the safety floor, so nothing was requested.",
  throttled: "Too many refreshes in a short time, so nothing was requested.",
};

export function formatPrice(price: number | null | undefined): string {
  if (price === null || price === undefined) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

/** A side's own spread: +1.5, -3, PK. */
export function formatLine(line: number | null | undefined): string {
  if (line === null || line === undefined) return "—";
  if (line === 0) return "PK";
  return line > 0 ? `+${line}` : `${line}`;
}

export async function fetchLines(gameId: number | string, signal?: AbortSignal): Promise<LinesResponse> {
  const response = await fetch(`/api/v1/games/${gameId}/lines`, { signal });
  if (!response.ok) throw new Error(await errorDetail(response, `Lines request failed with status ${response.status}`));
  return response.json() as Promise<LinesResponse>;
}

export async function refreshGame(gameId: number | string): Promise<RefreshResponse> {
  const response = await fetch(`/api/v1/games/${gameId}/refresh`, { method: "POST" });
  if (!response.ok) throw new Error(await errorDetail(response, `Refresh failed with status ${response.status}`));
  return response.json() as Promise<RefreshResponse>;
}
