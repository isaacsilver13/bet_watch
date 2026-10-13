import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  describeBet, fetchBets, fetchSummary, formatMoney, formatPoints, formatSigned, voidBet,
  type Bet, type Summary, type SummaryRow,
} from "../lib/bets";
import { BetsPage } from "./BetsPage";

vi.mock("../lib/bets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/bets")>();
  return { ...actual, fetchBets: vi.fn(), fetchSummary: vi.fn(), voidBet: vi.fn() };
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

describe("BetsPage", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(fetchBets).mockReset();
    vi.mocked(fetchSummary).mockReset();
    vi.mocked(voidBet).mockReset();
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

  it("voids an open bet and refreshes", async () => {
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
