import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchPredictionMarkets } from "../lib/api";
import { createBet, type Bet } from "../lib/bets";
import { fetchLines, refreshGame, type BookLine, type LinesResponse } from "../lib/lines";
import type { SlateGame } from "../lib/slate";
import type { PredictionContract } from "../types";
import { LinesPanel } from "./LinesPanel";

vi.mock("../lib/lines", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/lines")>();
  return { ...actual, fetchLines: vi.fn(), refreshGame: vi.fn() };
});
vi.mock("../lib/bets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/bets")>();
  return { ...actual, createBet: vi.fn() };
});
vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return { ...actual, fetchPredictionMarkets: vi.fn() };
});

const GAME = {
  id: 7, home: { id: 10, abbr: "DEN", name: "Denver Broncos" }, away: { id: 26, abbr: "SEA", name: "Seattle Seahawks" },
  sport: "nfl",
} as SlateGame;

function book(name: string, venue: string | null, spreadHome: number, home: number, away: number): BookLine {
  return {
    book: name, venue, observed_at: new Date().toISOString(),
    spread_home: spreadHome, spread_home_price: home, spread_away_price: away,
    total: 42.5, over_price: -110, under_price: -110, ml_home: 102, ml_away: -122,
  };
}

function lines(overrides: Partial<LinesResponse> = {}): LinesResponse {
  return {
    game_id: 7, event_id: "event:den",
    reference: { open: { source: "espn", spread_home: 2.5, spread_home_price: -110, spread_away_price: -110, total: 43.5, over_price: -110, under_price: -110, ml_home: null, ml_away: null, captured_at: "2026-10-12T15:00:00Z" } },
    books: [book("DraftKings", "draftkings", 1.5, -110, -110), book("BetRivers", "betrivers", 1.5, -105, -115)],
    best: {
      spread_home: { book: "BetRivers", venue: "betrivers", price: -105, line: 1.5 },
      spread_away: { book: "DraftKings", venue: "draftkings", price: -110, line: -1.5 },
      ml_home: { book: "DraftKings", venue: "draftkings", price: 102, line: null },
    },
    consensus: { spread_home: 1.5, total: 42.5, home_cover_prob: 0.496, over_prob: 0.5, home_win_prob: 0.469, books: 2 },
    history: [
      { observed_at: "2026-10-13T15:00:00Z", spread_home: 2.5, total: 43.5, home_win_prob: 0.45 },
      { observed_at: "2026-10-15T15:00:00Z", spread_home: 1.5, total: 42.5, home_win_prob: 0.469 },
    ],
    snapshot_at: new Date().toISOString(), quota_remaining: 471, can_refresh: true,
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><LinesPanel game={GAME} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const SAVED = {
  id: 1, venue: "draftkings", market: "spread", selection: "home", line: 1.5, price_american: -110, stake: 11, fee: 0.5,
  game: { id: 7, away: "SEA", home: "DEN" },
} as unknown as Bet;

describe("LinesPanel", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(fetchLines).mockReset();
    vi.mocked(refreshGame).mockReset();
    vi.mocked(createBet).mockReset();
    vi.mocked(fetchPredictionMarkets).mockReset();
  });

  it("shows best prices without an all-books grid, plus consensus and line movement", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines());
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    renderPanel();
    const comparison = await screen.findByRole("table", { name: "Best and FanDuel prices" });
    expect(within(comparison).getByRole("columnheader", { name: "Best tracked book" })).toBeInTheDocument();
    expect(within(comparison).queryByRole("columnheader", { name: "Book" })).not.toBeInTheDocument();
    expect(screen.getByText(/Consensus of 2 books: DEN \+1\.5 · O\/U 42\.5 · DEN win 46\.9%/)).toBeInTheDocument();
    expect(within(comparison).getByRole("button", { name: "Log BetRivers DEN +1.5 -105" })).toBeInTheDocument();
    expect(within(comparison).getByRole("button", { name: "Log DraftKings SEA -1.5 -110" })).toBeInTheDocument();
    expect(within(comparison).getByRole("button", { name: "Log DraftKings DEN ML +102" })).toBeInTheDocument();
    expect(within(comparison).getAllByText("No quote at this line")).toHaveLength(6);
    expect(within(comparison).getAllByText("No tracked quote at this line")).toHaveLength(3);
    expect(screen.getByText("DEN spread: +2.5 → +1.5 over 2 snapshots")).toBeInTheDocument();
    expect(screen.getByText("Total: 43.5 → 42.5 over 2 snapshots")).toBeInTheDocument();
    expect(screen.getByText(/471 credits left/)).toBeInTheDocument();
    expect(screen.getByText(/DEN \+2\.5 · 43\.5/)).toBeInTheDocument();  // ESPN's opener
  });

  it("compares FanDuel only at the best offer's threshold and logs its exact quote", async () => {
    const fanduel = book("FanDuel", "fanduel", 1.5, -108, -112);
    fanduel.over_price = -105;
    vi.mocked(fetchLines).mockResolvedValue(lines({
      books: [...lines().books, fanduel],
      best: {
        spread_home: { book: "BetRivers", venue: "betrivers", price: -105, line: 1.5 },
        spread_away: { book: "DraftKings", venue: "draftkings", price: -110, line: -1.5 },
        over: { book: "FanDuel", venue: "fanduel", price: -105, line: 42.5 },
      },
    }));
    renderPanel();
    const comparison = await screen.findByRole("table", { name: "Best and FanDuel prices" });
    const homeRow = within(comparison).getByRole("row", { name: /DEN \+1\.5/ });
    expect(within(homeRow).getByRole("button", { name: "Log FanDuel DEN +1.5 -108" })).toBeInTheDocument();
    fireEvent.click(within(homeRow).getByRole("button", { name: "Log FanDuel DEN +1.5 -108" }));
    expect(screen.getByLabelText("Book")).toHaveValue("fanduel");
    expect(screen.getByLabelText("Line")).toHaveValue(1.5);
    expect(screen.getByLabelText("Price")).toHaveValue(-108);
  });

  it("does not show a FanDuel price from a different spread or total", async () => {
    const fanduel = book("FanDuel", "fanduel", 2.5, -105, -115);
    fanduel.total = 43.5;
    vi.mocked(fetchLines).mockResolvedValue(lines({
      books: [...lines().books, fanduel],
      best: {
        ...lines().best,
        over: { book: "DraftKings", venue: "draftkings", price: -110, line: 42.5 },
      },
    }));
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    renderPanel();
    const comparison = await screen.findByRole("table", { name: "Best and FanDuel prices" });
    expect(within(comparison).getAllByText("No quote at this line")).toHaveLength(4);
    expect(within(comparison).queryByRole("button", { name: /Log FanDuel .*\+1\.5|Log FanDuel Over 42\.5/ })).not.toBeInTheDocument();
    expect(within(comparison).getByRole("button", { name: "Log FanDuel DEN ML +102" })).toBeInTheDocument();
  });

  it("keeps missing total and moneyline selections visible on a spread-only snapshot", async () => {
    const spreadOnly = book("DraftKings", "draftkings", 1.5, -110, -110);
    spreadOnly.total = spreadOnly.over_price = spreadOnly.under_price = spreadOnly.ml_home = spreadOnly.ml_away = null;
    vi.mocked(fetchLines).mockResolvedValue(lines({
      books: [spreadOnly],
      best: { spread_home: { book: "DraftKings", venue: "draftkings", price: -110, line: 1.5 } },
      consensus: { spread_home: 1.5, total: null, home_cover_prob: null, over_prob: null, home_win_prob: null, books: 1 },
    }));
    renderPanel();
    const comparison = await screen.findByRole("table", { name: "Best and FanDuel prices" });
    expect(within(comparison).getByRole("row", { name: /Over —/ })).toBeInTheDocument();
    expect(within(comparison).getByRole("row", { name: /DEN ML/ })).toBeInTheDocument();
    expect(within(comparison).getAllByText("No tracked quote at this line")).toHaveLength(5);
  });

  it("opens a bet form pre-filled from the clicked price and logs it", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines());
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    vi.mocked(createBet).mockResolvedValue(SAVED);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Log BetRivers DEN +1.5 -105" }));
    expect(screen.getByLabelText("Book")).toHaveValue("betrivers");
    expect(screen.getByLabelText("Market")).toHaveValue("spread");
    expect(screen.getByLabelText("Side")).toHaveValue("home");
    expect(screen.getByLabelText("Line")).toHaveValue(1.5);
    expect(screen.getByLabelText("Price")).toHaveValue(-105);
    fireEvent.change(screen.getByLabelText("Stake ($)"), { target: { value: "11" } });
    fireEvent.click(screen.getByRole("button", { name: "Log bet" }));
    await waitFor(() => expect(createBet).toHaveBeenCalledWith({
      game_id: 7, venue: "betrivers", market: "spread", selection: "home", line: 1.5,
      price_american: -105, stake: 11,
    }, expect.anything()));
    expect(await screen.findByRole("status")).toHaveTextContent("Logged DEN +1.5 -110 for $11.00 at DraftKings. Fee $0.50.");
  });

  it("clears the line for a moneyline pick and shows the server's message when logging fails", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines());
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    vi.mocked(createBet).mockRejectedValue(new Error("Game not found"));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /Log DraftKings DEN ML/ }));
    expect(screen.getByLabelText("Market")).toHaveValue("moneyline");
    expect(screen.queryByLabelText("Line")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Stake ($)"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Log bet" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Game not found");
  });

  it("refreshes prices on request and explains when nothing was fetched", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines());
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    vi.mocked(refreshGame).mockResolvedValue({ refreshed: false, reason: "recent", state: null, snapshot_at: null, quota_remaining: null });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Refresh prices (3 credits)" }));
    expect(await screen.findByRole("status")).toHaveTextContent("fetched in the last 10 minutes");
    expect(refreshGame).toHaveBeenCalledWith(7);
  });

  it("has no refresh button when the server cannot spend, and says when no prices exist", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines({
      books: [], best: {}, consensus: null, history: [], snapshot_at: null, quota_remaining: null, can_refresh: false, event_id: null,
    }));
    renderPanel();
    expect(await screen.findByText(/No sportsbook prices for this game yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refresh prices/ })).not.toBeInTheDocument();
    expect(screen.getByText("No sportsbook prices yet")).toBeInTheDocument();
    expect(screen.getAllByText(/not enough snapshots yet/i)).toHaveLength(2);  // one per chart
  });

  it("tells an NBA game when to expect prices, in the NBA's own rhythm", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines({
      books: [], best: {}, consensus: null, history: [], snapshot_at: null, quota_remaining: null, can_refresh: false, event_id: null,
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><LinesPanel game={{ ...GAME, sport: "nba" }} /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/10am snapshot and shortly before the first and last tips/)).toBeInTheDocument();
    expect(screen.queryByText(/Tuesday/)).not.toBeInTheDocument();
  });

  it("requests prediction contracts for the game's sport", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines());
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {}, contracts: [],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><LinesPanel game={{ ...GAME, sport: "nba" }} /></MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(fetchPredictionMarkets).toHaveBeenCalledWith("nba", expect.anything()));
  });

  it("marks old prices as stale and shows only this game's Kalshi contracts", async () => {
    vi.mocked(fetchLines).mockResolvedValue(lines({ snapshot_at: "2020-01-01T00:00:00Z" }));
    const contract = (eventId: string, title: string) => ({
      venue: "kalshi", contract_id: title, source_event_id: title, title, outcome: "DEN", url: "https://kalshi.test",
      rules: "", probability: 0.5, last_price: 0.5, best_bid: 0.49, best_ask: 0.51, midpoint: 0.5, spread: 0.02,
      bid_size: 10, ask_size: 10, volume: 100, open_interest: 50, liquidity: null, status: "active", close_time: null,
      metadata_updated_at: null, observed_at: "2026-10-15T15:00:00Z", rules_primary: "", rules_secondary: "",
      match_status: "matched", match_reason: "", event_id: eventId, matched_selection: "DEN",
    }) as PredictionContract;
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "", provider_status: {}, provider_details: {}, contract_counts: {},
      contracts: [contract("event:den", "KXNFLGAME-DEN"), contract("event:other", "KXNFLGAME-OTHER")],
    });
    renderPanel();
    expect(await screen.findByText(/· stale/)).toBeInTheDocument();
    expect(await screen.findByText("KXNFLGAME-DEN")).toBeInTheDocument();
    expect(screen.queryByText("KXNFLGAME-OTHER")).not.toBeInTheDocument();
  });

  it("shows the API error", async () => {
    vi.mocked(fetchLines).mockRejectedValue(new Error("Game not found"));
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("Game not found");
  });
});
