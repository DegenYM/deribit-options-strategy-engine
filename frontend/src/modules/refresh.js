import { INVESTOR, INVESTOR_ZH, i18n, resolveApiUrl } from "../shared/context.js";
import {
  ACTIVITY_PAGE_SIZE,
  BOOK_COLORS,
  CORE_BOOKS,
  FETCH_JSON_MAX_RETRIES,
  FETCH_JSON_RETRYABLE_STATUS,
  FETCH_JSON_RETRY_BASE_MS,
  FRONTEND_API_CONCURRENCY,
  FRONTEND_REFRESH_INTERVAL_MS,
  INVESTOR_OVERLAY_MAX_MS,
  INVESTOR_STATUS_TIMEOUT_MS,
  USE_DASHBOARD_BUNDLE,
  fmt,
} from "../shared/config.js";
import { STATE } from "../shared/state.js";
import { applyDashboardBundlePayload, dashboardBundleUrl, delay, fetchJson, formatFetchError, isInvestorOverviewDisplayReady, mergeStatusPayload, num, premiumSweepFillStatsByBook, promisePool, realizedSummaryUrl, setRefreshControlsDisabled, setRefreshProgressBar, setText, showToast, transfersUrl, updateUnderlyingIndexCache, applyDiskGroupsPayload } from "./domain.js";
import { saveInvestorCache } from "./investor-cache.js";
import { loadChartJs } from "./chart-vendor.js";
import { formatDateTimeHmsLocal } from "./date-time.js";
import { aprSeriesUrl, renderAprChart, renderCumulativeSpotPnlChart, renderCumulativePnlChart, renderDailyPnlChart, scheduleChartResizeAll } from "./charts.js";
import { renderAccountCards, renderAggregate, renderBookCards, renderRecentActivity, renderRegime, renderStrategyGroups, renderStress, renderTransferCards } from "./render.js";

/** Set for the duration of refreshAll; used by background status/bundle retries. */
let activeRenderDashboard = null;
/** Registered once at init; survives after refreshAll for late async updates. */
let persistentRenderDashboard = null;

export function registerRenderDashboard(fn) {
  persistentRenderDashboard = fn;
  STATE.dashboardRenderHook = () => invokeRenderDashboard();
}

function invokeRenderDashboard() {
  (activeRenderDashboard ?? persistentRenderDashboard)?.();
}

function showRefreshFetchToast(
  label,
  err,
  { retry, hasCachedData = false, silentIfLimited = false } = {},
) {
  if (silentIfLimited && hasCachedData && STATE.lastRefreshMs) return;
  const msg = formatFetchError(err);
  showToast(`${label}: ${msg}`, retry ? { retry } : undefined);
}

let lastRefreshTickHandle = null;

function relativeRefreshText(deltaMs) {
  const s = Math.max(0, Math.round(deltaMs / 1000));
  if (s < 10) return i18n("just now", "剛剛");
  if (s < 60) return i18n(`${s}s ago`, `${s} 秒前`);
  const m = Math.round(s / 60);
  if (m < 60) return i18n(`${m}m ago`, `${m} 分鐘前`);
  const h = Math.round(m / 60);
  if (h < 24) return i18n(`${h}h ago`, `${h} 小時前`);
  const d = Math.round(h / 24);
  return i18n(`${d}d ago`, `${d} 天前`);
}

export function updateLastRefreshLabel() {
  const el = document.getElementById("last-refresh");
  if (!el || !STATE.lastRefreshMs) return;
  const full = formatDateTimeHmsLocal(new Date(STATE.lastRefreshMs));
  el.textContent = i18n(
    `updated ${relativeRefreshText(Date.now() - STATE.lastRefreshMs)}`,
    `更新於 ${relativeRefreshText(Date.now() - STATE.lastRefreshMs)}`
  );
  el.title = i18n(
    `Last refresh (local time): ${full}`,
    `上次更新（本地時間）：${full}`
  );
}

export function startLastRefreshTicker() {
  if (lastRefreshTickHandle) return;
  lastRefreshTickHandle = setInterval(updateLastRefreshLabel, 15000);
}

function roundIvRankPctOneDecimal(pct) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  return Math.round(pct * 10) / 10;
}

function formatIvRankPctText(pct) {
  const rounded = roundIvRankPctOneDecimal(pct);
  if (rounded === null) return null;
  return rounded.toFixed(1);
}

export function resolveIvRankPct(spotPayload, symbol) {
  const key = String(symbol || "").toUpperCase();
  const rank = num(spotPayload?.iv_rank?.[key]);
  if (rank !== null) {
    if (rank >= 0 && rank <= 1) return roundIvRankPctOneDecimal(rank * 100);
    if (rank > 1 && rank <= 100) return roundIvRankPctOneDecimal(rank);
  }
  const pctRaw = num(spotPayload?.iv_rank_pct?.[key]);
  if (pctRaw !== null && pctRaw >= 0 && pctRaw <= 100) {
    return roundIvRankPctOneDecimal(pctRaw);
  }
  return null;
}

