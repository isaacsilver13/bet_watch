import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  describeBet, fetchBets, fetchModelPicks, fetchSummary, formatMoney, formatPoints, formatSigned, voidBet,
  type Bet, type ModelPick, type ModelPicks, type Summary, type SummaryRow, type TrackRecord,
} from "../lib/bets";
import { BetsPage } from "./BetsPage";

vi.mock("../lib/bets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/bets")>();
  return { ...actual, fetchBets: vi.fn(), fetchModelPicks: vi.fn(), fetchSummary: vi.fn(), voidBet: vi.fn() };
});

const GAME = {
  id: 7, away: "SEA", home: "DEN", season_type: "REG", week: 6, start_time: "2026-10-16T00:15:00Z",
  status: "final", away_score: 20, home_score: 24,
};

function bet(overrides: Partial<Bet>): Bet {
  return {
    id: 1, game: GAME, placed_at: "2026-10-15T16:00:00Z", venue: "fanduel", market: "spread", selection: "home",
    line: -2.5, price_american: -110, stake: 11, fee: 0.5, status: "won", payout: 21, profit: 9.5,
    settled_at: "2026-10-16T04:00:00Z", close_line: -3.5, close_price: -108, close_source: "odds_api",
    clv_points: 1, clv_prob: 0.02, clv_pct: null, note: null,
    ...overrides,
  };
}

const row = (key: string, overrides: Partial<SummaryRow> = {}): SummaryRow => ({
  key, bets: 2, open: 1, settled: 1, staked: 11, fees: 0.5, profit: 9.5, roi: 0.8636,
  avg_clv_points: 1, avg_clv_prob: 0.02, clv_samples: 1, beat_close_pct: 100, ...overrides,
});

const SUMMARY: Summary = {
  group_by: "venue",
  total: row("all", { bets: 3, open: 1, staked: 41, fees: 1.5, profit: -11.5, roi: -0.28, avg_clv_points: 0.25, clv_samples: 3, beat_close_pct: 66.7 }),
  groups: [row("fanduel", { profit: -11, roi: -0.3548 }), row("draftkings")],
};

