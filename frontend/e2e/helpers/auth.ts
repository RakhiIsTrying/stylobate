import type { Page } from "@playwright/test";

/**
 * Drive the sign-in form to log in. Sets the Supabase auth cookies so the
 * proxy.ts middleware lets subsequent navigations through.
 *
 * Requires:
 *   PLAYWRIGHT_TEST_EMAIL    — test user email
 *   PLAYWRIGHT_TEST_PASSWORD — test user password
 */
export async function loginAsTestUser(page: Page): Promise<void> {
  const email = process.env.PLAYWRIGHT_TEST_EMAIL;
  const password = process.env.PLAYWRIGHT_TEST_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "PLAYWRIGHT_TEST_EMAIL and PLAYWRIGHT_TEST_PASSWORD must be set",
    );
  }
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  // sign-in redirects to /chat once cookies are set
  await page.waitForURL("**/chat", { timeout: 10_000 });
}