function formatIvRankLabel(pct, symbol) {
  const pctText = formatIvRankPctText(pct);
  if (pctText === null) return null;
  const rounded = roundIvRankPctOneDecimal(pct);
  const dvol = num(STATE.lastDvol?.[symbol]);
  const lookback = num(STATE.ivRankLookbackDays);
  const detail =
    dvol !== null && lookback !== null
      ? i18n(
          `IV Rank ${pctText}% (DVOL ${dvol}, ${lookback}d H/L range)`,
          `IV Rank ${pctText}%（DVOL ${dvol}，${lookback} 日 K 高低區間）`
        )
      : i18n(`IV Rank ${pctText}%`, `IV Rank ${pctText}%`);
  return { text: i18n(`IVR ${pctText}%`, `IVR ${pctText}%`), title: detail, pct: rounded };
}

function ivrLevelClass(pct) {
  if (pct < 25) return "header-ivr--low";
  if (pct < 60) return "header-ivr--mid";
  return "header-ivr--high";
}

function formatPriceChange24hText(pct) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  const rounded = Math.round(pct * 10) / 10;
  const sign = rounded > 0 ? "+" : rounded < 0 ? "" : "";
  return `${sign}${rounded.toFixed(1)}%`;
}

function priceChange24hClass(pct) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "";
  if (pct > 0) return "header-price-change--up";
  if (pct < 0) return "header-price-change--down";
  return "header-price-change--flat";
}

function updateHeaderMarketLine(symbol, spotUsd, ivRankPct, priceChangePct24h = null) {
  const key = String(symbol || "").toUpperCase();
  const priceEl = document.getElementById(`header-spot-${key.toLowerCase()}-price`);
  const changeEl = document.getElementById(`header-spot-${key.toLowerCase()}-change`);
  const ivrEl = document.getElementById(`header-spot-${key.toLowerCase()}-ivr`);
  const legacyEl = document.getElementById(`header-spot-${key.toLowerCase()}`);
  const priceText =
    spotUsd !== null && spotUsd > 0 ? fmt.usd2.format(spotUsd) : "—";
  const fullSpotPart =
    spotUsd !== null && spotUsd > 0 ? `${key} ${priceText}` : `${key} —`;
  if (priceEl) {
    priceEl.textContent = priceText;
  }
  const changeText = formatPriceChange24hText(priceChangePct24h);
  if (changeEl) {
    changeEl.classList.remove(
      "header-price-change--up",
      "header-price-change--down",
      "header-price-change--flat"
    );
    if (changeText) {
      changeEl.hidden = false;
      changeEl.textContent = changeText;
      changeEl.title = i18n("24h price change", "24 小時漲跌幅");
      changeEl.classList.add(priceChange24hClass(priceChangePct24h));
    } else {
      changeEl.hidden = true;
      changeEl.textContent = "";
      changeEl.removeAttribute("title");
    }
  }
  const ivMeta = formatIvRankLabel(ivRankPct, key);
  if (ivrEl) {
    ivrEl.classList.remove("header-ivr--low", "header-ivr--mid", "header-ivr--high");
    if (ivMeta) {
      ivrEl.hidden = false;
      ivrEl.textContent = ivMeta.text;
      ivrEl.title = ivMeta.title;
      ivrEl.dataset.ivrPct = String(ivMeta.pct);
      ivrEl.classList.add(ivrLevelClass(ivMeta.pct));
    } else {
      ivrEl.hidden = true;
      ivrEl.textContent = "";
      ivrEl.removeAttribute("title");
      delete ivrEl.dataset.ivrPct;
    }
  }
  if (legacyEl && !priceEl) {
    legacyEl.textContent = ivMeta ? `${fullSpotPart} · ${ivMeta.text}` : fullSpotPart;
    legacyEl.title = ivMeta?.title || "";
  }
}

export function updateHeaderSpotDom() {
  updateHeaderMarketLine(
    "BTC",
    STATE.lastSpotUsd.BTC,
    STATE.lastIvRankPct.BTC,
    STATE.lastPriceChangePct24h.BTC
  );
  updateHeaderMarketLine(
    "ETH",
    STATE.lastSpotUsd.ETH,
    STATE.lastIvRankPct.ETH,
    STATE.lastPriceChangePct24h.ETH
  );
}

function waitForChartPanelLayout() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

