import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { board } from "../test/boardFixtures";
import { SlatePage } from "./SlatePage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("SlatePage network behaviour", () => {
  it("only reads: loading, switching market, filtering and expanding never reach the refresh endpoint", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      const body = url.includes("/api/v1/board") ? board() : url.includes("/context") ? {} : {};
      return new Response(JSON.stringify(body), { status: url.includes("/context") ? 503 : 200 });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/nfl"]}>
          <Routes><Route path="/nfl" element={<SlatePage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole("link", { name: "SEA @ DEN" });
    fireEvent.click(screen.getByRole("tab", { name: "Total" }));
    fireEvent.change(screen.getByLabelText("Team"), { target: { value: "DEN" } });
    fireEvent.click(screen.getByText(/Books \(4 selected\)/));
    fireEvent.click(screen.getByRole("checkbox", { name: "BetMGM" }));
    fireEvent.click(screen.getByRole("button", { name: "Research matchup" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/context"))).toBe(true));

    expect(calls.every((call) => call.method === "GET")).toBe(true);
    expect(calls.some((call) => /refresh/.test(call.url))).toBe(false);
    expect(calls.every((call) => /\/api\/v1\/(board|games\/\d+\/context)/.test(call.url))).toBe(true);
  });
});
