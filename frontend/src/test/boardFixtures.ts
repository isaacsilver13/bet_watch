import type { BoardGame, BoardResponse } from "../lib/board";
import type { BookLine } from "../lib/lines";

export const SNAPSHOT = "2999-01-01T15:00:00Z";
export const KICKOFF = "2999-01-02T00:15:00Z";

export function book(name: string, venue: string | null, spreadHome: number, home: number, away: number, extra: Partial<BookLine> = {}): BookLine {
  return {
    book: name, venue, observed_at: SNAPSHOT,
    spread_home: spreadHome, spread_home_price: home, spread_away_price: away,
    total: 42.5, over_price: -110, under_price: -110, ml_home: 102, ml_away: -122,
    ...extra,
  };
}

const line = (spread: number, total: number) => ({
  source: "espn", spread_home: spread, spread_home_price: -110, spread_away_price: -110,
  total, over_price: -110, under_price: -110, ml_home: 102, ml_away: -122,
  captured_at: "2026-10-11T15:00:00Z",
});

/** SEA @ DEN. BetMGM hangs a different number at a much better price; it must never rank. */
export function boardGame(overrides: Partial<BoardGame> = {}, id = 1): BoardGame {
  return {
    game: {
      id, sport: "nfl", espn_id: `40187${id}`, season: 2026, season_type: "REG", week: 6,
      start_time: KICKOFF, status: "scheduled",
      home: { id: 10, abbr: "DEN", name: "Denver Broncos" },
      away: { id: 26, abbr: "SEA", name: "Seattle Seahawks" },
      home_score: null, away_score: null, venue: "Empower Field at Mile High", roof: "outdoors", neutral: false,
      lines: { open: line(2.5, 43.5), current: line(1.5, 42.5) },
      spread_move: -1, total_move: -1, odds_event_id: "event:den",
      weather: { temp_f: 55.4, wind_mph: 18, precip_pct: 20, conditions: "Overcast", fetched_at: "2026-10-14T21:00:00Z" },
    },
    market: {
      snapshot_at: SNAPSHOT,
      books: [
        book("DraftKings", "draftkings", 1.5, -110, -110),
        book("BetRivers", "betrivers", 1.5, -105, -115),
        book("FanDuel", "fanduel", 1.5, -108, -112),
        book("BetMGM", "betmgm", 2.5, 300, -400),
      ],
      snapshot_books: [
        { book: "DraftKings", venue: "draftkings" }, { book: "BetRivers", venue: "betrivers" },
        { book: "FanDuel", venue: "fanduel" }, { book: "BetMGM", venue: "betmgm" },
      ],
      consensus: { spread_home: 1.5, total: 42.5, home_cover_prob: 0.5, over_prob: 0.5, home_win_prob: 0.469, books: 4 },
      best: {
        spread_home: { book: "BetRivers", venue: "betrivers", price: -105, line: 1.5 },
        spread_away: { book: "DraftKings", venue: "draftkings", price: -110, line: -1.5 },
        over: { book: "DraftKings", venue: "draftkings", price: -110, line: 42.5 },
        under: { book: "DraftKings", venue: "draftkings", price: -110, line: 42.5 },
        ml_home: { book: "DraftKings", venue: "draftkings", price: 102, line: null },
        ml_away: { book: "DraftKings", venue: "draftkings", price: -122, line: null },
      },
      coverage: {
        spread: { quoting: 4, at_line: 3 }, total: { quoting: 4, at_line: 4 }, moneyline: { quoting: 4, at_line: 4 },
      },
      movement: { first_at: "2999-01-01T09:00:00Z", spread_home: 2.5, total: 43.5, snapshots: 2 },
    },
    flags: [{ kind: "qb", team: "DEN", text: "DEN QB Bo Nix listed Questionable", as_of: "2026-10-14T20:00:00Z" }],
    ...overrides,
  };
}

export function board(games: BoardGame[] = [boardGame()], overrides: Partial<BoardResponse> = {}): BoardResponse {
  return {
    sport: "nfl", season: 2026, season_type: "REG", week: 6,
    weeks: [5, 6, 7].map((w) => ({ season_type: "REG", week: w })),
    snapshot_at: games.length ? SNAPSHOT : null,
    books_available: [
      { book: "BetMGM", venue: "betmgm" }, { book: "BetRivers", venue: "betrivers" },
      { book: "DraftKings", venue: "draftkings" }, { book: "FanDuel", venue: "fanduel" },
    ],
    books_selected: [], can_refresh: false, games,
    ...overrides,
  };
}