export async function renderPerformanceCharts() {
  if (!chartsSectionOpen()) return;
  await waitForChartPanelLayout();
  try {
    await loadChartJs();
  } catch (err) {
    console.error("chart vendor load failed", err);
    showToast(`${i18n("charts", "圖表")}: ${formatFetchError(err)}`);
    return;
  }
  const chartFns = [
    ["risk-capital", renderCumulativeSpotPnlChart],
    ["cum-pnl", renderCumulativePnlChart],
    ["daily-pnl", renderDailyPnlChart],
    ["apr", renderAprChart],
  ];
  for (const [name, fn] of chartFns) {
    try {
      fn();
    } catch (err) {
      console.error(`${name} chart render failed`, err);
    }
  }
  scheduleChartResizeAll();
}

/** Minimum time the blocking investor overlay stays visible (allows first paint). */
const INVESTOR_LOAD_MIN_MS = 450;

const INVESTOR_LOAD_STEPS = {
  spot: {
    en: "Fetching BTC / ETH market prices…",
    zh: "正在取得 BTC / ETH 即時報價…",
  },
  snapshot: {
    en: "Loading last equity snapshot…",
    zh: "正在讀取最近權益快照…",
  },
  health: {
    en: "Checking account connection…",
    zh: "正在確認帳戶連線…",
  },
  groups: {
    en: "Loading open positions and spreads…",
    zh: "正在讀取持倉與價差部位…",
  },
  cumulative: {
    en: "Loading realized P&L history…",
    zh: "正在載入已實現損益歷史…",
  },
  "spot-pnl": {
    en: "Loading spot P&L history…",
    zh: "正在載入 spot 損益歷史…",
  },
  apr: {
    en: "Calculating rolling performance (APR)…",
    zh: "正在計算滾動年化報酬…",
  },
  status: {
    en: "Syncing live equity and margin…",
    zh: "正在同步即時權益與保證金…",
  },
  summary: {
    en: "Loading performance summary from local records…",
    zh: "正在從本地紀錄載入績效摘要…",
  },
  render: {
    en: "Preparing your dashboard…",
    zh: "正在整理儀表板顯示…",
  },
  done: {
    en: "Done",
    zh: "完成",
  },
};

export function investorLoadLabel(stepKey) {
  const s = INVESTOR_LOAD_STEPS[stepKey];
  return s ? i18n(s.en, s.zh) : "";
}

export function investorLoadStepCount(hasPrivateCreds, { includeCharts = true } = {}) {
  let steps = 2 + 1 + (hasPrivateCreds ? 2 : 0) + 1;
  if (includeCharts) steps += 3;
  return steps;
}

export function setInvestorLoadProgress(ratio, stepKey) {
  const pct = Math.min(100, Math.max(0, Math.round(ratio * 100)));
  const fill = document.getElementById("investor-load-bar-fill");
  if (fill) fill.style.width = `${pct}%`;
  const pctEl = document.querySelector("[data-investor-load-pct]");
  if (pctEl) pctEl.textContent = `${pct}%`;
  const stepEl = document.querySelector("[data-investor-load-step]");
  if (stepEl && stepKey) stepEl.textContent = investorLoadLabel(stepKey);
}

export function applyInvestorLoadCopy() {
  if (!INVESTOR) return;
  const set = (attr, en, zh) => {
    const el = document.querySelector(`[data-investor-load-${attr}]`);
    if (el) el.textContent = i18n(en, zh);
  };
  set("eyebrow", "Initializing", "初始化中");
  set("title", "Loading portfolio data", "正在載入投資組合資料");
  set(
    "hint",
    "Restoring cached snapshot; live marks sync in the background.",
    "先還原快取快照；即時標價於背景同步。"
  );
}

export function beginInvestorLoad({ blocking = true } = {}) {
  if (!INVESTOR) return;
  STATE.investorLoadDone = 0;
  STATE.investorLoadTotal = investorLoadStepCount(false);
  STATE.investorLoadStartedMs = Date.now();
  STATE.investorLoadDismissAllowed = false;
  document.body.classList.toggle("investor-blocking-load", blocking);
  const overlay = document.getElementById("investor-load-overlay");
  if (overlay) {
    overlay.classList.remove("hidden");
    overlay.classList.toggle("investor-load-overlay--refresh", !blocking);
    overlay.setAttribute("aria-busy", "true");
  }
  const refreshBtn = document.getElementById("refresh-now");
  if (refreshBtn) refreshBtn.disabled = true;
  setInvestorLoadProgress(0, "spot");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      STATE.investorLoadDismissAllowed = true;
    });
  });
}

function investorOverlayDismissDelayMs() {
  const started = STATE.investorLoadStartedMs || 0;
  const minRemaining = Math.max(0, INVESTOR_LOAD_MIN_MS - (Date.now() - started));
  if (!STATE.investorLoadDismissAllowed) {
    return Math.max(minRemaining, 32);
  }
  return minRemaining;
}

