import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ComparisonResponse, PredictionMarketsResponse, Sport } from "./types";
import App from "./App";
import { fetchOdds, fetchPredictionMarkets, fetchStatus } from "./lib/api";

vi.mock("./lib/api", () => ({
  fetchOdds: vi.fn(),
  fetchPredictionMarkets: vi.fn(),
  fetchStatus: vi.fn(),
}));

function board(sport: Sport, team: string): ComparisonResponse {
  return {
    generated_at: "2026-10-08T00:00:00Z",
    provider_status: { fixture: "configured" },
    provider_details: {
      fixture: {
        status: "configured",
        cache_hit: false,
        stale: false,
        last_refreshed_at: "2026-10-08T00:00:00Z",
        error_type: null,
        quota_remaining: null,
        cache_age_seconds: 0,
      },
    },
    events: [{
      event: {
        id: `event:${sport}`,
        sport,
        league: sport.toUpperCase(),
        home_team: `${team} Home`,
        away_team: `${team} Away`,
        start_time: "2026-10-09T00:00:00Z",
      },
      offers: [],
    }],
  };
}

function predictions(
  state: "configured" | "error",
  withContract = false,
): PredictionMarketsResponse {
  const contracts = withContract ? [{
    venue: "kalshi",
    contract_id: "KXNFLGAME-TEST-WAS",
    source_event_id: "KXNFLGAME-TEST",
    title: "Washington wins",
    outcome: "Washington",
    url: "https://kalshi.com/markets/test",
    rules: "If Washington wins, resolves Yes.",
    rules_primary: "If Washington wins, resolves Yes.",
    rules_secondary: "",
    probability: 0.4,
    last_price: 0.4,
    best_bid: 0.39,
    best_ask: 0.41,
    midpoint: 0.4,
    spread: 0.02,
    bid_size: 5,
    ask_size: 7,
    volume: 20,
    open_interest: 10,
    liquidity: null,
    status: "active",
    close_time: "2026-10-09T00:00:00Z",
    metadata_updated_at: null,
    observed_at: "2026-10-08T00:00:00Z",
    match_status: "unmatched" as const,
    match_reason: "fixture",
    event_id: null,
    matched_selection: null,
  }] : [];
  return {
    generated_at: "2026-10-08T00:00:00Z",
    provider_status: { kalshi: state },
    provider_details: {
      kalshi: {
        status: state,
        cache_hit: false,
        stale: false,
        last_refreshed_at: "2026-10-08T00:00:00Z",
        error_type: state === "error" ? "timeout" : null,
        quota_remaining: null,
        cache_age_seconds: 0,
      },
    },
    contract_counts: {
      total: contracts.length,
      matched: 0,
      unmatched: contracts.length,
      ambiguous: 0,
      rejected: 0,
    },
    contracts,
  };
}

describe("App request isolation", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/?sport=nfl");
    vi.mocked(fetchStatus).mockResolvedValue({
      status: "ok",
      provider: "fixture",
      provider_status: "configured",
      data_stale: false,
      generated_at: null,
      event_count: 0,
      coverage: { regions: "us", bookmakers: [] },
      capabilities: { public_expensive_refresh: false },
      prediction_markets: { kalshi_enabled: true },
    });
    vi.mocked(fetchPredictionMarkets).mockResolvedValue({
      generated_at: "2026-10-08T00:00:00Z",
      provider_status: { kalshi: "configured" },
      provider_details: {},
      contract_counts: { total: 0, matched: 0, unmatched: 0, ambiguous: 0, rejected: 0 },
      contracts: [],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("ignores a superseded sport response", async () => {
    let resolveNfl!: (value: Awaited<ReturnType<typeof fetchOdds>>) => void;
    const nflRequest = new Promise<Awaited<ReturnType<typeof fetchOdds>>>((resolve) => { resolveNfl = resolve; });
    vi.mocked(fetchOdds).mockImplementation((sport) => sport === "nfl"
      ? nflRequest
      : Promise.resolve({ data: board("nba", "NBA"), refreshIgnored: null }));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "NBA" }));

    expect(await screen.findByText(/NBA Away/)).toBeInTheDocument();
    await act(async () => resolveNfl({ data: board("nfl", "NFL"), refreshIgnored: null }));

    expect(screen.queryByText(/NFL Away/)).not.toBeInTheDocument();
    expect(screen.getByText(/NBA Away/)).toBeInTheDocument();
  });

  it("preserves a good Kalshi snapshot when a later 200 response reports an error", async () => {
    let nflPredictionCalls = 0;
    vi.mocked(fetchOdds).mockImplementation((sport) => Promise.resolve({
      data: board(sport, sport.toUpperCase()),
      refreshIgnored: null,
    }));
    vi.mocked(fetchPredictionMarkets).mockImplementation((sport) => {
      if (sport === "nba") return Promise.resolve(predictions("configured"));
      nflPredictionCalls += 1;
      return Promise.resolve(predictions(nflPredictionCalls === 1 ? "configured" : "error", true));
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Show other prediction markets/ }));
    expect(await screen.findByText("Washington")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "NBA" }));
    expect(await screen.findByText(/NBA Away/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "NFL" }));

    expect(await screen.findByText(/kalshi: stale/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show other prediction markets/ }));
    expect(screen.getByText("Washington")).toBeInTheDocument();
  });

  it("restores URL-backed filters during browser navigation", async () => {
    vi.mocked(fetchOdds).mockImplementation((sport) => Promise.resolve({
      data: board(sport, sport.toUpperCase()),
      refreshIgnored: null,
    }));

    render(<App />);
    await screen.findByText(/NFL Away/);
    act(() => {
      window.history.pushState(null, "", "/?sport=nba&date=2026-10-09&team=Home&market=total");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(screen.getByRole("button", { name: "NBA" })).toHaveClass("active");
    expect(screen.getByLabelText("Game date")).toHaveValue("2026-10-09");
    expect(screen.getByLabelText("Team")).toHaveValue("Home");
    expect(screen.getByRole("tab", { name: "Total" })).toHaveAttribute("aria-selected", "true");
  });
});
