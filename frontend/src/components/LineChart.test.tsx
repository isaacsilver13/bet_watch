import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { errorDetail } from "../lib/http";
import { formatLine, formatPrice } from "../lib/lines";
import { LineChart } from "./LineChart";

describe("LineChart", () => {
  afterEach(cleanup);

  it("draws one point per snapshot and says where the number started and ended", () => {
    const { container } = render(
      <LineChart
        format={formatLine}
        label="DEN spread"
        points={[
          { at: "2026-10-13T15:00:00Z", value: 2.5 },
          { at: "2026-10-14T15:00:00Z", value: 2 },
          { at: "2026-10-15T15:00:00Z", value: 1.5 },
        ]}
      />,
    );
    expect(container.querySelectorAll("circle")).toHaveLength(3);
    expect(screen.getByRole("img", { name: "DEN spread trend" })).toBeInTheDocument();
    expect(screen.getByText("DEN spread: +2.5 → +1.5 over 3 snapshots")).toBeInTheDocument();
    const [first, last] = [...container.querySelectorAll("circle")];
    expect(Number(first.getAttribute("cy"))).toBeLessThan(Number(last.getAttribute("cy")));  // the line fell, so the point is lower
    expect(Number(first.getAttribute("cx"))).toBeLessThan(Number(last.getAttribute("cx")));
  });

  it("draws a flat line without dividing by zero, and needs two snapshots to draw at all", () => {
    const { container, rerender } = render(
      <LineChart label="Total" points={[{ at: "2026-10-13T15:00:00Z", value: 42.5 }, { at: "2026-10-14T15:00:00Z", value: 42.5 }]} />,
    );
    expect([...container.querySelectorAll("circle")].map((c) => c.getAttribute("cy"))).toEqual(["40.0", "40.0"]);
    rerender(<LineChart label="Total" points={[{ at: "2026-10-13T15:00:00Z", value: 42.5 }]} />);
    expect(screen.getByText("Total: not enough snapshots yet to draw a trend.")).toBeInTheDocument();
  });
});

describe("price and error formatting", () => {
  it("formats prices and a side's own spread", () => {
    expect(formatPrice(105)).toBe("+105");
    expect(formatPrice(-110)).toBe("-110");
    expect(formatPrice(null)).toBe("—");
    expect(formatLine(1.5)).toBe("+1.5");
    expect(formatLine(-3)).toBe("-3");
    expect(formatLine(0)).toBe("PK");
    expect(formatLine(undefined)).toBe("—");
  });

  it("reads FastAPI error bodies, falling back when there is none", async () => {
    const json = (body: unknown, status = 422) => new Response(JSON.stringify(body), { status });
    expect(await errorDetail(json({ detail: "Game not found" }, 404), "x")).toBe("Game not found");
    expect(await errorDetail(json({ detail: [{ msg: "Value error, a spread bet needs its line" }, { msg: "Input should be greater than 0" }] }), "x"))
      .toBe("Value error, a spread bet needs its line; Input should be greater than 0");
    expect(await errorDetail(new Response("Bad gateway", { status: 502 }), "fallback")).toBe("fallback");
    expect(await errorDetail(json({ detail: [] }), "fallback")).toBe("fallback");
  });
});
