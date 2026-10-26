export interface SavedNbaGame {
  game_id: number;
  home: string;
  away: string;
  tip: string;
  pred_margin: number;
  pred_sigma?: number;
  p_home_win: number;
  market_spread_home: number | null;
  market_home_win_prob: number | null;
  books: number;
  odds_as_of?: string | null;
}

export interface SavedNbaPick {
  tier: "targeted" | "watchlist";
  game_id: number;
  home: string;
  away: string;
  market: "moneyline" | "spread";
  side: "home" | "away";
  team: string;
  line: number | null;
  book: string;
  price_american: number;
  fanduel_price_american: number | null;
  model_prob: number;
  model_prob_conditional?: number | null;
  push_prob?: number | null;
  market_prob: number | null;
  ev: number;
}

export interface SavedNbaReport {
  kind: "nba_daily_report";
  slate_date: string;
  generated_at: string;
  model_version: string;
  data_through: string;
  odds_as_of: string | null;
  games: SavedNbaGame[];
  picks: SavedNbaPick[];
}

export async function fetchSavedNbaReport(date: string, signal?: AbortSignal): Promise<SavedNbaReport> {
  const response = await fetch(`/api/v1/nba/reports/${encodeURIComponent(date)}`, { signal });
  if (response.status === 401) throw new Error("Owner session expired. Sign in again.");
  if (response.status === 404) throw new Error("No saved report for this date.");
  if (!response.ok) throw new Error(`Could not load report (${response.status})`);
  return response.json() as Promise<SavedNbaReport>;
}

export interface PaperPreview {
  slate_date: string;
  status: "preview_not_ledger";
  candidate_count: number;
  allocations: Array<{
    profile: string;
    opening_bankroll: number;
    policy: { min_net_ev: number; kelly_fraction: number; version: string };
    bets: Array<{ candidate_id: string; game_id: number; stake: number; fee: number; net_ev: number }>;
  }>;
}

export async function fetchSavedNbaPaperPreview(date: string, signal?: AbortSignal): Promise<PaperPreview> {
  const response = await fetch(`/api/v1/nba/reports/${encodeURIComponent(date)}/paper-preview`, { signal });
  if (response.status === 422) throw new Error("This saved report is ineligible for paper allocation.");
  if (!response.ok) throw new Error(`Could not load paper preview (${response.status})`);
  return response.json() as Promise<PaperPreview>;
}

export interface PaperLedgerProfile {
  profile: string;
  starting_bankroll: number;
  cash: number;
  open_stake: number;
  equity_at_cost: number;
  recorded_days: number;
  open_bets: number;
  settled_bets: number;
  settled_net: number;
  settled_roi: number | null;
  last_slate_date: string | null;
}

export interface PaperLedgerStatus {
  profiles: PaperLedgerProfile[];
}

export async function fetchPaperLedgerStatus(signal?: AbortSignal): Promise<PaperLedgerStatus> {
  const response = await fetch("/api/v1/nba/paper-ledger/status", { signal });
  if (response.status === 401) throw new Error("Owner session expired. Sign in again.");
  if (!response.ok) throw new Error(`Could not load paper ledger (${response.status})`);
  return response.json() as Promise<PaperLedgerStatus>;
}

export interface PaperLedgerWager {
  wager_id: number;
  candidate_id: string;
  game_id: number;
  home: string;
  away: string;
  side: "home" | "away";
  book: string;
  price_american: number;
  win_prob: number;
  stake: number;
  fee: number;
  net_ev: number;
  quote_observation_id: number;
  quote_observed_at: string;
  quote_provider_updated_at: string | null;
  result: "won" | "lost" | "push" | null;
  payout: number | null;
  net_return: number | null;
  settled_at: string | null;
  home_score: number | null;
  away_score: number | null;
  game_updated_at: string | null;
}

export interface PaperLedgerDay {
  slate_date: string;
  report_matches_recorded: boolean;
  forecast_matches_recorded: boolean;
  profiles: Array<{
    profile: string;
    opening_cash: number;
    decision_at: string;
    report_sha256: string;
    forecast_sha256: string;
    policy: { version: string; min_net_ev: number; kelly_fraction: number };
    bets: PaperLedgerWager[];
  }>;
}

export async function fetchPaperLedgerDay(date: string, signal?: AbortSignal): Promise<PaperLedgerDay | null> {
  const response = await fetch(`/api/v1/nba/paper-ledger/slates/${encodeURIComponent(date)}`, { signal });
  if (response.status === 401) throw new Error("Owner session expired. Sign in again.");
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Could not load paper decisions (${response.status})`);
  return response.json() as Promise<PaperLedgerDay>;
}
