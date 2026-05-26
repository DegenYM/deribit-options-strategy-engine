import { INVESTOR } from "./shared/context.js";
import { FRONTEND_REFRESH_INTERVAL_MS } from "./shared/config.js";
import { STATE } from "./shared/state.js";
import * as domain from "./modules/domain.js";
import * as charts from "./modules/charts.js";
import * as render from "./modules/render.js";
import * as refresh from "./modules/refresh.js";

export function renderDashboard() {
  domain.updateUnderlyingIndexCache(STATE.status, STATE.groups);
  render.renderRegime(STATE.status);
  render.renderTopBar(STATE.health);
  refresh.updateHeaderSpotDom();
  render.renderAccountCards(STATE.health, STATE.status);
  render.renderBookCards(STATE.status);
  render.renderAggregate(STATE.status, STATE.report);
  render.renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
  refresh.renderPerformanceCharts();
  render.renderRecentActivity(STATE.status, STATE.report, STATE.groups);
  render.renderStress(STATE.stress);
}

function setBookFilter(book) {
  STATE.bookFilter = book;
  const filterRoot = document.querySelector("#book-filter");
  if (filterRoot) {
    filterRoot.querySelectorAll("button[data-book]").forEach((btn) => {
      btn.classList.toggle("filter-active", btn.dataset.book === book);
    });
  }
  charts.renderBookEquityChart();
  charts.renderCumulativePnlChart();
  charts.renderDailyPnlChart();
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
  document.getElementById("apr-window")?.addEventListener("change", async (e) => {
    STATE.aprWindow = parseInt(e.target.value, 10) || 30;
    try {
      STATE.aprSeries = await domain.fetchJson(charts.aprSeriesUrl());
    } catch (err) {
      domain.showToast(`apr series: ${err.message}`);
    }
    charts.renderAprChart();
  });
}

function attachExpandableSections() {
  document.querySelectorAll("details.collapsible-section").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (!details.open) return;
      charts.scheduleChartResizeAll();
      if (INVESTOR && details.id === "charts-section") {
        refresh.loadChartDataIfNeeded({ renderDashboard });
      }
    });
  });
}

export function initDashboard() {
  const boot = () => {
    refresh.applyInvestorLoadCopy();
    charts.attachChartResizeObservers();
    attachControls();
    attachExpandableSections();
    attachAutoRefresh();
    refresh.refreshAll({ force: true, renderDashboard });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}
