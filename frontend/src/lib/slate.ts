export interface SlateTeam { id: number; abbr: string; name: string }

export interface SlateLine {
  source: string;
  spread_home: number | null;
  spread_home_price: number | null;
  spread_away_price: number | null;
  total: number | null;
  over_price: number | null;
  under_price: number | null;
  ml_home: number | null;
  ml_away: number | null;
  captured_at: string;
}

export type LineKind = "open" | "current" | "close";

export interface SlateGame {
  id: number;
  espn_id: string;
  season: number;
  season_type: string;
  week: number | null;
  start_time: string;
  status: string;
  home: SlateTeam;
  away: SlateTeam;
  home_score: number | null;
  away_score: number | null;
  venue: string | null;
  neutral: boolean;
  lines: Partial<Record<LineKind, SlateLine>>;
  spread_move: number | null;
  total_move: number | null;
  odds_event_id: string | null;
}

export interface WeekRef { season_type: string; week: number }

export interface SlateResponse {
  sport: string;
  season: number;
  season_type: string;
  week: number;
  weeks: WeekRef[];
  games: SlateGame[];
}

export interface SlateRequest {
  season?: string;
  seasonType?: string;
  week?: string;
  signal?: AbortSignal;
}

export async function fetchSlate({ season, seasonType, week, signal }: SlateRequest = {}): Promise<SlateResponse> {
  const params = new URLSearchParams({ sport: "nfl" });
  if (season && week) {
    params.set("season", season);
    params.set("week", week);
    params.set("season_type", (seasonType ?? "reg").toUpperCase());
  }
  const response = await fetch(`/api/v1/slate?${params.toString()}`, { signal });
  if (!response.ok) throw new Error(`Slate request failed with status ${response.status}`);
  return response.json() as Promise<SlateResponse>;
}

/** Book-style spread label from the home line: -1.5 -> "DEN -1.5", +1.5 -> "SEA -1.5". */
export function formatSpread(game: Pick<SlateGame, "home" | "away">, spreadHome: number | null | undefined): string {
  if (spreadHome === null || spreadHome === undefined) return "—";
  if (spreadHome === 0) return "PK";
  return spreadHome < 0 ? `${game.home.abbr} ${spreadHome}` : `${game.away.abbr} -${spreadHome}`;
}
