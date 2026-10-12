import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchOdds } from "./api";

describe("fetchOdds", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("surfaces an ignored protected refresh", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ generated_at: "2026-10-08T00:00:00Z", provider_status: {}, provider_details: {}, events: [] }),
      { status: 200, headers: { "Content-Type": "application/json", "X-Refresh-Ignored": "unauthorized" } },
    )));

    const result = await fetchOdds("nfl", { forceRefresh: true });

    expect(result.refreshIgnored).toBe("unauthorized");
    expect(result.data.events).toEqual([]);
  });
});
