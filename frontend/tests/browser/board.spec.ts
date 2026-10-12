import { expect, test } from "@playwright/test";

test("fixture board exposes the comparison table and matched Kalshi contract", async ({ page }) => {
  await page.goto("/odds?sport=nfl&market=moneyline");

  await expect(page.getByRole("heading", { name: "Lineup" })).toBeVisible();
  await expect(page.getByRole("button", { name: "NFL" })).toHaveClass(/active/);
  await expect(page.getByRole("tab", { name: "Moneyline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("columnheader", { name: "DraftKings" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Game winner contracts" })).toBeVisible();
  await expect(page.getByText("Last trade").first()).toBeVisible();
  await expect(page.getByText("Bid", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Ask", { exact: true }).first()).toBeVisible();

  const pageOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(pageOverflows).toBe(false);

  await page.getByRole("tab", { name: "Total" }).focus();
  await expect(page.getByRole("tab", { name: "Total" })).toBeFocused();
});