function scheduleInvestorOverlayDismiss(onReady) {
  const attempt = () => {
    if (!INVESTOR || STATE.investorReady) return;
    const delayMs = investorOverlayDismissDelayMs();
    if (delayMs > 0) {
      setTimeout(attempt, delayMs);
      return;
    }
    onReady();
  };
  attempt();
}

export function advanceInvestorLoad(stepKey) {
  if (!INVESTOR) return;
  STATE.investorLoadDone = Math.min(
    STATE.investorLoadTotal || 1,
    STATE.investorLoadDone + 1
  );
  const ratio =
    STATE.investorLoadTotal > 0 ? STATE.investorLoadDone / STATE.investorLoadTotal : 0;
  setInvestorLoadProgress(ratio, stepKey);
}

export function setInvestorPageReady(ready) {
  if (!INVESTOR) return;
  if (!ready) {
    beginInvestorLoad({ blocking: !STATE.investorReady });
    return;
  }
  setInvestorLoadProgress(1, "done");
  STATE.investorReady = true;
  document.body.classList.remove("investor-blocking-load");
  document.body.classList.add("investor-ready");
  const overlay = document.getElementById("investor-load-overlay");
  if (overlay) {
    overlay.classList.add("hidden");
    overlay.classList.remove("investor-load-overlay--refresh");
    overlay.setAttribute("aria-busy", "false");
  }
  const refreshBtn = document.getElementById("refresh-now");
  if (refreshBtn) refreshBtn.disabled = false;
  scheduleChartResizeAll();
}

export async function tickHeaderSpot({ renderDependentViews = true, updateDom = true } = {}) {
  try {
    const d = await fetchJson("/api/spot");
    STATE.lastSpotUsd.BTC = num(d.BTC);
    STATE.lastSpotUsd.ETH = num(d.ETH);
    STATE.lastPriceChangePct24h.BTC = num(d?.price_change_pct_24h?.BTC);
    STATE.lastPriceChangePct24h.ETH = num(d?.price_change_pct_24h?.ETH);
    STATE.lastIvRankPct.BTC = resolveIvRankPct(d, "BTC");
    STATE.lastIvRankPct.ETH = resolveIvRankPct(d, "ETH");
    STATE.lastDvol.BTC = num(d?.dvol?.BTC);
    STATE.lastDvol.ETH = num(d?.dvol?.ETH);
    STATE.ivRankLookbackDays = num(d?.iv_rank_lookback_days);
    if (updateDom) {
      updateHeaderSpotDom();
      if (renderDependentViews && !STATE.refreshInFlight) {
        renderAggregate(STATE.status, STATE.report);
        renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
        renderRecentActivity(STATE.status, STATE.report, STATE.groups);
      }
    }
  } catch (_) {
    /* ignore */
  }
}

export function chartsSectionOpen() {
  const el = document.getElementById("charts-section");
  return Boolean(el?.open);
}

export function stressSectionOpen() {
  const el = document.getElementById("stress-section");
  return Boolean(el?.open);
}

export function transfersSectionOpen() {
  const el = document.getElementById("transfers-section");
  return Boolean(el?.open);
}

export async function fetchPortfolioSnapshot() {
  try {
    const d = await fetchJson("/api/portfolio/snapshot");
    STATE.portfolioSnapshot = d;
    if (d?.realized_summary) {
      STATE.report = d.realized_summary;
      STATE.summaryLoadPending = false;
      STATE.summaryLoadInFlight = false;
    }
    if (d?.groups) {
      applyDiskGroupsPayload(d.groups);
    }
    const liveStatus = d?.live_status;
    if (liveStatus && typeof liveStatus === "object") {
      STATE.status = mergeStatusPayload(STATE.status, liveStatus);
    }
    if (!premiumSweepFillStatsByBook(STATE.status) && Boolean(STATE.health?.has_private_creds)) {
      fetchJson("/api/status")
        .then((status) => {
          if (status?.premium_sweep_fill_stats_by_book) {
            STATE.status = mergeStatusPayload(STATE.status, status);
            invokeRenderDashboard();
          }
        })
        .catch(() => {});
    }
    if (d?.source === "ledger" || d?.source === "portal_cache") {
      if (d.source === "portal_cache" && d.cache_kind === "live") {
        STATE.dataFreshness.source = "live";
        STATE.dataFreshness.live = true;
        STATE.dataFreshness.statusMs = 0;
        STATE.groupsLivePending = false;
      } else {
        STATE.dataFreshness.source = "snapshot";
        STATE.dataFreshness.live = false;
      }
      STATE.dataFreshness.snapshotMs = num(d.freshness_ms);
    }
  } catch (_) {
    /* snapshot is optional for first paint */
  } finally {
    invokeRenderDashboard();
  }
}

