import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  easternDate,
  fetchDaySlate,
  slatePath,
  type DaySlateResponse,
  type SlateGame,
} from "../lib/slate";
import { NbaSlatePage } from "./NbaSlatePage";

vi.mock("../lib/slate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/slate")>();
  return { ...actual, fetchDaySlate: vi.fn() };
});

const line = {
  source: "espn", spread_home: -7.5, spread_home_price: -108, spread_away_price: -112,
  total: 228.5, over_price: -110, under_price: -110, ml_home: -300, ml_away: 250,
  captured_at: "2026-01-15T15:00:00Z",
};

function game(id: number, home: string, away: string, status: string, start: string): SlateGame {
  return {
    id, sport: "nba", espn_id: String(id), season: 2026, season_type: "REG", week: null,
    start_time: start, status,
    home: { id: id * 10, abbr: home, name: home }, away: { id: id * 10 + 1, abbr: away, name: away },
    home_score: status === "final" ? 118 : null, away_score: status === "final" ? 111 : null,
    venue: "Chase Center", roof: "dome", neutral: false,
    lines: status === "scheduled" ? { open: { ...line, spread_home: -6.5 }, current: line } : {},
    spread_move: status === "scheduled" ? -1 : null, total_move: null, odds_event_id: null, weather: null,
  };
}

function day(date: string, games: SlateGame[] = [
  game(1, "ORL", "MEM", "final", "2026-01-15T19:00:00Z"),
  game(4, "GSW", "NYK", "scheduled", "2026-01-16T03:00:00Z"),
]): DaySlateResponse {
  return { sport: "nba", date, previous: "2026-01-08", next: "2026-01-22", games };
}

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/nba" element={<><NbaSlatePage /><Where /></>} />
          <Route path="/nba/:date" element={<><NbaSlatePage /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NbaSlatePage", () => {
  afterEach(() => { cleanup(); vi.mocked(fetchDaySlate).mockReset(); });

  it("shows the day's games with their lines, scores and a link to each matchup", async () => {
    vi.mocked(fetchDaySlate).mockResolvedValue(day("2026-01-15"));
    renderAt("/nba");
    expect(await screen.findByRole("heading", { name: "Thursday, January 15, 2026" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "MEM @ ORL" })).toHaveAttribute("href", "/game/1");
    expect(screen.getByText("Final · MEM 111–118 ORL")).toBeInTheDocument();
    expect(screen.getByText("GSW -7.5")).toBeInTheDocument();  // the home favorite
    expect(screen.getByText("O/U 228.5")).toBeInTheDocument();
    expect(screen.getByText("Home line -1 since open")).toBeInTheDocument();
    expect(screen.queryByText("Indoors")).not.toBeInTheDocument();  // every arena is indoors
    expect(fetchDaySlate).toHaveBeenCalledWith(expect.objectContaining({ date: undefined }));
  });

  it("steps to the previous and next day that has games", async () => {
    vi.mocked(fetchDaySlate).mockImplementation(async ({ date } = {}) => day(date ?? "2026-01-15"));
    renderAt("/nba/2026-01-15");
    await screen.findByText("MEM @ ORL");
    fireEvent.click(screen.getByRole("button", { name: "Next game day" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/nba/2026-01-22"));
    fireEvent.click(await screen.findByRole("button", { name: "Previous game day" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/nba/2026-01-08"));
  });

  it("jumps to a picked date", async () => {
    vi.mocked(fetchDaySlate).mockImplementation(async ({ date } = {}) => day(date ?? "2026-01-15"));
    renderAt("/nba");
    await screen.findByText("MEM @ ORL");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-02-03" } });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/nba/2026-02-03"));
  });

  it("says so on a day with no games, and still offers the neighbours", async () => {
    vi.mocked(fetchDaySlate).mockResolvedValue(day("2026-01-18", []));
    renderAt("/nba/2026-01-18");
    expect(await screen.findByText("No games on this day.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next game day" })).toBeEnabled();
  });

  it("shows the API error message", async () => {
    vi.mocked(fetchDaySlate).mockRejectedValue(new Error("No NBA games loaded yet. Run nba_history."));
    renderAt("/nba");
    expect(await screen.findByRole("alert")).toHaveTextContent("nba_history");
  });
});

describe("slate helpers", () => {
  it("dates a game by its Eastern evening, not its UTC date", () => {
    expect(easternDate("2026-01-16T00:30:00Z")).toBe("2026-01-15");  // 7:30pm in New York
    expect(easternDate("2026-01-15T19:00:00Z")).toBe("2026-01-15");
    expect(easternDate("2026-07-01T02:00:00Z")).toBe("2026-06-30");  // daylight time
  });

  it("links back to the slate that holds the game", () => {
    const nba = game(4, "GSW", "NYK", "scheduled", "2026-01-16T03:00:00Z");
    expect(slatePath(nba)).toBe("/nba/2026-01-15");
    expect(slatePath({ ...nba, sport: "nfl", season: 2026, season_type: "REG", week: 6 })).toBe(
      "/nfl/2026/reg/6");
  });
});
