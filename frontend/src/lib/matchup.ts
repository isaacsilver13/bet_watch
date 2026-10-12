import type { SlateGame, SlateTeam } from "./slate";

export interface Tally { win: number; loss: number; push: number }
/** For over/under, win = over and loss = under. */
export interface RecordSet { su: Tally; ats: Tally; ou: Tally }
export interface Metric { value: number; rank: number; of: number }

export interface Situation {
  rest_days: number | null;
  short_week: boolean;
  off_bye: boolean;
  travel_miles: number | null;
  tz_crossed: number | null;
  previous_opponent: string | null;
}

export interface Injury {
  player: string;
  position: string | null;
  status: string;
  detail: string | null;
  reported_at: string | null;
}

export interface QbFlag { player: string; status: string }

export interface TeamContext {
  team: SlateTeam;
  records: Record<string, RecordSet>;
  efficiency: Record<string, Metric>;
  situation: Situation;
  injuries: Injury[];
  qb_flag: QbFlag | null;
}

export interface Meeting {
  game_id: number;
  start_time: string;
  home: string;
  away: string;
  home_score: number;
  away_score: number;
  spread_home: number | null;
  total: number | null;
}

export interface ContextResponse {
  game_id: number;
  last_n: number;
  divisional: boolean;
  primetime: boolean;
  home: TeamContext;
  away: TeamContext;
  head_to_head: { meetings: Meeting[]; home_su: Tally; home_ats: Tally };
  data_as_of: Record<string, string | null>;
}

async function getJson<T>(path: string, label: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`${label} request failed with status ${response.status}`);
  return response.json() as Promise<T>;
}

export function fetchGame(id: string, signal?: AbortSignal): Promise<SlateGame> {
  return getJson(`/api/v1/games/${encodeURIComponent(id)}`, "Game", signal);
}

export function fetchContext(id: string, lastN: number, signal?: AbortSignal): Promise<ContextResponse> {
  return getJson(
    `/api/v1/games/${encodeURIComponent(id)}/context?last_n=${lastN}`,
    "Context",
    signal,
  );
}

/** "2-1" for wins-losses, "2-1-1" once there is a push. */
export function formatTally(tally: Tally): string {
  const base = `${tally.win}-${tally.loss}`;
  return tally.push ? `${base}-${tally.push}` : base;
}

export interface MetricRow { key: string; label: string; decimals: number; signed?: boolean }

/** Display order and formatting. Ranks already account for which direction is better. */
export const METRIC_ROWS: MetricRow[] = [
  { key: "off_epa_play", label: "Offense EPA / play", decimals: 3, signed: true },
  { key: "def_epa_play", label: "Defense EPA / play allowed", decimals: 3, signed: true },
  { key: "off_pass_epa_play", label: "Pass EPA / play", decimals: 3, signed: true },
  { key: "off_rush_epa_play", label: "Rush EPA / play", decimals: 3, signed: true },
  { key: "def_pass_epa_play", label: "Pass EPA / play allowed", decimals: 3, signed: true },
  { key: "def_rush_epa_play", label: "Rush EPA / play allowed", decimals: 3, signed: true },
  { key: "yards_play", label: "Yards / play", decimals: 1 },
  { key: "yards_play_allowed", label: "Yards / play allowed", decimals: 1 },
  { key: "points_for_pg", label: "Points / game", decimals: 1 },
  { key: "points_against_pg", label: "Points allowed / game", decimals: 1 },
  { key: "turnovers_pg", label: "Turnovers / game", decimals: 2 },
  { key: "takeaways_pg", label: "Takeaways / game", decimals: 2 },
  { key: "sacks_taken_pg", label: "Sacks taken / game", decimals: 2 },
  { key: "sacks_pg", label: "Sacks / game", decimals: 2 },
];

export function formatMetric(metric: Metric | undefined, row: MetricRow): string {
  if (!metric) return "—";
  const sign = row.signed && metric.value > 0 ? "+" : "";
  return `${sign}${metric.value.toFixed(row.decimals)} (#${metric.rank} of ${metric.of})`;
}

/** Hours of age after which a panel is flagged stale, by data_as_of key. */
export const STALE_AFTER_HOURS: Record<string, number> = {
  records: 24 * 8,
  efficiency: 24 * 8,
  situational: 24 * 8,
  injuries: 36,
};

export function isStale(asOf: string | null | undefined, key: string, nowMs = Date.now()): boolean {
  if (!asOf) return false;
  const limit = STALE_AFTER_HOURS[key];
  return limit !== undefined && nowMs - new Date(asOf).getTime() > limit * 3_600_000;
}