export async function fetchStatusWithTimeout() {
  const timeoutMs = INVESTOR_STATUS_TIMEOUT_MS;
  let timedOut = false;
  const timeoutPromise = delay(timeoutMs).then(() => {
    timedOut = true;
    throw new Error("status timeout");
  });
  try {
    const d = await Promise.race([fetchJson("/api/status"), timeoutPromise]);
    STATE.status = mergeStatusPayload(STATE.status, d);
    STATE.statusErrorOnce = false;
    STATE.dataFreshness.source = "live";
    STATE.dataFreshness.live = true;
    STATE.dataFreshness.statusMs = 0;
    return d;
  } catch (err) {
    if (timedOut && STATE.portfolioSnapshot?.portfolio) {
      if (!STATE.statusErrorOnce) {
        showToast(
          i18n(
            "Live sync is slow; showing last snapshot.",
            "即時同步較慢，先顯示最近快照。"
          )
        );
        STATE.statusErrorOnce = true;
      }
      fetchJson("/api/status")
        .then((d) => {
          STATE.status = mergeStatusPayload(STATE.status, d);
          STATE.dataFreshness.source = "live";
          STATE.dataFreshness.live = true;
          invokeRenderDashboard();
        })
        .catch(() => {});
      return null;
    }
    STATE.status = null;
    if (!STATE.statusErrorOnce) {
      showToast(`${i18n("status", "即時狀態")}: ${formatFetchError(err)}`, {
        retry: () => refreshAll({ force: true, renderDashboard: persistentRenderDashboard }),
      });
      STATE.statusErrorOnce = true;
    }
    return null;
  }
}

export async function fetchDashboardBundle({ backgroundOnTimeout = false, sections = null } = {}) {
  const timeoutMs = INVESTOR_STATUS_TIMEOUT_MS;
  let timedOut = false;
  const bundleRequest = fetchJson(dashboardBundleUrl(30, { sections }));
  const raced = INVESTOR
    ? Promise.race([
        bundleRequest,
        delay(timeoutMs).then(() => {
          timedOut = true;
          throw new Error("dashboard bundle timeout");
        }),
      ])
    : bundleRequest;
  try {
    applyDashboardBundlePayload(await raced);
    return true;
  } catch (err) {
    if (INVESTOR && timedOut && STATE.portfolioSnapshot?.portfolio) {
      if (!STATE.statusErrorOnce) {
        showToast(
          i18n(
            "Live sync is slow; showing last snapshot.",
            "即時同步較慢，先顯示最近快照。"
          )
        );
        STATE.statusErrorOnce = true;
      }
      if (backgroundOnTimeout) {
        fetchJson(dashboardBundleUrl(30, { sections }))
          .then((d) => {
            applyDashboardBundlePayload(d);
            invokeRenderDashboard();
          })
          .catch(() => {});
      }
      return false;
    }
    if (!INVESTOR || !timedOut) {
      showRefreshFetchToast(i18n("dashboard bundle", "Dashboard 資料"), err, {
        hasCachedData: Boolean(STATE.status || STATE.groups?.open?.length || STATE.groups?.closed?.length),
      });
    }
    return false;
  }
}

async function fetchChartSeries(investorFetchWrap = null) {
  const chartFetchOpts =
    typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function"
      ? { signal: AbortSignal.timeout(INVESTOR_STATUS_TIMEOUT_MS) }
      : {};

  const fetchCumulative = () =>
    fetchJson("/api/cumulative_pnl_series", chartFetchOpts)
      .then((d) => {
        STATE.cumulativePnl = d;
      })
      .catch((err) =>
        showRefreshFetchToast(i18n("cumulative pnl", "累積損益"), err, {
          hasCachedData: Boolean(STATE.cumulativePnl),
        })
      );

  const fetchSpotPnlSeries = () =>
    fetchJson("/api/cumulative_spot_pnl_series", chartFetchOpts)
      .then((d) => {
        STATE.cumulativeSpotPnl = d;
      })
      .catch((err) =>
        showRefreshFetchToast(i18n("cumulative spot pnl", "累積 spot 損益"), err, {
          hasCachedData: Boolean(STATE.cumulativeSpotPnl),
        })
      );

  const fetchApr = () =>
    fetchJson(aprSeriesUrl(), chartFetchOpts)
      .then((d) => {
        STATE.aprSeries = d;
      })
      .catch((err) =>
        showRefreshFetchToast(i18n("apr series", "APR 序列"), err, {
          hasCachedData: Boolean(STATE.aprSeries),
        })
      );

  if (investorFetchWrap) {
    await Promise.all([
      investorFetchWrap("cumulative", fetchCumulative),
      investorFetchWrap("spot-pnl", fetchSpotPnlSeries),
      investorFetchWrap("apr", fetchApr),
    ]);
  } else {
    await Promise.all([fetchCumulative(), fetchSpotPnlSeries(), fetchApr()]);
  }
}

