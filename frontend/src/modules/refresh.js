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
  STRATEGIES,
  STRATEGY_BY_ID,
  USE_DASHBOARD_BUNDLE,
  fmt,
} from "../shared/config.js";
import { STATE } from "../shared/state.js";
import { applyDashboardBundlePayload, dashboardBundleUrl, delay, fetchJson, num, promisePool, realizedSummaryUrl, setRefreshControlsDisabled, setRefreshProgressBar, setText, showToast, updateUnderlyingIndexCache } from "./domain.js";
import { loadChartJs } from "./chart-vendor.js";
import { formatTimeHms } from "./date-time.js";
import { aprSeriesUrl, renderAprChart, renderBookEquityChart, renderCumulativePnlChart, renderDailyPnlChart, scheduleChartResizeAll } from "./charts.js";
import { renderAccountCards, renderAggregate, renderBookCards, renderRecentActivity, renderRegime, renderStrategyGroups, renderStress } from "./render.js";

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

export function updateHeaderSpotDom() {
  const elBtc = document.getElementById("header-spot-btc");
  const elEth = document.getElementById("header-spot-eth");
  const b = STATE.lastSpotUsd.BTC;
  const e = STATE.lastSpotUsd.ETH;
  if (elBtc) elBtc.textContent = b !== null && b > 0 ? `BTC ${fmt.usd2.format(b)}` : "BTC —";
  if (elEth) elEth.textContent = e !== null && e > 0 ? `ETH ${fmt.usd2.format(e)}` : "ETH —";
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
    showToast(`charts: ${err.message}`);
    return;
  }
  const chartFns = [
    ["risk-capital", renderBookEquityChart],
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
  if (includeCharts) steps += 2;
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
  set("eyebrow", "Please wait", "請稍候");
  set("title", "Loading your portfolio", "正在載入您的投資組合");
  set(
    "hint",
    "Showing snapshot first; live positions and P&L sync in the background.",
    "先顯示最近快照；持倉與損益於背景同步中。"
  );
}

export function beginInvestorLoad({ blocking = true } = {}) {
  if (!INVESTOR) return;
  STATE.investorLoadDone = 0;
  STATE.investorLoadTotal = investorLoadStepCount(false);
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
    if (updateDom) {
      updateHeaderSpotDom();
      if (renderDependentViews && !STATE.refreshInFlight) {
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

export async function fetchPortfolioSnapshot() {
  try {
    const d = await fetchJson("/api/portfolio/snapshot");
    STATE.portfolioSnapshot = d;
    if (d?.realized_summary) {
      STATE.report = d.realized_summary;
      STATE.summaryLoadPending = false;
      STATE.summaryLoadInFlight = false;
    }
    if (d?.source === "ledger") {
      STATE.dataFreshness.source = "snapshot";
      STATE.dataFreshness.snapshotMs = num(d.freshness_ms);
      STATE.dataFreshness.live = false;
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
    STATE.status = d;
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
          STATE.status = d;
          STATE.dataFreshness.source = "live";
          STATE.dataFreshness.live = true;
          invokeRenderDashboard();
        })
        .catch(() => {});
      return null;
    }
    STATE.status = null;
    if (!STATE.statusErrorOnce) {
      showToast(`status: ${err.message}`);
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
      showToast(`dashboard bundle: ${err.message}`);
    }
    return false;
  }
}

async function fetchChartSeries(investorFetchWrap = null) {
  const fetchCumulative = () =>
    fetchJson("/api/cumulative_pnl_series")
      .then((d) => {
        STATE.cumulativePnl = d;
      })
      .catch((err) => showToast(`cumulative pnl: ${err.message}`));

  const fetchApr = () =>
    fetchJson(aprSeriesUrl())
      .then((d) => {
        STATE.aprSeries = d;
      })
      .catch((err) => showToast(`apr series: ${err.message}`));

  if (investorFetchWrap) {
    await Promise.all([
      investorFetchWrap("cumulative", fetchCumulative),
      investorFetchWrap("apr", fetchApr),
    ]);
  } else {
    await Promise.all([fetchCumulative(), fetchApr()]);
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
    showToast(`stress: ${err.message}`);
  } finally {
    STATE.stressLoadInFlight = false;
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
    beginInvestorLoad({ blocking: true });
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
      const hasPortfolio = snap?.source === "ledger" && snap?.portfolio;
      const hasSummary = Boolean(STATE.report?.summary);
      if (!hasPortfolio && !hasSummary) return;
      if (!STATE.investorReady) {
        setInvestorPageReady(true);
        setRefreshProgressBar(true, { indeterminate: true });
      }
      scheduleRender();
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
            showToast(`realized summary: ${err.message}`);
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
      if (investorFirstLoad) {
        STATE.investorLoadTotal = investorLoadStepCount(Boolean(d?.has_private_creds), {
          includeCharts: chartsSectionOpen(),
        });
      }
      if (d?.has_private_creds) {
        if (INVESTOR && investorFirstLoad) getSnapshotLoad();
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
      showToast(`health failed: ${err.message}`);
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
        setInvestorPageReady(true);
        setRefreshProgressBar(true, { indeterminate: true });
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
          showToast(`groups: ${err.message}`);
        });
    }

    function fetchSummary() {
      return ensureSummaryLoad();
    }

    function fetchStatusOp() {
      return fetchJson("/api/status")
        .then((d) => {
          STATE.status = d;
          STATE.statusErrorOnce = false;
          scheduleRender();
        })
        .catch((err) => {
          STATE.status = null;
          if (!STATE.statusErrorOnce) {
            showToast(`status: ${err.message}`);
            STATE.statusErrorOnce = true;
          }
        });
    }

    function fetchStress() {
      return loadStressIfNeeded({ force: true });
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

    if (!INVESTOR || STATE.investorReady) {
      scheduleRender();
    }

    setRefreshProgressBar(false);
    setRefreshControlsDisabled(false);
    setText(
      "last-refresh",
      `${i18n("last refresh (local time):", "上次更新（本地時間）：")} ${formatTimeHms()}`
    );
  } finally {
    STATE.refreshInFlight = false;
    setRefreshProgressBar(false);
    setRefreshControlsDisabled(false);
    activeRenderDashboard = null;
  }
}
