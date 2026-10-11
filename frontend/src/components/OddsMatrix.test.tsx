import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EventComparison, Offer } from "../types";
import { OddsMatrix } from "./OddsMatrix";

const baseOffer: Offer = {
  event_id: "event:1",
  provider: "fixture",
  bookmaker: "Book A",
  market_type: "moneyline",
  selection: "Home Team",
  line: null,
  price_american: 100,
  price_decimal: 2,
  player_name: null,
  provider_offer_id: null,
  provider_updated_at: null,
  observed_at: "2026-10-08T00:00:00Z",
  deep_link: null,
  status: "available",
};

const comparison: EventComparison = {
  event: {
    id: "event:1",
    sport: "nfl",
    league: "NFL",
    home_team: "Home Team",
    away_team: "Away Team",
    start_time: "2026-10-09T00:00:00Z",
  },
  offers: [baseOffer, { ...baseOffer, bookmaker: "Book B", price_american: 110, price_decimal: 2.1 }],
};

describe("OddsMatrix", () => {
  it("highlights the best sportsbook price for an exact selection", () => {
    render(<OddsMatrix comparison={comparison} mobileMarket="moneyline" />);

    expect(screen.getByText("+110").parentElement).toHaveClass("best-price");
    expect(screen.getByRole("rowheader", { name: "Moneyline" })).toBeInTheDocument();
  });
});
