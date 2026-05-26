import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const repoRoot = path.resolve(fileURLToPath(new URL(".", import.meta.url)), "..");
const launcher = path.join(repoRoot, "scripts", "run_e2e_dashboard.py");

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
        command: `python3 "${launcher}" --host 127.0.0.1 --port 8765`,
        url: "http://127.0.0.1:8765/api/health",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