const BETS: Bet[] = [
  bet({ id: 3, status: "open", payout: null, profit: null, close_source: null, close_line: null, close_price: null, clv_points: null, clv_prob: null }),
  bet({ id: 2, market: "moneyline", selection: "away", line: null, price_american: 150, status: "lost", payout: 0, profit: -10.5, clv_points: null, clv_prob: -0.01, venue: "draftkings" }),
  bet({ id: 1 }),
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><BetsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const record = (tier: TrackRecord["tier"], overrides: Partial<TrackRecord> = {}): TrackRecord => ({
  tier, picks: 0, graded: 0, won: 0, lost: 0, push: 0, flat_roi: null, avg_clv: null, clv_samples: 0,
  beat_close_pct: null, ...overrides,
});
const NO_PICKS: ModelPicks = { record: [record("targeted"), record("watchlist")], picks: [] };
const NBA_GAME = { ...GAME, id: 9, away: "NYK", home: "GSW", season_type: "REG", week: null };

function pick(overrides: Partial<ModelPick>): ModelPick {
  return {
    id: 1, slate_date: "2026-01-15", game: NBA_GAME, model_version: "v1", tier: "targeted", market: "moneyline",
    side: "home", line: null, book: "betrivers", price_american: -295, model_prob: 0.8, market_prob_novig: 0.74,
    ev: 0.0712, kelly_frac: 0.063, close_line: null, close_price: -274, close_source: "espn", clv_points: null,
    clv: -0.0188, result: "won", ...overrides,
  };
}

describe("BetsPage", () => {
  beforeEach(() => {
    vi.mocked(fetchModelPicks).mockResolvedValue(NO_PICKS);
  });

  afterEach(() => {
    cleanup();
    vi.mocked(fetchModelPicks).mockReset();
    vi.mocked(fetchBets).mockReset();
    vi.mocked(fetchSummary).mockReset();
    vi.mocked(voidBet).mockReset();
    vi.restoreAllMocks();
  });

  it("shows profit after fees, ROI, CLV and the breakdown", async () => {
    vi.mocked(fetchBets).mockResolvedValue(BETS);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    renderPage();
    const profit = (await screen.findByText("Profit after fees")).closest("div")!;
    expect(within(profit).getByText("-$11.50")).toHaveClass("negative");
    const roi = screen.getByText("ROI", { selector: "dt" }).closest("div")!;
    expect(within(roi).getByText("-28.0%")).toBeInTheDocument();
    expect(within(roi).getByText("on $41.00 staked")).toBeInTheDocument();
    const clv = screen.getByText("Average CLV").closest("div")!;
    expect(within(clv).getByText("+0.25 pts")).toBeInTheDocument();
    expect(within(clv).getByText("3 bets with a close")).toBeInTheDocument();
    expect(screen.getByText("67%")).toBeInTheDocument();  // beat the close
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByRole("row", { name: /FanDuel 2 \$11\.00 -\$11\.00 -35\.5%/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /DraftKings/ })).toBeInTheDocument();
  });

  it("links to both sports' slates", async () => {
    vi.mocked(fetchBets).mockResolvedValue([]);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    renderPage();
    expect(await screen.findByRole("link", { name: "NFL" })).toHaveAttribute("href", "/nfl");
    expect(screen.getByRole("link", { name: "NBA" })).toHaveAttribute("href", "/nba");
  });

  it("lists each bet with its result, profit and closing-line value", async () => {
    vi.mocked(fetchBets).mockResolvedValue(BETS);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    renderPage();
    await screen.findAllByText("DEN -2.5 -110");
    const rowWith = (text: string) => screen.getAllByRole("row").find((r) => within(r).queryByText(text))!;
    const won = rowWith("Won");
    expect(within(won).getByText("Won")).toBeInTheDocument();
    expect(within(won).getByText("$9.50")).toHaveClass("positive");
    expect(within(won).getByText("+1 pts · +2.0% price (odds_api)")).toBeInTheDocument();
    expect(within(won).getByText("fee $0.50")).toBeInTheDocument();
    expect(within(won).getByRole("link", { name: "SEA @ DEN" })).toHaveAttribute("href", "/game/7");
    expect(within(won).getByText(/SEA 20–24 DEN/)).toBeInTheDocument();
    const lost = rowWith("Lost");
    expect(within(lost).getByText("-$10.50")).toHaveClass("negative");
    expect(within(lost).getByText("-1.0% price (odds_api)")).toBeInTheDocument();
    const open = rowWith("Open");
    expect(within(open).getByRole("button", { name: "Void DEN -2.5 -110" })).toBeInTheDocument();
    expect(within(open).getAllByText("—")).toHaveLength(2);  // no profit and no close yet
  });

  it("filters by status and regroups the breakdown", async () => {
    vi.mocked(fetchBets).mockResolvedValue(BETS);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    renderPage();
    await screen.findAllByText("DEN -2.5 -110");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(fetchBets).toHaveBeenLastCalledWith("open", expect.anything()));
    fireEvent.click(screen.getByRole("button", { name: "Market" }));
    await waitFor(() => expect(fetchSummary).toHaveBeenLastCalledWith("market", expect.anything()));
  });

  it("asks before voiding, and a declined confirmation changes nothing", async () => {
    vi.mocked(fetchBets).mockResolvedValue(BETS);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Void DEN -2.5 -110" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("DEN -2.5 -110"));
    expect(voidBet).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("voids an open bet and refreshes", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(fetchBets).mockResolvedValue(BETS);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    vi.mocked(voidBet).mockResolvedValue(bet({ id: 3, status: "void" }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Void DEN -2.5 -110" }));
    await waitFor(() => expect(voidBet).toHaveBeenCalledWith(3, expect.anything()));
    await waitFor(() => expect(vi.mocked(fetchBets).mock.calls.length).toBeGreaterThan(1));  // refetched
  });

  it("says when there are no bets and shows errors", async () => {
    vi.mocked(fetchBets).mockResolvedValue([]);
    vi.mocked(fetchSummary).mockRejectedValue(new Error("Could not load the summary (status 503)"));
    renderPage();
    expect(await screen.findByText(/No bets here yet/)).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("status 503");
  });

  it("shows the model picks' running CLV, results and the real-money gate", async () => {
    vi.mocked(fetchBets).mockResolvedValue([]);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    vi.mocked(fetchModelPicks).mockResolvedValue({
      record: [
        record("targeted", { picks: 12, graded: 10, won: 6, lost: 4, flat_roi: 0.081, avg_clv: 0.0142, clv_samples: 11, beat_close_pct: 63.6 }),
        record("watchlist", { picks: 1, graded: 1, won: 1, flat_roi: 0.952, avg_clv: -0.0238, clv_samples: 1, beat_close_pct: 0 }),
      ],
      picks: [
        pick({ id: 2, tier: "watchlist", market: "spread", line: -7.5, price_american: -105, clv_points: 1, clv: -0.0238 }),
        pick({ id: 1 }),
      ],
    });
    renderPage();
    const recordTable = await screen.findByRole("table", { name: "Model pick record" });
    const targeted = within(recordTable).getByRole("row", { name: /Targeted/ });
    expect(within(targeted).getByText("6-4-0")).toBeInTheDocument();
    expect(within(targeted).getByText("+8.1%")).toHaveClass("positive");
    expect(within(targeted).getByText("+1.4%")).toHaveClass("positive");
    expect(screen.getByText(/Gate not met: \+1\.4% CLV over 11 of 200 targeted picks/)).toBeInTheDocument();
    const picks = screen.getByRole("table", { name: "Model picks" });
    const ml = within(picks).getByRole("row", { name: /GSW ML -295/ });
    expect(within(ml).getByText("BetRivers")).toBeInTheDocument();
    expect(within(ml).getByText("+7.1%")).toBeInTheDocument();
    expect(within(ml).getByText("-1.9%")).toHaveClass("negative");
    const spread = within(picks).getByRole("row", { name: /GSW -7\.5 -105/ });
    expect(within(spread).getByText("+1 pts")).toBeInTheDocument();
    expect(within(spread).getByText(/Watchlist/)).toBeInTheDocument();
  });

  it("says when the model has no picks yet", async () => {
    vi.mocked(fetchBets).mockResolvedValue([]);
    vi.mocked(fetchSummary).mockResolvedValue(SUMMARY);
    renderPage();
    expect(await screen.findByText("No model picks yet.")).toBeInTheDocument();
    expect(screen.getByText(/No closes yet/)).toBeInTheDocument();
  });
});

describe("bet formatting", () => {
  it("formats money, signed percentages and points", () => {
    expect(formatMoney(9.5)).toBe("$9.50");
    expect(formatMoney(-20.5)).toBe("-$20.50");
    expect(formatMoney(null)).toBe("—");
    expect(formatSigned(0.0649)).toBe("+6.5%");
    expect(formatSigned(-0.01)).toBe("-1.0%");
    expect(formatSigned(null)).toBe("—");
    expect(formatPoints(1.5)).toBe("+1.5");
    expect(formatPoints(-0.5)).toBe("-0.5");
    expect(formatPoints(0)).toBe("0");
  });

  it("describes bets the way a book writes them", () => {
    expect(describeBet(bet({}))).toBe("DEN -2.5 -110");
    expect(describeBet(bet({ market: "spread", selection: "away", line: 2.5, price_american: 105 }))).toBe("SEA +2.5 +105");
    expect(describeBet(bet({ market: "total", selection: "over", line: 44.5, price_american: -105 }))).toBe("Over 44.5 -105");
    expect(describeBet(bet({ market: "total", selection: "under", line: 44.5 }))).toBe("Under 44.5 -110");
    expect(describeBet(bet({ market: "moneyline", selection: "away", line: null, price_american: 150 }))).toBe("SEA ML +150");
  });
});
