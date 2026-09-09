/**
 * Headless JS unit-test runner for the dashboard modules (frontend/src/modules/*.js).
 *
 * Runs each listed `scripts/dev/test_*.mjs` in a child Node process and fails if any
 * exits non-zero. Only true unit tests belong here (plain `node:assert`, no server,
 * process exits on its own). Manual boot scripts (test_dashboard_boot.mjs,
 * test_dashboard_investor_boot.mjs) keep timers alive and are intentionally excluded.
 *
 * Usage:  node scripts/dev/run_unit_tests.mjs        (or `npm run test:unit` in frontend/)
 * Works on Node >= 16 (does not rely on `node --test`).
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

const UNIT_TESTS = [
  "test_profit_disposition.mjs",
  "test_open_group_dedupe.mjs",
  "test_overview_equity.mjs",
];

let failed = 0;
for (const name of UNIT_TESTS) {
  const file = path.join(here, name);
  const started = Date.now();
  const result = spawnSync(process.execPath, [file], { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" });
  const ms = Date.now() - started;
  if (result.status === 0) {
    console.log(`ok   ${name} (${ms} ms)`);
  } else {
    failed += 1;
    console.log(`FAIL ${name} (exit ${result.status ?? result.signal}, ${ms} ms)`);
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
  }
}

console.log(`\n${UNIT_TESTS.length - failed}/${UNIT_TESTS.length} unit test files passed`);
process.exit(failed === 0 ? 0 : 1);
