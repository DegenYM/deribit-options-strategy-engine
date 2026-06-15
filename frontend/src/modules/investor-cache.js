import { INVESTOR, INVESTOR_LOCALE, resolveApiUrl } from "../shared/context.js";
import { STATE } from "../shared/state.js";
import { isPortfolioBreakdownConsistent, applyDiskGroupsPayload } from "./domain.js";

const CACHE_VERSION = 3;

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
  return `inv-dash:v${CACHE_VERSION}:${base}:${INVESTOR_LOCALE}`;
}

export function isInvestorCacheComplete(payload) {
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
    lastDvol: state.lastDvol,
    ivRankLookbackDays: state.ivRankLookbackDays,
  };
}

export function loadInvestorCache() {
  if (!INVESTOR) return null;
  try {
    const raw = window.localStorage.getItem(storageKey());
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.savedAt !== "number") return null;
    if (!isInvestorCacheComplete(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveInvestorCache(state = STATE) {
  if (!INVESTOR || !isInvestorCacheComplete({ portfolioSnapshot: state.portfolioSnapshot, report: state.report })) {
    return;
  }
  try {
    window.localStorage.setItem(storageKey(), JSON.stringify(pickCacheFields(state)));
  } catch {
    /* quota / private mode */
  }
}

export function hydrateFromInvestorCache(cached) {
  if (!cached || !isInvestorCacheComplete(cached)) return false;

  STATE.portfolioSnapshot = cached.portfolioSnapshot;
  STATE.report = cached.report;
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

export function clearInvestorCache() {
  if (!INVESTOR) return;
  try {
    window.localStorage.removeItem(storageKey());
  } catch {
    /* ignore */
  }
}
