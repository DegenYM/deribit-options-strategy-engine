/**
 * Boot dashboard modules with mocked DOM/API and run renderDashboard once.
 * Usage: node scripts/dev/test_dashboard_boot.mjs
 */
import { STATE } from "../../frontend/src/shared/state.js";
import { renderDashboard } from "../../frontend/src/dashboard.js";
import * as domain from "../../frontend/src/modules/domain.js";

const els = new Map();
globalThis.window = globalThis;
globalThis.document = {
  readyState: "complete",
  addEventListener() {},
  querySelector(sel) {
    return els.get(sel) || null;
  },
  querySelectorAll(sel) {
    if (sel === "details.collapsible-section") return [];
    if (sel === ".chart-panel-canvas") return [];
    return [];
  },
  getElementById(id) {
    if (!els.has(id)) {
      els.set(id, {
        id,
        textContent: "",
        innerHTML: "",
        className: "",
        classList: { add() {}, remove() {}, toggle() {} },
        hidden: false,
        disabled: false,
        checked: true,
        style: {},
        dataset: {},
        addEventListener() {},
        querySelector() {
          return null;
        },
        querySelectorAll() {
          return [];
        },
        closest() {
          return null;
        },
        open: false,
        setAttribute() {},
        removeAttribute() {},
      });
    }
    return els.get(id);
  },
};
globalThis.requestAnimationFrame = (cb) => {
  cb();
  return 0;
};
globalThis.ResizeObserver = class {
  observe() {}
};
globalThis.Chart = class {
  constructor() {}
  destroy() {}
  resize() {}
  update() {}
};
globalThis.luxon = {
  DateTime: {
    now: () => ({
      toFormat: () => "12:00:00",
      toUTC: () => ({
        startOf: () => ({
          minus: () => ({ toMillis: () => Date.now() - 86400000 }),
        }),
      }),
    }),
    fromISO: () => ({ isValid: false }),
  },
};

const fixtures = {
  "/api/health": {
    ok: true,
    env: "mainnet",
    has_private_creds: true,
    accounts: [{ name: "naked_short", env_file: ".env.naked_short" }],
    scheduler_running: false,
  },
  "/api/spot": {
    BTC: 65000,
    ETH: 3500,
    price_change_pct_24h: { BTC: 1.2, ETH: -0.8 },
  },
  "/api/groups": { open: [], closed: [], underlying_index_usd: { BTC: 65000, ETH: 3500 } },
  "/api/dashboard_bundle?days=30": null,
  "/api/status": {
    portfolio: {
      total_equity_usdc: 10000,
      day_start_equity_usdc: 9900,
      day_drawdown_pct: 0.01,
      equity_by_book: { USDC: 10000 },
      regime: "normal",
    },
    trade_groups: [],
    positions: [],
    accounts: { USDC: { equity: 10000 } },
    account_statuses: [],
    underlying_index_usd: { BTC: 65000, ETH: 3500 },
  },
  "/api/realized_summary?days=30": {
    summary: {
      realized_pnl_usdc: 500,
      lifetime_realized_apr: 0.12,
      realized_win_rate: 0.6,
      avg_holding_days: 5,
      realized_closed_group_count: 3,
      window_days_used: 30,
      window_realized_pnl_usdc: 100,
      window_realized_apr: 0.08,
    },
    recent_closed_trades: [],
  },
  "/api/cumulative_pnl_series": { points: [] },
  "/api/apr_series?window_days=30": { points: [] },
  "/api/stress?shocks=0.1,0.2,0.3,0.4,0.5": {
    strategy_stresses: [],
    equity_usdc_by_book: { BTC: 0, ETH: 0, USDC: 10000 },
    scenarios: [],
  },
};

globalThis.fetch = async (url) => {
  const pathOnly = String(url).replace(/^https?:\/\/[^/]+/, "");
  const key = Object.keys(fixtures).find((k) => pathOnly === k || pathOnly.startsWith(`${k}&`));
  const exact = fixtures[pathOnly] ?? fixtures[key];
  if (exact === undefined && pathOnly.startsWith("/api/realized_summary")) {
    return { ok: true, status: 200, json: async () => fixtures["/api/realized_summary?days=30"] };
  }
  if (exact === undefined && pathOnly.startsWith("/api/apr_series")) {
    return { ok: true, status: 200, json: async () => fixtures["/api/apr_series?window_days=30"] };
  }
  if (exact === undefined && pathOnly.startsWith("/api/dashboard_bundle")) {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        groups: fixtures["/api/groups"],
        status: fixtures["/api/status"],
        realized_summary: fixtures["/api/realized_summary?days=30"],
      }),
    };
  }
  if (exact === null || exact === undefined) {
    throw new Error(`unexpected fetch ${pathOnly}`);
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => exact };
};

const errors = [];
try {
  const { initDashboard } = await import("../frontend/src/dashboard.js");
  initDashboard();
  await new Promise((r) => setTimeout(r, 1500));
} catch (err) {
  errors.push(String(err));
}

const agg = document.getElementById("aggregate-card")?.innerHTML || "";
const stillLoading = /Loading|skeleton-block/i.test(agg) && !/overview-metrics-grid|overview-metric-cell/i.test(agg);

if (errors.length) {
  console.error("errors:\n", errors.join("\n"));
  process.exit(1);
}
if (stillLoading) {
  console.error("aggregate still loading:", agg.slice(0, 200));
  process.exit(1);
}
console.log("boot OK; aggregate rendered", agg.slice(0, 80).replace(/\s+/g, " "));
