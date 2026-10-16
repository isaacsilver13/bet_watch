import { errorDetail } from "./http";

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

export interface SlateWeather {
  temp_f: number | null;
  wind_mph: number | null;
  precip_pct: number | null;
  conditions: string | null;
  fetched_at: string;
}

export type LineKind = "open" | "current" | "close";

export interface StarFlag { out: string[]; questionable: string[] }
export interface SlateStars { home: StarFlag; away: StarFlag }
export interface BetBadge { total: number; open: number; net: number | null }

export interface SlateGame {
  id: number;
  sport: string;
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
  roof: string | null;
  neutral: boolean;
  lines: Partial<Record<LineKind, SlateLine>>;
  spread_move: number | null;
  total_move: number | null;
  odds_event_id: string | null;
  weather: SlateWeather | null;
  star?: SlateStars | null;
  bets?: BetBadge | null;
}

export interface WeekRef { season_type: string; week: number }

/** A sport with no weeks (the NBA): one Eastern calendar day of games. */
export interface DaySlateResponse {
  sport: string;
  date: string;  // YYYY-MM-DD
  previous: string | null;  // the nearest earlier day with games
  next: string | null;
  games: SlateGame[];
}

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

export async function fetchDaySlate(
  { date, signal }: { date?: string; signal?: AbortSignal } = {},
): Promise<DaySlateResponse> {
  const params = new URLSearchParams({ sport: "nba" });
  if (date) params.set("date", date);
  const response = await fetch(`/api/v1/slate/days?${params.toString()}`, { signal });
  if (!response.ok) throw new Error(await errorDetail(response, `Slate request failed with status ${response.status}`));
  return response.json() as Promise<DaySlateResponse>;
}

/** The US Eastern calendar date of a game (YYYY-MM-DD), which is how the NBA dates its slate. */
export function easternDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

/** The slate page that holds this game. */
export function slatePath(
  game: Pick<SlateGame, "sport" | "season" | "season_type" | "week" | "start_time">,
): string {
  if (game.sport === "nba") return `/nba/${easternDate(game.start_time)}`;
  return `/nfl/${game.season}/${game.season_type.toLowerCase()}/${game.week}`;
}

/** Book-style spread label from the home line: -1.5 -> "DEN -1.5", +1.5 -> "SEA -1.5". */
export function formatSpread(game: Pick<SlateGame, "home" | "away">, spreadHome: number | null | undefined): string {
  if (spreadHome === null || spreadHome === undefined) return "—";
  if (spreadHome === 0) return "PK";
  return spreadHome < 0 ? `${game.home.abbr} ${spreadHome}` : `${game.away.abbr} -${spreadHome}`;
}

/** "55° · 12 mph wind · Overcast" for outdoor games, "Indoors" for domes, null when unknown. */
export function weatherText(game: Pick<SlateGame, "roof" | "weather"> & { sport?: string }): string | null {
  if (game.sport === "nba") return null;  // every arena is indoors; weather is not a factor
  if (game.roof === "dome") return "Indoors";
  const retractable = game.roof === "retractable" ? " (retractable roof)" : "";
  const weather = game.weather;
  if (!weather) return game.roof === "retractable" ? "Retractable roof" : null;
  const parts = [
    weather.temp_f !== null ? `${Math.round(weather.temp_f)}°` : null,
    weather.wind_mph !== null ? `${Math.round(weather.wind_mph)} mph wind` : null,
    weather.conditions,
  ].filter((part): part is string => part !== null);
  return parts.length ? `${parts.join(" · ")}${retractable}` : null;
}

export interface StarBadge { team: string; kind: "out" | "questionable"; names: string[] }

/** The badges a card shows: per team, the stars who are out and then those who are questionable. Away first. */
export function starBadges(game: Pick<SlateGame, "home" | "away" | "star">): StarBadge[] {
  if (!game.star) return [];
  const badges: StarBadge[] = [];
  for (const side of ["away", "home"] as const) {
    const flag = game.star[side];
    if (flag.out.length) badges.push({ team: game[side].abbr, kind: "out", names: flag.out });
    if (flag.questionable.length) badges.push({ team: game[side].abbr, kind: "questionable", names: flag.questionable });
  }
  return badges;
}

/** "3 bets · 1 open · +$4.50": the profit appears once a bet has settled. */
export function betsText(bets: BetBadge): string {
  const net = bets.net === null ? "" : ` · ${bets.net < 0 ? "-" : "+"}$${Math.abs(bets.net).toFixed(2)}`;
  return `${bets.total} bet${bets.total === 1 ? "" : "s"} · ${bets.open} open${net}`;
}
