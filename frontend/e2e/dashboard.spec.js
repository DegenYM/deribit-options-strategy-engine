import { expect, test } from "@playwright/test";

test.describe("dashboard smoke", () => {
  test("ops dashboard page loads", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page).toHaveTitle(/Deribit Strategy Dashboard/i);
    await expect(page.locator("#aggregate-card")).toBeVisible();
    await expect(page.locator('script[src*="app.js"]')).toHaveCount(1);
  });

  test("investor summary page loads", async ({ page }) => {
    const response = await page.goto("/investor.html");
    expect(response?.ok()).toBeTruthy();
    await expect(page.locator("#aggregate-card")).toBeVisible();
  });

  test("health endpoint responds", async ({ request }) => {
    const response = await request.get("/api/health");
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload).toHaveProperty("env");
    expect(payload).toHaveProperty("server_time_ms");
    expect(payload).toHaveProperty("has_private_creds");
  });

  test("dashboard bundle endpoint responds", async ({ request }) => {
    const response = await request.get("/api/dashboard_bundle?days=7");
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload).toHaveProperty("status");
    expect(payload).toHaveProperty("groups");
    expect(payload).toHaveProperty("realized_summary");
  });
});