export async function loadChartDataIfNeeded({ force = false, investorFetchWrap = null } = {}) {
  if (!force && !chartsSectionOpen()) return;
  if (!force && STATE.chartsDataLoaded) {
    await renderPerformanceCharts();
    return;
  }
  if (STATE.chartsLoadPromise) {
    await STATE.chartsLoadPromise;
    if (chartsSectionOpen()) await renderPerformanceCharts();
    return;
  }

  STATE.chartsLoadInFlight = true;
  STATE.chartsLoadPromise = (async () => {
    await loadChartJs();
    await fetchChartSeries(investorFetchWrap);
    STATE.chartsDataLoaded = true;
    await renderPerformanceCharts();
  })();

  try {
    await STATE.chartsLoadPromise;
  } finally {
    STATE.chartsLoadInFlight = false;
    STATE.chartsLoadPromise = null;
  }
}

export async function loadStressIfNeeded({ force = false } = {}) {
  if (INVESTOR) return;
  if (!force && !stressSectionOpen()) return;
  if (!force && STATE.stressDataLoaded) {
    renderStress(STATE.stress);
    return;
  }
  if (STATE.stressLoadInFlight) return;
  if (!STATE.health?.has_private_creds) return;
  STATE.stressLoadInFlight = true;
  try {
    const d = await fetchJson("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5");
    STATE.stress = d;
    STATE.stressDataLoaded = true;
    renderStress(STATE.stress);
  } catch (err) {
    showRefreshFetchToast(i18n("stress", "壓力測試"), err, {
      hasCachedData: Boolean(STATE.stress),
    });
  } finally {
    STATE.stressLoadInFlight = false;
  }
}

export async function loadTransfersIfNeeded({ force = false } = {}) {
  if (!force && !transfersSectionOpen()) return;
  if (!force && STATE.transfersDataLoaded) {
    renderTransferCards(STATE.transfers);
    return;
  }
  if (STATE.transfersLoadInFlight) return;
  if (!STATE.health?.has_private_creds) return;
  STATE.transfersLoadInFlight = true;
  try {
    const d = await fetchJson(transfersUrl(90, 100));
    STATE.transfers = d;
    STATE.transfersDataLoaded = true;
    renderTransferCards(STATE.transfers);
  } catch (err) {
    showRefreshFetchToast(i18n("asset transfers", "資產劃轉"), err, {
      hasCachedData: Boolean(STATE.transfers),
    });
  } finally {
    STATE.transfersLoadInFlight = false;
  }
}

export function refreshWaitMs() {
  if (!STATE.lastRefreshStartedMs) return 0;
  return Math.max(0, FRONTEND_REFRESH_INTERVAL_MS - (Date.now() - STATE.lastRefreshStartedMs));
}

