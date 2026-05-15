import type { Page } from "@playwright/test";

/**
 * Inject a Supabase session into localStorage so the page treats
 * the user as logged in. The session JSON must be obtained out-of-band
 * (e.g., via the Supabase password-grant endpoint) and exported as
 * PLAYWRIGHT_SESSION_JSON.
 *
 * The localStorage key for @supabase/ssr's createBrowserClient is
 * `sb-<project-ref>-auth-token` by default.
 */
export async function loginAsTestUser(page: Page): Promise<void> {
  const sessionJson = process.env.PLAYWRIGHT_SESSION_JSON;
  if (!sessionJson) {
    throw new Error("PLAYWRIGHT_SESSION_JSON env var must be set");
  }
  const projectRef =
    process.env.PLAYWRIGHT_SUPABASE_PROJECT_REF ?? "pvjamgocmmldfpzzswaj";
  const storageKey = `sb-${projectRef}-auth-token`;
  await page.addInitScript(
    ({ key, value }) => {
      window.localStorage.setItem(key, value);
    },
    { key: storageKey, value: sessionJson },
  );
}
