import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchContext, fetchGame, formatMetric, formatTally, isStale, METRIC_ROWS,
  type ContextResponse, type RecordSet, type TeamContext,
} from "../lib/matchup";
import type { SlateGame } from "../lib/slate";
import { MatchupPage } from "./MatchupPage";

vi.mock("../lib/matchup", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/matchup")>();
  return { ...actual, fetchGame: vi.fn(), fetchContext: vi.fn() };
});

const tally = (win: number, loss: number, push = 0) => ({ win, loss, push });
const record = (su: [number, number], ats: [number, number], ou: [number, number, number]): RecordSet => ({
  su: tally(...su), ats: tally(...ats), ou: tally(...ou),
});

function team(id: number, abbr: string, extra: Partial<TeamContext> = {}): TeamContext {
  return {
    team: { id, abbr, name: abbr },
    records: {
      season: record([2, 1], [2, 1], [2, 0, 1]), last_n: record([1, 1], [1, 1], [1, 1, 0]),
      home: record([1, 0], [1, 0], [0, 1, 0]), away: record([1, 1], [0, 2], [2, 0, 0]),
      favorite: record([1, 1], [1, 1], [1, 1, 0]), underdog: record([1, 0], [1, 0], [1, 0, 0]),
    },
    efficiency: { off_epa_play: { value: 0.104, rank: 1, of: 32 }, def_epa_play: { value: -0.04, rank: 3, of: 32 } },
    situation: {
      rest_days: 4, short_week: true, off_bye: false, travel_miles: 1018, tz_crossed: 1,
      previous_opponent: "ARI",
    },
    injuries: [], qb_flag: null,
    ...extra,
  };
}

const GAME: SlateGame = {
  id: 7, espn_id: "401872995", season: 2026, season_type: "REG", week: 6,
  start_time: "2026-10-16T00:15:00Z", status: "scheduled",
  home: { id: 10, abbr: "DEN", name: "Denver Broncos" }, away: { id: 26, abbr: "SEA", name: "Seattle Seahawks" },
  home_score: null, away_score: null, venue: "Empower Field at Mile High", roof: "outdoors", neutral: false,
  lines: { current: {
    source: "espn", spread_home: -1.5, spread_home_price: -110, spread_away_price: -110, total: 42.5,
    over_price: -110, under_price: -110, ml_home: null, ml_away: null, captured_at: "2026-10-14T21:00:00Z",
  } },
  spread_move: null, total_move: null, odds_event_id: null,
  weather: { temp_f: 55.4, wind_mph: 11.8, precip_pct: 60, conditions: "Overcast", fetched_at: "2026-10-14T21:00:00Z" },
};

