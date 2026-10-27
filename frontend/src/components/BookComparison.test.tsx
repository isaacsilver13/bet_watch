import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { comparisonLine, isBestQuote, quoteCell, MARKET_SIDES } from "../lib/board";
import { boardGame } from "../test/boardFixtures";
import { BookComparison, coverageText } from "./BookComparison";

afterEach(cleanup);
const entry = boardGame();
const [away, home] = MARKET_SIDES.spread;

describe("same-line comparison", () => {
  it("compares spreads from each side's point of view", () => {
    expect(comparisonLine(home, entry.market.consensus)).toBe(1.5);
    expect(comparisonLine(away, entry.market.consensus)).toBe(-1.5);
    expect(comparisonLine(MARKET_SIDES.moneyline[0], entry.market.consensus)).toBeNull();
  });

  it("never ranks a quote at another number, however good its price", () => {
    const mgm = entry.market.books.find((b) => b.book === "BetMGM")!;
    const cell = quoteCell(mgm, home, entry.market.consensus);
    expect(cell).toMatchObject({ line: 2.5, price: 300, comparable: false });
    expect(isBestQuote(cell, { book: "BetMGM", venue: "betmgm", price: 300, line: 2.5 })).toBe(false);
  });

  it("ranks moneylines directly, with no number to differ on", () => {
    const dk = entry.market.books[0];
    expect(quoteCell(dk, MARKET_SIDES.moneyline[1], entry.market.consensus).comparable).toBe(true);
  });

  it("says when there is no common line", () => {
    const none = { ...entry.market, consensus: { ...entry.market.consensus!, spread_home: null } };
    expect(coverageText(none, "spread", entry.game)).toBe("No common line to compare.");
    expect(coverageText({ ...none, coverage: { spread: { quoting: 0, at_line: 0 } } }, "spread", entry.game)).toBe("No book quotes this market yet.");
  });
});

describe("BookComparison", () => {
  it("labels each book's observation time and tags the other number instead of ranking it", () => {
    const onPick = vi.fn();
    render(<BookComparison actionable data={entry.market} game={entry.game} market="spread" onPick={onPick} snapshotAt={entry.market.snapshot_at} />);
    const table = screen.getByRole("table", { name: "spread prices by book" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5);  // selection + 4 books
    expect(within(table).getAllByText("Best")).toHaveLength(2);  // one per side, both at the shared number
    expect(within(table).getByRole("button", { name: "Log BetMGM DEN +2.5 +300" })).toHaveTextContent("Other number");
    fireEvent.click(within(table).getByRole("button", { name: "Log FanDuel DEN +1.5 -108" }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({
      venue: "fanduel", market: "spread", selection: "home", line: 1.5, price: -108,
      quote: expect.objectContaining({ book: "FanDuel", actionable: true }),
    }));
  });

  it("offers no log buttons when the game is not actionable", () => {
    render(<BookComparison actionable={false} data={entry.market} game={entry.game} market="spread" onPick={vi.fn()} snapshotAt={entry.market.snapshot_at} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
