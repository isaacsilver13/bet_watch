import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchSlate, weatherText, type SlateResponse } from "../lib/slate";
import { SlatePage } from "./SlatePage";

vi.mock("../lib/slate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/slate")>();
  return { ...actual, fetchSlate: vi.fn() };
});

const line = (spread: number, total: number) => ({
  source: "espn", spread_home: spread, spread_home_price: -110, spread_away_price: -110,
  total, over_price: -110, under_price: -110, ml_home: 102, ml_away: -122,
  captured_at: "2026-10-11T15:00:00Z",
});

function slate(week: number, withGames = true): SlateResponse {
  return {
    sport: "nfl", season: 2026, season_type: "REG", week,
    weeks: [5, 6, 7].map((w) => ({ season_type: "REG", week: w })),
    games: withGames ? [{
      id: 1, espn_id: "401872995", season: 2026, season_type: "REG", week,
      start_time: "2026-10-16T00:15:00Z", status: "scheduled",
      home: { id: 10, abbr: "DEN", name: "Denver Broncos" },
      away: { id: 26, abbr: "SEA", name: "Seattle Seahawks" },
      home_score: null, away_score: null, venue: "Empower Field at Mile High", roof: "outdoors", neutral: false,
      lines: { open: line(2.5, 43.5), current: line(1.5, 42.5) },
      spread_move: -1, total_move: -1, odds_event_id: null,
      weather: { temp_f: 55.4, wind_mph: 11.8, precip_pct: 60, conditions: "Overcast", fetched_at: "2026-10-14T21:00:00Z" },
    }] : [],
  };
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/nfl" element={<SlatePage />} />
          <Route path="/nfl/:season/:seasonType/:week" element={<SlatePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SlatePage", () => {
  afterEach(() => { cleanup(); vi.mocked(fetchSlate).mockReset(); });

  it("shows each game's current line, opener and movement", async () => {
    vi.mocked(fetchSlate).mockResolvedValue(slate(6));
    renderAt("/nfl");
    expect(await screen.findByText("SEA @ DEN")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2026 · Week 6" })).toBeInTheDocument();
    expect(screen.getByText("SEA -1.5")).toBeInTheDocument();
    expect(screen.getByText("O/U 42.5")).toBeInTheDocument();
    expect(screen.getByText("SEA -2.5 · 43.5")).toBeInTheDocument();
    expect(screen.getByText("Home line -1 since open")).toBeInTheDocument();
    expect(screen.getByText("55° · 12 mph wind · Overcast")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SEA @ DEN" })).toHaveAttribute("href", "/game/1");
  });

  it("navigates to the next week", async () => {
    vi.mocked(fetchSlate).mockImplementation(async ({ week } = {}) => slate(Number(week ?? 6)));
    renderAt("/nfl/2026/reg/6");
    await screen.findByText("SEA @ DEN");  // buttons stay disabled until the week loads
    fireEvent.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() =>
      expect(fetchSlate).toHaveBeenLastCalledWith(
        expect.objectContaining({ season: "2026", seasonType: "reg", week: "7" }),
      ),
    );
  });

  it("says so when a week has no games loaded", async () => {
    vi.mocked(fetchSlate).mockResolvedValue(slate(6, false));
    renderAt("/nfl");
    expect(await screen.findByText("No games loaded for this week yet.")).toBeInTheDocument();
  });

  it("shows the API error message", async () => {
    vi.mocked(fetchSlate).mockRejectedValue(new Error("Slate request failed with status 503"));
    renderAt("/nfl");
    expect(await screen.findByRole("alert")).toHaveTextContent("503");
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
