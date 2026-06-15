import { INVESTOR } from "./context.js";

export const fmt = {
  usd0: new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }),
  usd2: new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }),
  num4: new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 4,
  }),
  num8: new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 8,
  }),
  pct2: new Intl.NumberFormat("en-US", {
    style: "percent",
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }),
  pct1: new Intl.NumberFormat("en-US", {
    style: "percent",
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  }),
};

export const BOOK_COLORS = {
  BTC: "#fb923c",
  ETH: "#818cf8",
  USDC: "#38bdf8",
  USDT: "#22c55e",
  TOTAL: "#a3e635",
};
export const CORE_BOOKS = ["BTC", "ETH", "USDC", "USDT"];
export const FRONTEND_REFRESH_INTERVAL_MS = 180_000;
export const FRONTEND_API_CONCURRENCY = INVESTOR ? 6 : 3;
export const USE_DASHBOARD_BUNDLE = true;
export const INVESTOR_STATUS_TIMEOUT_MS = 45_000;
export const INVESTOR_OVERLAY_MAX_MS = 6_000;
export const FETCH_JSON_RETRYABLE_STATUS = new Set([502, 503, 504]);
export const FETCH_JSON_MAX_RETRIES = 2;
export const FETCH_JSON_RETRY_BASE_MS = 450;
/** Extra attempts when the browser reports a network error (e.g. server restart). */
export const FETCH_JSON_NETWORK_MAX_RETRIES = 3;
export const FETCH_JSON_NETWORK_RETRY_BASE_MS = 500;
export const ACTIVITY_PAGE_SIZE = 10;

export const STRATEGIES = [
  {
    id: "covered_call",
    title: "Covered Call",
    titleZh: "備兌買權",
    short: "Covered Call",
    shortZh: "備兌",
    chipShort: "CC",
    chipShortZh: "備兌",
    accentClass: "strategy-card-call",
    description: "Short call backed by existing BTC/ETH spot collateral.",
    descriptionZh: "在持有現貨擔保下賣出買權，以權利金增強收益。",
  },
  {
    id: "naked_short",
    title: "Naked Short",
    titleZh: "單賣選擇權（裸賣）",
    short: "Naked Short",
    shortZh: "裸賣",
    chipShort: "Naked",
    chipShortZh: "裸賣",
    accentClass: "strategy-card-put",
    description: "Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",
    descriptionZh: "單邊賣出買／賣權；在對應方向具尾部風險，需嚴格風控。",
  },
  {
    id: "bull_put_spread",
    title: "Bull Put Spread",
    titleZh: "牛勢賣權價差",
    short: "Put Spread",
    shortZh: "賣權價差",
    chipShort: "Spread",
    chipShortZh: "價差",
    accentClass: "strategy-card-spread",
    description: "Short put paired with a lower-strike long put protection leg.",
    descriptionZh: "賣出較高履約價賣權，並買入較低履約價賣權作保護。",
  },
];

export const STRATEGY_BY_ID = Object.fromEntries(STRATEGIES.map((s) => [s.id, s]));
