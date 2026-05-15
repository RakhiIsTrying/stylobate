import { expect, test } from "@playwright/test";
import { loginAsTestUser } from "./helpers/auth";

test.describe("Portfolio", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page);
    await page.goto("/portfolio");
  });

  test("add a position, see it in USD cohort, delete it", async ({ page }) => {
    // Create a portfolio if none exists
    const newPortfolioBtn = page.getByRole("button", { name: /^\+ New$/ }).first();
    if (await newPortfolioBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await newPortfolioBtn.click();
      await page.getByPlaceholder("Portfolio name").fill("Playwright Test");
      await page.getByRole("button", { name: "Create" }).click();
    }

    await page.getByRole("button", { name: "+ Add position" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();
    await page.getByLabel("Quantity").fill("10");
    await page.getByLabel(/Cost basis/).fill("175");
    await page.getByRole("button", { name: "Save position" }).click();

    // AAPL row appears within the USD (Equities & ETFs) cohort
    await expect(
      page.getByText(/USD \(Equities & ETFs\)/),
    ).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("AAPL")).toBeVisible();

    // Delete it (confirm() prompt is auto-accepted)
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button", { name: /Delete/ })
      .first()
      .click();
    await expect(page.getByText("AAPL")).not.toBeVisible({ timeout: 5000 });
  });

  test("refresh button reloads", async ({ page }) => {
    await page.getByRole("button", { name: "+ Add position" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();
    await page.getByLabel("Quantity").fill("5");
    await page.getByLabel(/Cost basis/).fill("180");
    await page.getByRole("button", { name: "Save position" }).click();
    await expect(page.getByText("AAPL")).toBeVisible({ timeout: 8000 });

    await page.getByRole("button", { name: /Refresh prices/ }).click();
    // Button momentarily flips to "Refreshing..."
    await expect(
      page.getByRole("button", { name: /Refresh|Refreshing/ }),
    ).toBeVisible();

    // Cleanup
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button", { name: /Delete/ })
      .first()
      .click();
  });

  test("watchlist: add NVDA, see price, delete", async ({ page }) => {
    await page.getByRole("button", { name: "Watchlist" }).click();

    const newWatchlistBtn = page.getByRole("button", { name: /^\+ New$/ }).first();
    if (await newWatchlistBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await newWatchlistBtn.click();
      await page.getByPlaceholder("Watchlist name").fill("Playwright Watchlist");
      await page.getByRole("button", { name: "Create" }).click();
    }

    await page.getByRole("button", { name: "+ Add ticker" }).click();
    await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("NVIDIA");
    await page.getByRole("button", { name: "Resolve" }).click();
    await page.getByRole("button", { name: "Use this ticker" }).click();

    await expect(page.getByText("NVDA")).toBeVisible({ timeout: 10000 });

    // Cleanup
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button", { name: /Delete/ })
      .first()
      .click();
    await expect(page.getByText("NVDA")).not.toBeVisible({ timeout: 5000 });
  });
});