function context(overrides: Partial<ContextResponse> = {}): ContextResponse {
  return {
    game_id: 7, last_n: 5, divisional: false, primetime: true,
    home: team(10, "DEN", {
      injuries: [{ player: "Bo Nix", position: "QB", status: "Questionable", detail: "Ankle", reported_at: null }],
      qb_flag: { player: "Bo Nix", status: "Questionable" },
    }),
    away: team(26, "SEA"),
    head_to_head: {
      meetings: [{
        game_id: 1, start_time: "2025-12-14T18:00:00Z", home: "DEN", away: "SEA",
        home_score: 20, away_score: 17, spread_home: 2.5, total: 40,
      }],
      home_su: tally(1, 0), home_ats: tally(1, 0),
    },
    data_as_of: {
      records: new Date().toISOString(), efficiency: new Date().toISOString(),
      situational: new Date().toISOString(), injuries: new Date().toISOString(),
    },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/game/7"]}>
        <Routes><Route path="/game/:id" element={<MatchupPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MatchupPage", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(fetchGame).mockReset();
    vi.mocked(fetchContext).mockReset();
  });

  it("shows the header with line and weather, and the records tab first", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    expect(await screen.findByRole("heading", { name: "Seattle Seahawks at Denver Broncos" })).toBeInTheDocument();
    expect(screen.getByText("DEN -1.5")).toBeInTheDocument();
    expect(screen.getByText("55° · 12 mph wind · Overcast")).toBeInTheDocument();
    expect((await screen.findAllByText("2-1 SU · 2-1 ATS · 2-0-1 O/U")).length).toBe(2);
    expect(screen.getByRole("link", { name: "Back to slate" })).toHaveAttribute("href", "/nfl/2026/reg/6");
  });

  it("shows efficiency with league ranks", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Efficiency" }));
    expect((await screen.findAllByText("+0.104 (#1 of 32)")).length).toBe(2);
    expect(screen.getAllByText("-0.040 (#3 of 32)").length).toBe(2);
    expect(screen.queryByText("Yards / play")).not.toBeInTheDocument();  // not in the data
  });

  it("flags a starting quarterback on the injury report", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Injuries" }));
    expect(await screen.findByRole("status")).toHaveTextContent("DEN's last starting QB, Bo Nix, is listed Questionable.");
    expect(screen.getByText(/Ankle/)).toBeInTheDocument();
  });

  it("shows the short week and travel on the situation tab", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Situation" }));
    expect((await screen.findAllByText("1018 mi")).length).toBe(2);
    expect(screen.getAllByText("Yes").length).toBe(2);  // both short weeks in this fixture
    expect(screen.getByText(/Primetime kickoff/)).toBeInTheDocument();
  });

  it("lists earlier meetings with the closing spread", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Head-to-head" }));
    expect(await screen.findByText("SEA 17 @ DEN 20")).toBeInTheDocument();
    expect(screen.getByText("DEN +2.5")).toBeInTheDocument();
  });

  it("refetches the context when the recent-games window changes", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context());
    renderPage();
    await screen.findAllByText("2-1 SU · 2-1 ATS · 2-0-1 O/U");
    fireEvent.change(screen.getByLabelText("Recent games"), { target: { value: "3" } });
    await waitFor(() => expect(fetchContext).toHaveBeenLastCalledWith("7", 3, expect.anything()));
  });

  it("marks stale panels and says when nothing is loaded", async () => {
    vi.mocked(fetchGame).mockResolvedValue(GAME);
    vi.mocked(fetchContext).mockResolvedValue(context({
      data_as_of: { records: null, efficiency: null, situational: null, injuries: "2020-01-01T00:00:00Z" },
    }));
    renderPage();
    expect(await screen.findByText("No data loaded yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Injuries" }));
    expect(await screen.findByText(/· stale/)).toBeInTheDocument();
  });

  it("shows the API error", async () => {
    vi.mocked(fetchGame).mockRejectedValue(new Error("Game request failed with status 404"));
    vi.mocked(fetchContext).mockRejectedValue(new Error("Context request failed with status 404"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("404");
  });
});

describe("matchup helpers", () => {
  it("formats tallies, metrics and staleness", () => {
    expect(formatTally(tally(2, 1))).toBe("2-1");
    expect(formatTally(tally(2, 1, 1))).toBe("2-1-1");
    const row = METRIC_ROWS.find((entry) => entry.key === "points_for_pg")!;
    expect(formatMetric({ value: 24.333, rank: 4, of: 32 }, row)).toBe("24.3 (#4 of 32)");
    expect(formatMetric(undefined, row)).toBe("—");
    const now = Date.parse("2026-10-14T12:00:00Z");
    expect(isStale("2026-10-12T23:00:00Z", "injuries", now)).toBe(true);  // 37h old, limit 36h
    expect(isStale("2026-10-14T00:00:00Z", "injuries", now)).toBe(false);
    expect(isStale("2026-10-07T00:00:00Z", "records", now)).toBe(false);  // 8-day limit
    expect(isStale(null, "injuries", now)).toBe(false);
  });
});
