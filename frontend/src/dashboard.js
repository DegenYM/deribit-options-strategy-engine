import { INVESTOR } from "./shared/context.js";
import { FRONTEND_REFRESH_INTERVAL_MS } from "./shared/config.js";
import { STATE } from "./shared/state.js";
import * as domain from "./modules/domain.js";
import * as charts from "./modules/charts.js";
import * as render from "./modules/render.js";
import * as refresh from "./modules/refresh.js";
import { loadChartJs } from "./modules/chart-vendor.js";
import { aggregateSkeletonHtml } from "./modules/domain.js";

function ensureAggregateSkeleton() {
  const root = document.getElementById("aggregate-card");
  if (!root) return;
  if (root.querySelector(".overview-metrics-grid, .inv-dashboard, .overview-metric-cell")) return;
  root.innerHTML = aggregateSkeletonHtml();
}

export function renderDashboard() {
  domain.updateUnderlyingIndexCache(STATE.status, STATE.groups);
  render.renderRegime(STATE.status);
  render.renderTopBar(STATE.health);
  refresh.updateHeaderSpotDom();
  if (!INVESTOR) {
    render.renderAccountCards(STATE.health, STATE.status);
    render.renderBookCards(STATE.status);
  }
  render.renderAggregate(STATE.status, STATE.report);
  render.renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
  refresh.renderPerformanceCharts().catch((err) => {
    console.error("performance charts failed", err);
  });
  render.renderRecentActivity(STATE.status, STATE.report, STATE.groups);
  if (!INVESTOR) {
    render.renderStress(STATE.stress);
  }
}

function setBookFilter(book) {
  STATE.bookFilter = book;
  const filterRoot = document.querySelector("#book-filter");
  if (filterRoot) {
    filterRoot.querySelectorAll("button[data-book]").forEach((btn) => {
      btn.classList.toggle("filter-active", btn.dataset.book === book);
    });
  }
  loadChartJs()
    .then(() => {
      charts.renderBookEquityChart();
      charts.renderCumulativePnlChart();
      charts.renderDailyPnlChart();
      charts.scheduleChartResizeAll();
    })
    .catch((err) =>
      domain.showToast(`charts: ${err.message}`, { retry: () => setBookFilter(book) })
    );
}

function attachAutoRefresh() {
  const checkbox = document.getElementById("auto-refresh");
  if (!checkbox) return;
  function reset() {
    if (STATE.autoRefreshHandle) {
      clearInterval(STATE.autoRefreshHandle);
      STATE.autoRefreshHandle = null;
    }
    if (checkbox.checked) {
      STATE.autoRefreshHandle = setInterval(
        () => refresh.refreshAll({ silentIfLimited: true, renderDashboard }),
        FRONTEND_REFRESH_INTERVAL_MS
      );
    }
  }
  checkbox.addEventListener("change", reset);
  reset();
}

function attachControls() {
  document.getElementById("refresh-now")?.addEventListener("click", () =>
    refresh.refreshAll({ renderDashboard })
  );
  document.getElementById("book-filter")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-book]");
    if (btn) setBookFilter(btn.dataset.book);
  });
  document.getElementById("activity-section")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button.activity-page-btn");
    if (!btn || btn.disabled) return;
    const section = btn.dataset.activitySection;
    const dir = btn.dataset.direction === "next" ? 1 : -1;
    if (section === "open") STATE.activityOpenPage += dir;
    else if (section === "closed") STATE.activityClosedPage += dir;
    render.renderRecentActivity(STATE.status, STATE.report, STATE.groups);
  });
  const reloadAprSeries = async () => {
    try {
      await loadChartJs();
      STATE.aprSeries = await domain.fetchJson(charts.aprSeriesUrl());
    } catch (err) {
      domain.showToast(`apr series: ${err.message}`, { retry: reloadAprSeries });
    }
    charts.renderAprChart();
  };
  document.getElementById("apr-window")?.addEventListener("change", (e) => {
    STATE.aprWindow = parseInt(e.target.value, 10) || 30;
    reloadAprSeries();
  });
}

function attachSectionHoverPrefetch(id, onPrefetch) {
  const summary = document.getElementById(id)?.querySelector("summary");
  if (!summary) return;
  summary.addEventListener("mouseenter", onPrefetch, { once: true });
}

function attachChartHoverPrefetch() {
  attachSectionHoverPrefetch("charts-section", () => {
    loadChartJs().catch(() => {});
  });
}

function attachStressHoverPrefetch() {
  attachSectionHoverPrefetch("stress-section", () => {
    refresh.loadStressIfNeeded().catch(() => {});
  });
}

const SECTION_STATE_PREFIX = "dash:section:";

function readSavedSectionState(id) {
  try {
    return window.localStorage.getItem(`${SECTION_STATE_PREFIX}${id}`);
  } catch {
    return null;
  }
}

function saveSectionState(id, open) {
  try {
    window.localStorage.setItem(`${SECTION_STATE_PREFIX}${id}`, open ? "open" : "closed");
  } catch {
    /* ignore storage failures (private mode, quota, etc.) */
  }
}

function attachExpandableSections() {
  document.querySelectorAll("details.collapsible-section").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (details.id) saveSectionState(details.id, details.open);
      if (!details.open) return;
      if (details.id === "charts-section") {
        refresh
          .loadChartDataIfNeeded()
          .then(() => charts.scheduleChartResizeAll())
          .catch((err) => {
            console.error("chart data load failed", err);
          });
        return;
      }
      if (details.id === "stress-section") {
        refresh.loadStressIfNeeded().catch((err) => {
          console.error("stress load failed", err);
        });
        return;
      }
      if (details.id === "strategies-section") {
        render.renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
        return;
      }
      if (details.id === "account-section") {
        render.renderAccountCards(STATE.health, STATE.status);
        return;
      }
      if (details.id === "books-section") {
        render.renderBookCards(STATE.status);
        return;
      }
      charts.scheduleChartResizeAll();
    });

    if (details.id) {
      const saved = readSavedSectionState(details.id);
      if (saved === "open" && !details.open) {
        details.open = true;
      } else if (saved === "closed" && details.open) {
        details.open = false;
      }
    }
  });
}

export function initDashboard() {
  refresh.registerRenderDashboard(renderDashboard);
  const boot = () => {
    ensureAggregateSkeleton();
    refresh.applyInvestorLoadCopy();
    charts.attachChartResizeObservers();
    attachControls();
    attachExpandableSections();
    attachChartHoverPrefetch();
    attachStressHoverPrefetch();
    attachAutoRefresh();
    refresh.startLastRefreshTicker();
    refresh.refreshAll({ force: true, renderDashboard });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}
