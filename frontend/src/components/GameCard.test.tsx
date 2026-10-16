import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { betsText, starBadges, type SlateGame } from "../lib/slate";
import { GameCard } from "./GameCard";

const GAME: SlateGame = {
  id: 4, sport: "nba", espn_id: "4", season: 2026, season_type: "REG", week: null,
  start_time: "2026-01-16T00:30:00Z", status: "scheduled",
  home: { id: 14, abbr: "MIA", name: "Miami Heat" }, away: { id: 2, abbr: "BOS", name: "Boston Celtics" },
  home_score: null, away_score: null, venue: "Kaseya Center", roof: "dome", neutral: false,
  lines: {}, spread_move: null, total_move: null, odds_event_id: null, weather: null,
};

function renderCard(game: SlateGame) {
  return render(<MemoryRouter><GameCard game={game} /></MemoryRouter>);
}

describe("GameCard badges", () => {
  afterEach(cleanup);

  it("flags a team whose star is out and one whose star is questionable", () => {
    renderCard({
      ...GAME,
      star: {
        home: { out: ["Bam Adebayo"], questionable: [] },
        away: { out: [], questionable: ["Jaylen Brown"] },
      },
    });
    expect(screen.getByText("★ MIA out")).toHaveAttribute("title", "Bam Adebayo");
    expect(screen.getByText("★ BOS questionable")).toHaveAttribute("title", "Jaylen Brown");
  });

  it("shows no star badge when nothing is flagged", () => {
    renderCard(GAME);
    expect(screen.queryByText(/★/)).not.toBeInTheDocument();
    renderCard({ ...GAME, star: null });
    expect(screen.queryByText(/★/)).not.toBeInTheDocument();
  });

  it("links the bet badge to the bet log", () => {
    renderCard({ ...GAME, bets: { total: 3, open: 1, net: 4.5 } });
    expect(screen.getByRole("link", { name: "3 bets · 1 open · +$4.50" })).toHaveAttribute("href", "/bets");
  });

  it("shows no bet badge for a game without bets", () => {
    renderCard(GAME);
    expect(screen.queryByRole("link", { name: /bet/ })).not.toBeInTheDocument();
  });
});

describe("badge text", () => {
  it("lists out before questionable, away team first", () => {
    const star = {
      home: { out: ["A"], questionable: ["B"] },
      away: { out: [], questionable: ["C"] },
    };
    expect(starBadges({ ...GAME, star })).toEqual([
      { team: "BOS", kind: "questionable", names: ["C"] },
      { team: "MIA", kind: "out", names: ["A"] },
      { team: "MIA", kind: "questionable", names: ["B"] },
    ]);
  });

  it("words bets and profit", () => {
    expect(betsText({ total: 1, open: 0, net: null })).toBe("1 bet · 0 open");
    expect(betsText({ total: 2, open: 0, net: -5.5 })).toBe("2 bets · 0 open · -$5.50");
  });
});