export async function refreshAll({ force = false, silentIfLimited = false, renderDashboard: renderDashboardFn } = {}) {
  activeRenderDashboard = renderDashboardFn ?? null;
  if (STATE.refreshInFlight) {
    if (!silentIfLimited) showToast(i18n("refresh already running", "已有更新正在進行"));
    return;
  }
  const waitMs = refreshWaitMs();
  if (!force && waitMs > 0) {
    if (!silentIfLimited)
      showToast(
        i18n(
          `refresh rate limited; wait ${Math.ceil(waitMs / 1000)}s`,
          `請稍候 ${Math.ceil(waitMs / 1000)} 秒後再試`
        )
      );
    return;
  }

  STATE.refreshInFlight = true;
  STATE.lastRefreshStartedMs = Date.now();
  const investorFirstLoad = INVESTOR && !STATE.investorReady;
  if (investorFirstLoad) {
    if (!STATE.investorLoadStartedMs) {
      beginInvestorLoad({ blocking: true });
    }
  } else {
    setRefreshProgressBar(true, { indeterminate: true });
    setRefreshControlsDisabled(true);
  }
  try {
    let renderScheduled = false;
    function scheduleRender() {
      if (renderScheduled) return;
      renderScheduled = true;
      requestAnimationFrame(() => {
        renderScheduled = false;
        try {
          renderDashboardFn?.();
        } catch (err) {
          console.error("renderDashboard failed", err);
          showToast(`render failed: ${err.message}`);
        }
      });
    }

    function investorFetch(stepKey, run) {
      if (!investorFirstLoad) return run();
      return run().finally(() => advanceInvestorLoad(stepKey));
    }

    let snapshotFetchedThisRefresh = false;
    let snapshotPromise = null;
    let summaryLoadPromise = null;

    const investorFetchWrap = investorFirstLoad
      ? (stepKey, run) => investorFetch(stepKey, run)
      : null;
    const wrapStep = (stepKey, run) =>
      investorFetchWrap ? investorFetchWrap(stepKey, run) : run();

    function maybeDismissInvestorOverlay() {
      if (!investorFirstLoad) return;
      const snap = STATE.portfolioSnapshot;
      const hasPortfolio =
        (snap?.source === "ledger" || snap?.source === "portal_cache") && snap?.portfolio;
      const hasSummary = Boolean(STATE.report?.summary);
      if (!hasPortfolio && !hasSummary) return;
      if (!isInvestorOverviewDisplayReady()) return;
      scheduleInvestorOverlayDismiss(() => {
        if (!STATE.investorReady) {
          setInvestorPageReady(true);
          setRefreshProgressBar(true, { indeterminate: true });
        }
        scheduleRender();
      });
    }

    function getSnapshotLoad() {
      if (!snapshotPromise) {
        const run = () =>
          fetchPortfolioSnapshot().then(() => {
            maybeDismissInvestorOverlay();
            scheduleRender();
          });
        snapshotPromise = investorFirstLoad ? investorFetch("snapshot", run) : run();
      }
      return snapshotPromise;
    }

    if (INVESTOR && investorFirstLoad) {
      getSnapshotLoad();
    }

    function ensureSummaryLoad() {
      if (!Boolean(STATE.health?.has_private_creds)) return Promise.resolve(false);
      if (STATE.report?.summary) {
        STATE.summaryLoadPending = false;
        STATE.summaryLoadInFlight = false;
        return Promise.resolve(true);
      }
      if (summaryLoadPromise) return summaryLoadPromise;
      STATE.summaryLoadPending = true;
      STATE.summaryLoadInFlight = true;
      const load = () =>
        fetchJson(realizedSummaryUrl(30))
          .then((d) => {
            STATE.report = d;
            scheduleRender();
            return true;
          })
          .catch((err) => {
            showRefreshFetchToast(i18n("realized summary", "已實現損益"), err, {
              hasCachedData: Boolean(STATE.report?.summary),
              silentIfLimited,
            });
            return false;
          });
      const tracked = wrapStep("summary", load);
      summaryLoadPromise = tracked
        .then((ok) => {
          if (ok) maybeDismissInvestorOverlay();
          return ok;
        })
        .finally(() => {
          STATE.summaryLoadPending = false;
          STATE.summaryLoadInFlight = false;
          scheduleRender();
        });
      return summaryLoadPromise;
    }

    const healthCore = fetchJson("/api/health").then((d) => {
      STATE.health = d;
      scheduleRender();
      if (investorFirstLoad) {
        STATE.investorLoadTotal = investorLoadStepCount(Boolean(d?.has_private_creds), {
          includeCharts: chartsSectionOpen(),
        });
      }
      if (d?.has_private_creds) {
        ensureSummaryLoad()?.then(() => maybeDismissInvestorOverlay());
      }
      return d;
    });

    try {
      await Promise.all([
        investorFetch("health", () => healthCore),
        investorFetch("spot", () =>
          tickHeaderSpot({
            renderDependentViews: false,
            updateDom: true,
          })
        ),
      ]);
    } catch (err) {
      showRefreshFetchToast(i18n("health", "連線狀態"), err, {
        hasCachedData: Boolean(STATE.health),
        silentIfLimited,
      });
    }

    const hasPrivateCreds = Boolean(STATE.health?.has_private_creds);

    if (!INVESTOR && hasPrivateCreds) {
      ensureSummaryLoad();
    }

    if (INVESTOR && investorFirstLoad) {
      try {
        await Promise.race([
          Promise.all([
            getSnapshotLoad(),
            hasPrivateCreds ? ensureSummaryLoad() : Promise.resolve(),
          ]),
          delay(INVESTOR_OVERLAY_MAX_MS),
        ]);
      } catch (_) {
        /* snapshot optional; always dismiss blocking overlay on timeout */
      }
      maybeDismissInvestorOverlay();
      if (!STATE.investorReady) {
        scheduleInvestorOverlayDismiss(() => {
          if (!STATE.investorReady) {
            setInvestorPageReady(true);
            setRefreshProgressBar(true, { indeterminate: true });
          }
          scheduleRender();
        });
      } else {
        scheduleRender();
      }
      snapshotFetchedThisRefresh = true;
    }

    if (!INVESTOR && force && hasPrivateCreds && !snapshotFetchedThisRefresh) {
      try {
        await fetchPortfolioSnapshot();
        scheduleRender();
      } catch (_) {
        /* snapshot optional; live bundle follows */
      }
      snapshotFetchedThisRefresh = true;
    }

    function fetchGroups() {
      return fetchJson("/api/groups")
        .then((d) => {
          STATE.groups = d;
          scheduleRender();
        })
        .catch((err) => {
          showRefreshFetchToast(i18n("groups", "持倉"), err, {
            hasCachedData: Boolean(STATE.groups?.open?.length || STATE.groups?.closed?.length),
            silentIfLimited,
          });
        });
    }

    function fetchSummary() {
      return ensureSummaryLoad();
    }

    function fetchStatusOp() {
      return fetchJson("/api/status")
        .then((d) => {
          STATE.status = mergeStatusPayload(STATE.status, d);
          STATE.statusErrorOnce = false;
          scheduleRender();
        })
        .catch((err) => {
          STATE.status = null;
          if (!STATE.statusErrorOnce) {
            showToast(`${i18n("status", "即時狀態")}: ${formatFetchError(err)}`, {
              retry: () => refreshAll({ force: true, renderDashboard: persistentRenderDashboard }),
            });
            STATE.statusErrorOnce = true;
          }
        });
    }

    function fetchStress() {
      return loadStressIfNeeded({ force: true });
    }

    function fetchTransfers() {
      return loadTransfersIfNeeded({ force: true });
    }

    async function fetchPortfolioDataIndividual() {
      await wrapStep("groups", fetchGroups);
      if (INVESTOR) {
        await wrapStep("status", () => fetchStatusWithTimeout().then(() => scheduleRender()));
        await ensureSummaryLoad();
      } else {
        await Promise.all([fetchStatusOp(), ensureSummaryLoad()]);
      }
    }

    async function fetchPortfolioData() {
      if (!hasPrivateCreds) {
        await wrapStep("groups", fetchGroups);
        STATE.status = null;
        STATE.report = null;
        STATE.stress = null;
        STATE.stressDataLoaded = false;
        STATE.transfers = null;
        STATE.transfersDataLoaded = false;
        return;
      }
      if (USE_DASHBOARD_BUNDLE) {
        const useStagedBundle =
          hasPrivateCreds && ((INVESTOR && investorFirstLoad) || (!INVESTOR && force));
        if (useStagedBundle) {
          if (INVESTOR && investorFirstLoad) {
            fetchDashboardBundle({ sections: "status,groups", backgroundOnTimeout: true })
              .then((liveOk) => {
                if (liveOk) {
                  advanceInvestorLoad("groups");
                  advanceInvestorLoad("status");
                }
                invokeRenderDashboard();
              })
              .catch(() => {});
            return;
          }
          const liveOk = await fetchDashboardBundle({ sections: "status,groups" });
          if (liveOk) {
            if (investorFirstLoad) {
              advanceInvestorLoad("groups");
              advanceInvestorLoad("status");
            }
            scheduleRender();
            return;
          }
        }
        const ok = await fetchDashboardBundle({ backgroundOnTimeout: INVESTOR });
        if (ok) {
          if (investorFirstLoad) {
            advanceInvestorLoad("groups");
            advanceInvestorLoad("status");
            if (STATE.report?.summary) advanceInvestorLoad("summary");
          }
          scheduleRender();
          return;
        }
      }
      await fetchPortfolioDataIndividual();
    }

    const wave = [() => fetchPortfolioData()];

    const stressNeeded = !INVESTOR && hasPrivateCreds && stressSectionOpen();
    if (stressNeeded) {
      wave.push(() => fetchStress());
    }

    const transfersNeeded = hasPrivateCreds && transfersSectionOpen();
    if (transfersNeeded) {
      wave.push(() => fetchTransfers());
    }

    if (INVESTOR && !snapshotFetchedThisRefresh) {
      wave.push(() =>
        wrapStep("snapshot", () => fetchPortfolioSnapshot().then(() => scheduleRender()))
      );
    }

    const chartsNeeded = chartsSectionOpen();
    if (chartsNeeded) {
      wave.push(() =>
        loadChartDataIfNeeded({
          force: false,
          investorFetchWrap,
        })
      );
    }

    await promisePool(wave, FRONTEND_API_CONCURRENCY);

    if (INVESTOR) {
      STATE.stress = null;
    }

    if (INVESTOR && !STATE.investorReady && isInvestorOverviewDisplayReady()) {
      scheduleInvestorOverlayDismiss(() => {
        if (!STATE.investorReady) {
          setInvestorPageReady(true);
        }
      });
    }

    scheduleRender();

    setRefreshProgressBar(false);
    setRefreshControlsDisabled(false);
    STATE.lastRefreshMs = Date.now();
    updateLastRefreshLabel();
  } finally {
    STATE.refreshInFlight = false;
    setRefreshProgressBar(false);
    setRefreshControlsDisabled(false);
    activeRenderDashboard = null;
    if (INVESTOR) {
      saveInvestorCache();
      invokeRenderDashboard();
    }
  }
}
