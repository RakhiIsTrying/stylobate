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
      page.getByText(/USD \(Equities & ETFs\)/).first(),
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
    // Let the watchlist fetch settle before deciding whether to create one
    await page.waitForLoadState("networkidle");

    // Only create a watchlist if we're in the empty state (avoids race where
    // an existing watchlist re-mounts the selector mid-click).
    const emptyState = page.getByText(/No watchlists yet/);
    if (await emptyState.isVisible({ timeout: 2000 }).catch(() => false)) {
      await page.getByRole("button", { name: /^\+ New$/ }).first().click();
      const nameInput = page.getByPlaceholder("Watchlist name");
      await nameInput.fill("Playwright Watchlist");
      await page.getByRole("button", { name: "Create", exact: true }).click();
      await expect(emptyState).toBeHidden({ timeout: 5000 });
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

  test("analyze portfolio button shows a snapshot section in chat", async ({ page }) => {
    // Set up: need at least one position so the strategist has data
    await page.waitForLoadState("networkidle");
    const emptyPositions = page.getByText(/No positions yet/);
    if (await emptyPositions.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Need to create a portfolio + position first
      const newBtn = page.getByRole("button", { name: /^\+ New$/ }).first();
      if (await newBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
        await newBtn.click();
        await page.getByPlaceholder("Portfolio name").fill("Playwright Analyze");
        await page.getByRole("button", { name: "Create", exact: true }).click();
        await expect(emptyPositions).toBeHidden({ timeout: 5000 });
      }
      await page.getByRole("button", { name: "+ Add position" }).click();
      await page.getByPlaceholder("Apple / RELIANCE.NS / BTC").fill("Apple");
      await page.getByRole("button", { name: "Resolve" }).click();
      await page.getByRole("button", { name: "Use this ticker" }).click();
      await page.getByLabel("Quantity").fill("10");
      await page.getByLabel(/Cost basis/).fill("150");
      await page.getByRole("button", { name: "Save position" }).click();
      await expect(page.getByText("AAPL")).toBeVisible({ timeout: 8000 });
    }

    // Click Analyze portfolio — navigates to /chat
    await page.getByRole("button", { name: "Analyze portfolio" }).click();
    await page.waitForURL("**/chat**", { timeout: 5000 });

    // Wait for the analysis to produce a section
    await expect(
      page.getByText(/Portfolio Snapshot|Returns vs Benchmark|Risk Metrics/),
    ).toBeVisible({ timeout: 60_000 });

    // Cleanup: nav back to portfolio and delete AAPL
    await page.goto("/portfolio");
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: /Delete/ }).first().click();
  });
});
