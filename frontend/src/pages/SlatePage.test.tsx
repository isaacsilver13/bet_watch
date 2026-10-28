import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchBoard } from "../lib/board";
import { weatherText } from "../lib/slate";
import { board, boardGame, book } from "../test/boardFixtures";
import { SlatePage } from "./SlatePage";

vi.mock("../lib/board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/board")>();
  return { ...actual, fetchBoard: vi.fn() };
});
vi.mock("../lib/matchup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/matchup")>();
  return { ...actual, fetchContext: vi.fn().mockRejectedValue(new Error("Context request failed")) };
});

function Where() {
  const location = useLocation();
  return <output aria-label="location">{location.pathname + location.search}</output>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Where />
        <Routes>
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function twoGames() {
  const second = boardGame({}, 2);
  second.game = {
    ...second.game,
    home: { id: 1, abbr: "KC", name: "Kansas City Chiefs" },
    away: { id: 2, abbr: "LV", name: "Las Vegas Raiders" },
  };
  return [boardGame({}, 1), second];
}

describe("SlatePage week board", () => {
  afterEach(() => { cleanup(); vi.mocked(fetchBoard).mockReset(); });

  it("shows the best price per side at the same line, with its book, and what it was compared against", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    expect(await screen.findByRole("link", { name: "SEA @ DEN" })).toHaveAttribute("href", "/game/1");
    expect(screen.getByRole("heading", { name: "2026 · Week 6" })).toBeInTheDocument();
    const best = screen.getByLabelText("Best price");
    expect(within(best).getByRole("button", { name: "Log BetRivers DEN +1.5 -105" })).toBeInTheDocument();
    expect(within(best).getByRole("button", { name: "Log DraftKings SEA -1.5 -110" })).toBeInTheDocument();
    expect(screen.getByText("3 of 4 books at SEA -1.5.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log BetMGM DEN +2.5 +300" })).not.toBeInTheDocument();
    // BetMGM's +300 is a different number: shown once expanded, tagged, and never "Best".
    fireEvent.click(screen.getByRole("button", { name: "Expand all books" }));
    const mgm = screen.getByRole("button", { name: "Log BetMGM DEN +2.5 +300" });
    expect(mgm).toHaveTextContent("Other number");
    expect(mgm).not.toHaveTextContent("Best");
  });

  it("keeps reference lines and book consensus in separate labelled lanes", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    await screen.findByRole("link", { name: "SEA @ DEN" });
    expect(screen.getByText("Reference · espn")).toBeInTheDocument();
    expect(screen.getAllByText("SEA -2.5 → SEA -1.5")).toHaveLength(2); // reference lane, then book lane
    expect(screen.getByText("Book consensus")).toBeInTheDocument();
  });

  it("states facts as flags, at most three, without telling anyone what to bet", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    const flags = within(await screen.findByRole("list", { name: "Research flags" })).getAllByRole("listitem");
    expect(flags.map((flag) => flag.textContent)).toEqual([
      "DEN QB Bo Nix listed Questionable",
      "Spread moved 1 point since open, now SEA -1.5",
      "55° · 18 mph wind · Overcast",
    ]);
  });

  it("switches market in the URL and restores it from the URL", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl?market=total&team=DEN");
    expect(await screen.findByRole("tab", { name: "Total", selected: true })).toBeInTheDocument();
    expect(within(await screen.findByLabelText("Best price")).getByRole("button", { name: "Log DraftKings Over 42.5 -110" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Moneyline" }));
    expect(screen.getByLabelText("location")).toHaveTextContent("market=moneyline");
    expect(screen.getByLabelText("location")).toHaveTextContent("team=DEN");
    fireEvent.click(screen.getByRole("tab", { name: "Spread" }));
    expect(screen.getByLabelText("location")).not.toHaveTextContent("market=");
  });

  it("filters by team without asking the server again", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board(twoGames()));
    renderAt("/nfl");
    await screen.findByRole("link", { name: "SEA @ DEN" });
    expect(screen.getByRole("link", { name: "LV @ KC" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Team"), { target: { value: "KC" } });
    expect(screen.queryByRole("link", { name: "SEA @ DEN" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LV @ KC" })).toBeInTheDocument();
    expect(fetchBoard).toHaveBeenCalledTimes(1);
  });

  it("re-asks for the selected books and says the best price is among them", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl?books=draftkings,fanduel");
    await screen.findByRole("link", { name: "SEA @ DEN" });
    expect(fetchBoard).toHaveBeenCalledWith(expect.objectContaining({ books: ["draftkings", "fanduel"] }));
    expect(screen.getByText("Best prices are among 2 selected books, not all tracked books.")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Books \(2 selected\)/));
    fireEvent.click(screen.getByRole("checkbox", { name: "BetMGM" }));
    await waitFor(() =>
      expect(fetchBoard).toHaveBeenLastCalledWith(expect.objectContaining({ books: ["draftkings", "fanduel", "betmgm"] })),
    );
  });

  it("opens the full book table in the row and keeps it in the URL", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    const open = await screen.findByRole("button", { name: "Expand all books" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(open);
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("location")).toHaveTextContent("game=1&panel=books");
    expect(screen.getByRole("table", { name: "spread prices by book" })).toBeInTheDocument();
  });

  it("opens the neutral research brief and says plainly when a report is unavailable", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl?game=1&panel=research");
    const brief = await screen.findByRole("region", { name: "Research brief" });
    expect(within(brief).getByText("Injury report unavailable.")).toBeInTheDocument();
    expect(within(brief).getByText("Reference line: SEA -2.5 to SEA -1.5, total 43.5 to 42.5.")).toBeInTheDocument();
    expect(within(brief).queryByText(/No injuries/i)).not.toBeInTheDocument();
    expect(within(brief).getByText(/Context, not betting advice/)).toBeInTheDocument();
  });

  it("logs a quoted price with its book and observation time shown before saving", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    fireEvent.click((await screen.findAllByRole("button", { name: "Log BetRivers DEN +1.5 -105" }))[0]);
    expect(screen.getByText(/Quote: BetRivers · observed/)).toBeInTheDocument();
    expect(screen.getByLabelText("Book")).toHaveValue("betrivers");
    expect(screen.getByLabelText("Market")).toHaveValue("spread");
    expect(screen.getByLabelText("Side")).toHaveValue("home");
    expect(screen.getByLabelText("Line")).toHaveValue(1.5);
    expect(screen.getByLabelText("Price")).toHaveValue(-105);
  });

  it("marks an old snapshot stale and stops calling its best prices current", async () => {
    const old = "2020-01-01T00:00:00Z";
    const entry = boardGame();
    entry.market.snapshot_at = old;
    vi.mocked(fetchBoard).mockResolvedValue(board([entry], { snapshot_at: old }));
    renderAt("/nfl");
    expect(await screen.findByText(/The saved odds snapshot is/)).toBeInTheDocument();
    expect(screen.getByLabelText("Best in saved snapshot")).toBeInTheDocument();
    expect(screen.queryByLabelText("Best price")).not.toBeInTheDocument();
  });

  it("shows the schedule with an explanation when no sportsbook snapshot exists", async () => {
    const entry = boardGame();
    entry.market = { ...entry.market, snapshot_at: null, books: [], snapshot_books: [], consensus: null, best: {}, coverage: {}, movement: null };
    vi.mocked(fetchBoard).mockResolvedValue(board([entry], { snapshot_at: null, books_available: [] }));
    renderAt("/nfl");
    expect(await screen.findByRole("link", { name: "SEA @ DEN" })).toBeInTheDocument();
    expect(screen.getByText(/No sportsbook prices yet\. They arrive with the Tuesday/)).toBeInTheDocument();
    expect(screen.getByText("No sportsbook snapshot for this game yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Log / })).not.toBeInTheDocument();
  });

  it("locks a final game's saved prices: no log buttons, labelled as not current", async () => {
    const entry = boardGame();
    entry.game = { ...entry.game, status: "final", home_score: 20, away_score: 17 };
    vi.mocked(fetchBoard).mockResolvedValue(board([entry]));
    renderAt("/nfl");
    expect(await screen.findByText("Final · SEA 17–20 DEN")).toBeInTheDocument();
    expect(screen.getByText("Saved pregame prices, not current.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Log / })).not.toBeInTheDocument();
  });

  it("shows a book that does not quote a market as a dash, and counts only those that do", async () => {
    const entry = boardGame();
    entry.market.books = [
      book("DraftKings", "draftkings", 1.5, -110, -110, { total: null, over_price: null, under_price: null }),
      ...entry.market.books.slice(1),
    ];
    entry.market.coverage = { ...entry.market.coverage, total: { quoting: 3, at_line: 3 } };
    vi.mocked(fetchBoard).mockResolvedValue(board([entry]));
    renderAt("/nfl?market=total&game=1&panel=books");
    expect(await screen.findByText("3 of 3 books at 42.5.")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "total prices by book" });
    expect(within(within(table).getByRole("row", { name: /Over/ })).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("navigates to the next week and carries the market along", async () => {
    vi.mocked(fetchBoard).mockImplementation(async ({ week } = {}) => board([boardGame()], { week: Number(week ?? 6) }));
    renderAt("/nfl/2026/reg/6?market=total");
    await screen.findByRole("link", { name: "SEA @ DEN" });
    fireEvent.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() =>
      expect(fetchBoard).toHaveBeenLastCalledWith(expect.objectContaining({ season: "2026", seasonType: "reg", week: "7" })),
    );
    expect(screen.getByLabelText("location")).toHaveTextContent("/nfl/2026/reg/7?market=total");
  });

  it("disables previous on the first week", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board([boardGame()], { week: 5 }));
    renderAt("/nfl");
    await screen.findByRole("link", { name: "SEA @ DEN" });
    expect(screen.getByRole("button", { name: "Previous week" })).toBeDisabled();
  });

  it("says so when a week has no games loaded", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board([]));
    renderAt("/nfl");
    expect(await screen.findByText("No games loaded for this week yet.")).toBeInTheDocument();
  });

  it("links across to the NBA slate and the full odds board", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board());
    renderAt("/nfl");
    expect(await screen.findByRole("link", { name: "NBA" })).toHaveAttribute("href", "/nba");
    expect(screen.getByRole("link", { name: "NFL" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Full odds board" })).toHaveAttribute("href", "/odds");
  });

  it("shows the API error message", async () => {
    vi.mocked(fetchBoard).mockRejectedValue(new Error("Board request failed with status 503"));
    renderAt("/nfl");
    expect(await screen.findByRole("alert")).toHaveTextContent("503");
  });

  it("offers the paid refresh only when the server allows it, and says it spends credits", async () => {
    vi.mocked(fetchBoard).mockResolvedValue(board([boardGame()], { can_refresh: true }));
    renderAt("/nfl");
    expect(await screen.findByRole("button", { name: "Refresh prices (3 credits)" })).toBeInTheDocument();
  });
});

describe("weatherText", () => {
  const weather = { temp_f: 72.4, wind_mph: 3.2, precip_pct: 0, conditions: null, fetched_at: "2026-10-14T21:00:00Z" };

  it("says indoors for domes, whatever the forecast", () => {
    expect(weatherText({ roof: "dome", weather })).toBe("Indoors");
  });

  it("notes a retractable roof, with or without a forecast", () => {
    expect(weatherText({ roof: "retractable", weather })).toBe("72° · 3 mph wind (retractable roof)");
    expect(weatherText({ roof: "retractable", weather: null })).toBe("Retractable roof");
  });

  it("is blank when an outdoor game has no forecast yet", () => {
    expect(weatherText({ roof: "outdoors", weather: null })).toBeNull();
    expect(weatherText({ roof: null, weather: null })).toBeNull();
  });
});
