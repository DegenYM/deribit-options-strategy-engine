import { INVESTOR, INVESTOR_LOCALE, resolveApiUrl } from "../shared/context.js";
import { DASHBOARD_CACHE_MAX_AGE_MS } from "../shared/config.js";
import { STATE } from "../shared/state.js";
import { isPortfolioBreakdownConsistent, applyDiskGroupsPayload } from "./domain.js";

const CACHE_VERSION = 4;

function closedProfitSweepQuoteUsdtSum(groups) {
  let sum = 0;
  for (const g of groups?.closed || []) {
    if (String(g?.status || "").toLowerCase() !== "closed") continue;
    const q = Number(g?.profit_sweep_quote_proceeds ?? g?.profit_sweep_quote_proceeds_lifetime);
    if (Number.isFinite(q)) sum += q;
  }
  return sum;
}

function portfolioUsdtWallet(portfolio) {
  const raw = portfolio?.equity_by_book?.USDT ?? portfolio?.equity_native_by_book?.USDT;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function profitSwapCacheConsistent(payload) {
  const wallet = portfolioUsdtWallet(payload?.portfolioSnapshot?.portfolio);
  const quoteSum = closedProfitSweepQuoteUsdtSum(payload?.groups);
  if (wallet === null || quoteSum <= 0) return true;
  // Wallet may exceed journal when pre-label premium sells sit in USDT spot.
  return wallet + 0.5 >= quoteSum;
}

function storageKey() {
  const base =
    resolveApiUrl("/").replace(/\/$/, "") ||
    (typeof location !== "undefined" ? location.origin : "");
  const scope = INVESTOR ? "inv-dash" : "ops-dash";
  return `${scope}:v${CACHE_VERSION}:${base}:${INVESTOR_LOCALE}`;
}

/**
 * The operator dashboard paints from status + groups, and unlike the investor
 * portal it has no blocking overlay to protect — so it only needs those two to
 * be worth hydrating. Staleness is surfaced by the freshness badge.
 */
function isDashboardCacheComplete(payload) {
  if (!payload) return false;
  const hasGroups = Array.isArray(payload.groups?.closed) && Array.isArray(payload.groups?.open);
  return Boolean(payload.status && hasGroups);
}

function isInvestorCacheComplete(payload) {
  if (!payload) return false;
  const hasPortfolio =
    (payload.portfolioSnapshot?.source === "ledger" ||
      payload.portfolioSnapshot?.source === "portal_cache") &&
    payload.portfolioSnapshot?.portfolio;
  const hasSummary = Boolean(payload.report?.summary);
  const breakdownOk = isPortfolioBreakdownConsistent(payload.portfolioSnapshot?.portfolio);
  const hasGroups = Array.isArray(payload.groups?.closed) && Array.isArray(payload.groups?.open);
  return Boolean(hasPortfolio && hasSummary && breakdownOk && hasGroups && profitSwapCacheConsistent(payload));
}

function pickCacheFields(state) {
  return {
    savedAt: Date.now(),
    portfolioSnapshot: state.portfolioSnapshot,
    report: state.report,
    status: state.status,
    groups: state.groups,
    health: state.health,
    lastSpotUsd: state.lastSpotUsd,
    lastPriceChangePct24h: state.lastPriceChangePct24h,
    lastIvRankPct: state.lastIvRankPct,
    lastIvPercentilePct: state.lastIvPercentilePct,
    lastDvol: state.lastDvol,
    ivRankLookbackDays: state.ivRankLookbackDays,
  };
}

export function isViewCacheComplete(payload) {
  return INVESTOR ? isInvestorCacheComplete(payload) : isDashboardCacheComplete(payload);
}

export function loadViewCache() {
  try {
    const raw = window.localStorage.getItem(storageKey());
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.savedAt !== "number") return null;
    // Beyond the max age a hydrated view is more misleading than useful.
    if (!INVESTOR && Date.now() - parsed.savedAt > DASHBOARD_CACHE_MAX_AGE_MS) return null;
    if (!isViewCacheComplete(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveViewCache(state = STATE) {
  // Check the payload we are about to store, not a partial probe of it — the
  // completeness rules inspect `groups`, which a probe used to omit.
  const payload = pickCacheFields(state);
  if (!isViewCacheComplete(payload)) return;
  try {
    window.localStorage.setItem(storageKey(), JSON.stringify(payload));
  } catch {
    /* quota / private mode */
  }
}

export function hydrateFromViewCache(cached) {
  if (!cached || !isViewCacheComplete(cached)) return false;

  if (cached.portfolioSnapshot) STATE.portfolioSnapshot = cached.portfolioSnapshot;
  if (cached.report) STATE.report = cached.report;
  STATE.status = cached.status;
  STATE.groups = cached.groups;
  if (cached.health) STATE.health = cached.health;
  if (cached.lastSpotUsd) STATE.lastSpotUsd = { ...STATE.lastSpotUsd, ...cached.lastSpotUsd };
  if (cached.lastPriceChangePct24h) {
    STATE.lastPriceChangePct24h = {
      ...STATE.lastPriceChangePct24h,
      ...cached.lastPriceChangePct24h,
    };
  }
  if (cached.lastIvRankPct) {
    STATE.lastIvRankPct = { ...STATE.lastIvRankPct, ...cached.lastIvRankPct };
  }
  if (cached.lastIvPercentilePct) {
    STATE.lastIvPercentilePct = {
      ...STATE.lastIvPercentilePct,
      ...cached.lastIvPercentilePct,
    };
  }
  if (cached.lastDvol) STATE.lastDvol = { ...STATE.lastDvol, ...cached.lastDvol };
  if (cached.ivRankLookbackDays != null) STATE.ivRankLookbackDays = cached.ivRankLookbackDays;

  STATE.dataFreshness = {
    source: "cache",
    snapshotMs: null,
    statusMs: null,
    live: false,
    cacheSavedAt: cached.savedAt,
    cacheAgeMs: Math.max(0, Date.now() - cached.savedAt),
  };
  STATE.summaryLoadPending = false;
  STATE.summaryLoadInFlight = false;
  if (cached.groups) {
    applyDiskGroupsPayload(cached.groups);
  }
  return true;
}

export function clearViewCache() {
  try {
    window.localStorage.removeItem(storageKey());
  } catch {
    /* ignore */
  }
}
