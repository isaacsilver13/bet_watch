import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PredictionContract } from "../types";
import { PredictionPanel } from "./PredictionMarkets";

const contract: PredictionContract = {
  venue: "kalshi",
  contract_id: "KX-TEAM",
  source_event_id: "KX-GAME",
  title: "Team wins",
  outcome: "Team",
  url: "https://kalshi.com/markets/kxgame",
  rules: "If Team wins, resolves to Yes.",
  rules_primary: "If Team wins, resolves to Yes.",
  rules_secondary: "A tie settles at 0.50.",
  probability: 0.51,
  last_price: 0.51,
  best_bid: 0.5,
  best_ask: 0.52,
  midpoint: 0.51,
  spread: 0.02,
  bid_size: 20,
  ask_size: 25,
  volume: 100,
  open_interest: 40,
  liquidity: null,
  status: "active",
  close_time: "2026-10-09T00:00:00Z",
  metadata_updated_at: "2026-10-08T00:00:00Z",
  observed_at: "2026-10-08T00:05:00Z",
  match_status: "matched",
  match_reason: "teams and date agree",
  event_id: "event:1",
  matched_selection: "Team",
};

describe("PredictionPanel", () => {
  it("labels last trade separately from executable bid and ask", () => {
    render(<PredictionPanel contracts={[contract]} heading="Game winner contracts" />);

    expect(screen.getByText("Last trade")).toBeInTheDocument();
    expect(screen.getByText("Bid")).toBeInTheDocument();
    expect(screen.getByText("Ask")).toBeInTheDocument();
    expect(screen.queryByText(/market probability/i)).not.toBeInTheDocument();
  });
});
