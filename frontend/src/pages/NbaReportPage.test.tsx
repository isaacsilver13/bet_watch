import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchPaperLedgerDay, fetchPaperLedgerStatus, fetchSavedNbaPaperPreview, fetchSavedNbaReport, type SavedNbaReport } from "../lib/reports";
import { NbaReportPage } from "./NbaReportPage";

vi.mock("../lib/reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/reports")>();
  return { ...actual, fetchSavedNbaReport: vi.fn(), fetchSavedNbaPaperPreview: vi.fn(), fetchPaperLedgerStatus: vi.fn(), fetchPaperLedgerDay: vi.fn() };
});

const REPORT: SavedNbaReport = {
  kind: "nba_daily_report", slate_date: "2026-10-22", generated_at: "2026-10-22T15:10:00Z",
  model_version: "v1", data_through: "2026-10-21", odds_as_of: "2026-10-22T15:00:00Z",
  games: [{ game_id: 7, away: "PHI", home: "CLE", tip: "2026-10-23T00:00:00Z",
    pred_margin: 4.2, pred_sigma: 14, p_home_win: 0.64, market_spread_home: -3.5,
    market_home_win_prob: 0.58, books: 5 }],
  picks: [
    { tier: "targeted", game_id: 7, away: "PHI", home: "CLE", market: "moneyline",
      side: "home", team: "CLE", line: null, book: "BetMGM", price_american: -110,
      fanduel_price_american: -115, model_prob: 0.64, market_prob: 0.58, ev: 0.05 },
    { tier: "watchlist", game_id: 7, away: "PHI", home: "CLE", market: "spread",
      side: "away", team: "PHI", line: 3.5, book: "FanDuel", price_american: 105,
      fanduel_price_american: 105, model_prob: 0.49, market_prob: 0.48, ev: 0.01 },
  ],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/nba/report/2026-10-22"]}>
      <Routes><Route path="/nba/report/:date" element={<NbaReportPage />} /></Routes>
    </MemoryRouter>
  </QueryClientProvider>);
}

describe("NbaReportPage", () => {
  beforeEach(() => { vi.mocked(fetchPaperLedgerDay).mockResolvedValue(null); });
  afterEach(() => { cleanup(); vi.mocked(fetchSavedNbaReport).mockReset();
    vi.mocked(fetchSavedNbaPaperPreview).mockReset(); vi.mocked(fetchPaperLedgerStatus).mockReset();
    vi.mocked(fetchPaperLedgerDay).mockReset(); });

  it("compares saved model and market probabilities and filters saved picks", async () => {
    vi.mocked(fetchSavedNbaReport).mockResolvedValue(REPORT);
    vi.mocked(fetchSavedNbaPaperPreview).mockResolvedValue({
      slate_date: REPORT.slate_date, status: "preview_not_ledger", candidate_count: 1,
      allocations: [{ profile: "all", opening_bankroll: 1000,
        policy: { min_net_ev: 0.03, kelly_fraction: 0.25, version: "paper-v1" },
        bets: [{ candidate_id: "x", game_id: 7, stake: 30, fee: 0, net_ev: 0.05 }] }],
    });
    vi.mocked(fetchPaperLedgerStatus).mockResolvedValue({ profiles: [{
      profile: "all", starting_bankroll: 1000, cash: 970, open_stake: 30,
      equity_at_cost: 1000, recorded_days: 1, open_bets: 1, settled_bets: 0,
      settled_net: 0, settled_roi: null, last_slate_date: "2026-10-22",
    }] });
    renderPage();
    expect(await screen.findByRole("heading", { name: "PHI @ CLE" })).toBeInTheDocument();
    expect(screen.getAllByText("64.0%")).toHaveLength(2);
    expect(screen.getByText("Market home win")).toBeInTheDocument();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("BetMGM")).toBeInTheDocument();
    expect(within(table).getByText("-115")).toBeInTheDocument();
    expect(within(table).queryByText("PHI +3.5")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Compare market"));
    expect(screen.queryByText("Market home win")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "watchlist" } });
    expect(within(screen.getAllByRole("table")[0]).getByText("PHI +3.5")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "$1,000 profile preview" })).toBeInTheDocument();
    expect((await screen.findAllByText("All eligible"))).toHaveLength(2);
    expect(await screen.findByText("Current paper ledger across all recorded slates")).toBeInTheDocument();
    const ledgerTable = screen.getAllByRole("table")[2];
    expect(within(ledgerTable).getByText("$970.00")).toBeInTheDocument();
    expect(within(ledgerTable).getByText("$30.00")).toBeInTheDocument();
    expect(within(ledgerTable).getByText("2026-10-22")).toBeInTheDocument();
    expect(screen.getByText(/No paper decision was recorded for this slate/)).toBeInTheDocument();
  });

  it("shows frozen wager evidence and an empty recorded profile", async () => {
    vi.mocked(fetchSavedNbaReport).mockResolvedValue(REPORT);
    const policy = { version: "paper-v1", min_net_ev: 0.03, kelly_fraction: 0.25 };
    const common = { opening_cash: 1000, decision_at: "2026-10-22T15:20:00Z",
      report_sha256: "a".repeat(64), forecast_sha256: "b".repeat(64), policy };
    vi.mocked(fetchPaperLedgerDay).mockResolvedValue({
      slate_date: REPORT.slate_date, report_matches_recorded: false,
      forecast_matches_recorded: true, profiles: [
      { ...common, profile: "all", bets: [{
        wager_id: 1, candidate_id: "forecast:quote", game_id: 7, home: "CLE", away: "PHI",
        side: "home", book: "BetMGM", price_american: -110, win_prob: 0.64,
        stake: 30, fee: 0, net_ev: 0.05, quote_observation_id: 42,
        quote_observed_at: "2026-10-22T15:00:00Z", quote_provider_updated_at: null,
        result: "won", payout: 57.27, net_return: 27.27,
        settled_at: "2026-10-23T03:00:00Z", home_score: 112, away_score: 100,
        game_updated_at: "2026-10-23T02:30:00Z",
      }] },
      { ...common, profile: "top_1", bets: [] },
    ] });
    renderPage();
    expect(await screen.findByText("won · PHI 100–CLE 112")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("differs from the frozen paper decision");
    expect(screen.getByText(/#42/)).toBeInTheDocument();
    expect(screen.getByText("$27.27")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Recorded profile"), { target: { value: "top_1" } });
    expect(screen.getByText("This profile recorded no wagers for the slate.")).toBeInTheDocument();
  });

  it("shows an explicit no-value-picks state", async () => {
    vi.mocked(fetchSavedNbaReport).mockResolvedValue({ ...REPORT, picks: [] });
    renderPage();
    expect(await screen.findByText("No value picks cleared the current threshold.")).toBeInTheDocument();
  });

  it("compares integer spreads only after conditioning on no push", async () => {
    vi.mocked(fetchSavedNbaReport).mockResolvedValue({
      ...REPORT,
      picks: [{ ...REPORT.picks[1], line: 3, model_prob: 0.45,
        model_prob_conditional: 0.5, market_prob: 0.48, push_prob: 0.1 }],
    });
    renderPage();
    await screen.findByRole("heading", { name: "PHI @ CLE" });
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "watchlist" } });
    const table = screen.getByRole("table");
    expect(within(table).getByText("50.0%")).toBeInTheDocument();
    expect(within(table).getByText("10.0%")).toBeInTheDocument();
    expect(within(table).queryByText("45.0%")).not.toBeInTheDocument();
  });
});
