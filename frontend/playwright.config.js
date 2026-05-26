import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:8765",
    trace: "on-first-retry",
  },
  webServer: process.env.DASHBOARD_BASE_URL
    ? undefined
    : {
        command:
          "python ../scripts/run_e2e_dashboard.py --host 127.0.0.1 --port 8765",
        url: "http://127.0.0.1:8765/api/health",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
