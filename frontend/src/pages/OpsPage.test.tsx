import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchHealth, type OpsHealth } from "../lib/ops";
import { OpsPage } from "./OpsPage";

vi.mock("../lib/ops", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/ops")>();
  return { ...actual, fetchHealth: vi.fn() };
});

const HEALTHY: OpsHealth = {
  generated_at: "2026-10-15T16:00:00Z", run_due_last_at: "2026-10-15T15:50:00Z", run_due_stale: false,
  jobs: [{ job: "odds_snapshot", slot_key: "odds_snapshot:nfl:2026-10-15:thu", state: "done", attempts: 1, updated_at: "2026-10-15T15:05:00Z" }],
  slots_today: { done: 4, missed: 1 }, credits_remaining: 471,
  snapshot_coverage: { targeted: 2, complete: 1, books: { DraftKings: { moneyline: 2, spread: 2, total: 1 } } },
  unlinked_events: [], stuck_raw_saved: [], finals_missing_close: 0,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}><MemoryRouter><OpsPage /></MemoryRouter></QueryClientProvider>,
  );
}

describe("OpsPage", () => {
  afterEach(() => {
    cleanup();
    vi.mocked(fetchHealth).mockReset();
  });

  it("shows the jobs, credits and book coverage when nothing is wrong", async () => {
    vi.mocked(fetchHealth).mockResolvedValue(HEALTHY);
    renderPage();
    expect(await screen.findByText("Nothing needs attention.")).toBeInTheDocument();
    expect(screen.getByText("471")).toBeInTheDocument();
    expect(screen.getByText("4 done · 1 missed")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Last snapshot: 1 of 2 games fully covered" })).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /DraftKings/ });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["2 games", "2 games", "1 games"]);
    expect(screen.getByRole("row", { name: /odds_snapshot done/ })).toBeInTheDocument();
  });

  it("lists everything that needs attention", async () => {
    vi.mocked(fetchHealth).mockResolvedValue({
      ...HEALTHY, run_due_stale: true, stuck_raw_saved: ["odds_snapshot:nfl:2026-10-14:sun"],
      unlinked_events: ["event:ghost"], finals_missing_close: 2,
    });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("run-due has not fired since");
    expect(alert).toHaveTextContent("1 paid response(s) saved but not parsed");
    expect(alert).toHaveTextContent("1 odds event(s) match no game: event:ghost");
    expect(alert).toHaveTextContent("2 finished game(s) still have no ESPN close");
  });

  it("shows the API error", async () => {
    vi.mocked(fetchHealth).mockRejectedValue(new Error("Health request failed with status 503"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("status 503");
  });
});
