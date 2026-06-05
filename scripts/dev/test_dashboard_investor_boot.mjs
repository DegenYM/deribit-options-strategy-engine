/**
 * Boot dashboard modules with mocked DOM/API (investor mode).
 * Usage: node scripts/dev/test_dashboard_investor_boot.mjs
 */
process.env.DASHBOARD_TEST_INVESTOR = "1";

globalThis.window = globalThis;
globalThis.window.__DASHBOARD_MODE__ = "investor";
globalThis.window.__INVESTOR_LOCALE__ = "zh-Hant";

const els = new Map();
globalThis.document = {
  readyState: "complete",
  body: {
    classList: { add() {}, remove() {}, toggle() {} },
  },
  addEventListener() {},
  querySelector(sel) {
    if (sel.startsWith("[data-investor-load-")) {
      return { textContent: "" };
    }
    return els.get(sel) || null;
  },
  querySelectorAll(sel) {
    if (sel === "details.collapsible-section") return [{ id: "charts-section", open: false, addEventListener() {} }];
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
        style: { width: "" },
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
    investor_id: "youming",
    investor_display_name: "Youming",
    accounts: [{ name: "covered_call", env_file: ".env.covered_call" }],
    scheduler_running: true,
    snapshot_interval_sec: 300,
  },
  "/api/spot": {
    BTC: 65000,
    ETH: 3500,
    price_change_pct_24h: { BTC: 1.2, ETH: -0.8 },
  },
  "/api/portfolio/snapshot": {
    source: "ledger",
    freshness_ms: 120000,
    portfolio: { total_equity_usdc: 9000, equity_by_book: { USDC: 9000 }, regime: "normal" },
  },
  "/api/groups": { open: [], closed: [], underlying_index_usd: { BTC: 65000, ETH: 3500 } },
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
};

globalThis.fetch = async (url) => {
  const pathOnly = String(url).replace(/^https?:\/\/[^/]+/, "");
  if (pathOnly.startsWith("/api/dashboard_bundle")) {
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
  if (pathOnly.startsWith("/api/realized_summary")) {
    return { ok: true, status: 200, json: async () => fixtures["/api/realized_summary?days=30"] };
  }
  if (pathOnly.startsWith("/api/apr_series")) {
    return { ok: true, status: 200, json: async () => fixtures["/api/apr_series?window_days=30"] };
  }
  const body = fixtures[pathOnly];
  if (!body) throw new Error(`unexpected fetch ${pathOnly}`);
  return { ok: true, status: 200, json: async () => body };
};

const { initDashboard } = await import("../../frontend/src/modules/../dashboard.js");
initDashboard();
await new Promise((r) => setTimeout(r, 2000));

const overlay = document.getElementById("investor-load-overlay");
const agg = document.getElementById("aggregate-card")?.innerHTML || "";
const overlayHidden = overlay?.classList && overlay.hidden !== false; // classList mock doesn't track hidden well
const overlayHasHiddenClass = overlay?.className?.includes?.("hidden");
const hasContent = /overview-metrics-grid|inv-dashboard|inv-panel/i.test(agg);

if (!hasContent) {
  console.error("investor aggregate empty:", agg.slice(0, 300));
  process.exit(1);
}
console.log("investor boot OK");
