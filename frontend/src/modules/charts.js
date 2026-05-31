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
import { bookEquityNative, bookEquityUsdForDisplay, dashboardStrategyIds, dedupeTradeGroups, fmtNum, fmtPct, fmtUsd, isDashboardStrategy, isDisplayableClosedTradeGroup, num, openRowEntryCreditUsd, pnlClass, realizedPnlDisplayUsdc, realizedPnlInAprBookNative, resolvedPortfolio, setText, strategyId, strategyInfo, strategyOrder, tradeGroupAprBook, closedTimestampMs, aprEffectiveCapitalUsdc } from "./domain.js";
export function chartCommonOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: {
      legend: {
        labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 },
      },
      tooltip: {
        backgroundColor: "rgba(15,23,42,0.95)",
        borderColor: "rgb(51,65,85)",
        borderWidth: 1,
        titleColor: "rgb(226,232,240)",
        bodyColor: "rgb(226,232,240)",
      },
    },
    scales: {
      x: {
        type: "time",
        time: { tooltipFormat: "yyyy-LL-dd HH:mm" },
        grid: { color: "rgba(51,65,85,0.4)" },
        ticks: { color: "rgb(148,163,184)" },
      },
      y: {
        grid: { color: "rgba(51,65,85,0.4)" },
        ticks: { color: "rgb(148,163,184)" },
      },
    },
  };
}

export function destroyChart(key) {
  const chart = STATE.charts[key];
  if (!chart) return;
  const canvas = chart.canvas;
  chart.destroy();
  STATE.charts[key] = null;
  if (canvas) {
    canvas.removeAttribute("width");
    canvas.removeAttribute("height");
    canvas.style.width = "";
    canvas.style.height = "";
  }
}

export function chartCanvasContext(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return canvas.getContext("2d");
}

export function resizeAllCharts() {
  Object.values(STATE.charts).forEach((chart) => {
    try {
      chart?.resize?.();
    } catch (_) {
      /* ignore */
    }
  });
}

export function scheduleChartResizeAll() {
  requestAnimationFrame(() => {
    resizeAllCharts();
    window.setTimeout(resizeAllCharts, 80);
    window.setTimeout(resizeAllCharts, 320);
  });
}

export let chartResizeObserversAttached = false;

export function attachChartResizeObservers() {
  if (chartResizeObserversAttached || typeof ResizeObserver === "undefined") return;
  chartResizeObserversAttached = true;
  document.querySelectorAll(".chart-panel-canvas").forEach((shell) => {
    const canvas = shell.querySelector("canvas");
    if (!canvas?.id) return;
    new ResizeObserver(() => resizeAllCharts()).observe(shell);
  });
}

/** Live portfolio equity for rolling APR denominator (matches engine effective capital). */
export function aprSeriesUrl() {
  let url = `/api/apr_series?window_days=${STATE.aprWindow}`;
  const cap = aprEffectiveCapitalUsdc();
  if (cap !== null) {
    url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
  }
  return url;
}

export function defaultEmptyChartTimeBounds() {
  const end = globalThis.luxon.DateTime.now().toUTC().startOf("day");
  const start = end.minus({ days: Math.max(STATE.aprWindow, 30) });
  return { min: start.toMillis(), max: end.toMillis() };
}

export function chartPanelShell(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  // Prefer explicit panel wrapper; fall back to canvas parent for older cached HTML.
  return canvas.closest(".chart-panel-canvas") || canvas.parentElement;
}

export function setChartPanelEmpty(canvasId, { empty, message = "" } = {}) {
  const shell = chartPanelShell(canvasId);
  if (!shell) return;
  let overlay = shell.querySelector(".chart-empty-overlay");
  if (!empty) {
    overlay?.remove();
    shell.classList.remove("chart-panel-canvas--empty");
    return;
  }
  shell.classList.add("chart-panel-canvas--empty");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "chart-empty-overlay";
    shell.appendChild(overlay);
  }
  overlay.textContent = message;
}

const EMPTY_CHART_COPY = {
  realized: {
    en: "No closed positions yet — this chart fills in after the first close.",
    zh: "尚無平倉紀錄 — 首次平倉後此圖表才會開始累積。",
  },
  apr: {
    en: "Rolling APR needs closed trades and daily equity snapshots.",
    zh: "滾動年化需有平倉紀錄與每日權益快照。",
  },
};

export function emptyChartMessage(kind) {
  const copy = EMPTY_CHART_COPY[kind] || EMPTY_CHART_COPY.realized;
  return i18n(copy.en, copy.zh);
}

export function emptyChartScaleOptions({ yPercent = false, chartType = "line" } = {}) {
  const xBounds = defaultEmptyChartTimeBounds();
  const base = chartCommonOptions();
  const yMin = yPercent ? -0.1 : -50;
  const yMax = yPercent ? 0.1 : 50;
  return {
    ...base,
    plugins: {
      ...base.plugins,
      legend: { display: false },
      tooltip: { enabled: false },
    },
    scales: {
      x: {
        ...base.scales.x,
        ...xBounds,
        display: true,
        offset: chartType === "bar",
        time: {
          unit: "day",
          round: "day",
          tooltipFormat: "yyyy-LL-dd",
        },
      },
      y: {
        ...base.scales.y,
        display: true,
        min: yMin,
        max: yMax,
        ticks: {
          ...base.scales.y.ticks,
          maxTicksLimit: 6,
          ...(yPercent ? { callback: (v) => fmtPct(v, 1) } : {}),
        },
      },
    },
  };
}

export function mountEmptyTimeSeriesChart(
  canvasId,
  key,
  { yPercent = false, chartType = "line", messageKind = "realized" } = {}
) {
  const ctx = chartCanvasContext(canvasId);
  if (!ctx) return;
  destroyChart(key);
  setChartPanelEmpty(canvasId, {
    empty: true,
    message: emptyChartMessage(messageKind),
  });
  const xBounds = defaultEmptyChartTimeBounds();
  const placeholder = [
    { x: xBounds.min, y: 0 },
    { x: xBounds.max, y: 0 },
  ];
  // Skeleton uses a line at y=0 so axes/grid render reliably (bar placeholders are invisible).
  STATE.charts[key] = new globalThis.Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: i18n("No realized history yet", "尚無已實現紀錄"),
          data: placeholder,
          borderWidth: 1,
          pointRadius: 0,
          borderColor: "rgba(148, 163, 184, 0.35)",
          backgroundColor: "transparent",
        },
      ],
    },
    options: emptyChartScaleOptions({ yPercent, chartType }),
  });
}

export function visibleBooks() {
  if (STATE.bookFilter === "ALL") return CORE_BOOKS;
  return [STATE.bookFilter];
}

/** Collateral book for an open trade group (matches engine grouping). */
export function tradeGroupCollateral(g) {
  const c = String(g.collateral_currency || "").toUpperCase();
  if (c) return c;
  const ins = String(g.short_instrument_name || "");
  if (ins.includes("_USDC-")) return "USDC";
  return String(g.currency || "").toUpperCase() || "";
}

export function sumOpenCreditByBook(openGroups) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  for (const g of openGroups || []) {
    const book = tradeGroupCollateral(g);
    const credit = openRowEntryCreditUsd(g, STATE.status, STATE.groups);
    if (credit === null || credit <= 0) continue;
    if (book === "BTC" || book === "ETH" || book === "USDC") out[book] += credit;
  }
  return out;
}

export function sumOpenCreditByStrategy(openRows, status, groups) {
  const out = Object.fromEntries(dashboardStrategyIds().map((id) => [id, 0]));
  for (const g of openRows || []) {
    const id = strategyId(g);
    if (!isDashboardStrategy(id)) continue;
    const credit = openRowEntryCreditUsd(g, status, groups);
    if (credit === null) continue;
    out[id] += credit;
  }
  return out;
}

/** Closed groups with realized PnL (full ``groups.closed`` + report enrich). */
export function lifetimeRealizedClosedRows(report, groups, status = null) {
  const st = status ?? STATE.status;
  return dedupeTradeGroups([
    ...(groups?.closed || []),
    ...(report?.recent_closed_trades || []),
  ])
    .filter((g) => isDisplayableClosedTradeGroup(g, st, groups))
    .filter((g) => num(g?.realized_pnl) !== null);
}

export function sumLifetimeRealizedPnlNativeByBook(report, groups, status) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  for (const g of lifetimeRealizedClosedRows(report, groups)) {
    const book = tradeGroupAprBook(g);
    if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
    const native = realizedPnlInAprBookNative(g, status);
    if (native === null) continue;
    out[book] += native;
  }
  return out;
}

/** Lifetime Total profit in USDC using live index for coin-collateral rows. */
export function sumLifetimeRealizedPnlUsdcAtSpot(report, groups, status) {
  let sum = 0;
  let any = false;
  for (const g of lifetimeRealizedClosedRows(report, groups, status)) {
    const pnl = realizedPnlDisplayUsdc(g, status);
    if (pnl === null) continue;
    sum += pnl;
    any = true;
  }
  return any ? sum : null;
}

export function sumWindowRealizedPnlUsdcAtSpot(report, groups, status, windowDays) {
  const days = windowDays ?? 30;
  const cutoffMs = Date.now() - days * 24 * 3600 * 1000;
  let sum = 0;
  let any = false;
  for (const g of lifetimeRealizedClosedRows(report, groups, status)) {
    const closedMs = closedTimestampMs(g);
    if (closedMs === null || closedMs < cutoffMs) continue;
    const pnl = realizedPnlDisplayUsdc(g, status);
    if (pnl === null) continue;
    sum += pnl;
    any = true;
  }
  return any ? sum : null;
}

export function sumWindowRealizedPnlNativeByBook(report, groups, status, windowDays) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  const days = windowDays ?? 30;
  const cutoffMs = Date.now() - days * 24 * 3600 * 1000;
  for (const g of lifetimeRealizedClosedRows(report, groups)) {
    const closedMs = closedTimestampMs(g);
    if (closedMs === null || closedMs < cutoffMs) continue;
    const book = tradeGroupAprBook(g);
    if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
    const native = realizedPnlInAprBookNative(g, status);
    if (native === null) continue;
    out[book] += native;
  }
  return out;
}

export function bookEquityNativeByBook(status) {
  const out = {};
  let any = false;
  for (const book of CORE_BOOKS) {
    const n = bookEquityNative(status, book);
    out[book] = n;
    if (n !== null) any = true;
  }
  if (!any) {
    const { portfolio } = resolvedPortfolio();
    for (const book of CORE_BOOKS) {
      if (book === "USDC") {
        out[book] = num(portfolio?.equity_by_book?.[book]);
        continue;
      }
      const usd = num(portfolio?.equity_by_book?.[book]);
      const spot =
        num(status?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
      out[book] =
        usd !== null && spot !== null && spot > 0 ? usd / spot : null;
    }
  }
  return out;
}

export function openTradeGroupsForRisk() {
  const tg = STATE.status?.trade_groups;
  if (tg && tg.length) return tg;
  return STATE.groups?.open || [];
}

export function riskBarChartBaseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 },
      },
      tooltip: {
        backgroundColor: "rgba(15,23,42,0.95)",
        borderColor: "rgb(51,65,85)",
        borderWidth: 1,
        titleColor: "rgb(226,232,240)",
        bodyColor: "rgb(226,232,240)",
      },
    },
    scales: {
      x: {
        grid: { color: "rgba(51,65,85,0.4)" },
        ticks: { color: "rgb(148,163,184)" },
      },
      y: {
        beginAtZero: true,
        grid: { color: "rgba(51,65,85,0.4)" },
        ticks: {
          color: "rgb(148,163,184)",
          maxTicksLimit: 8,
        },
      },
    },
  };
}

export function renderBookEquityChart() {
  const ctx = chartCanvasContext("chart-risk-capital");
  if (!ctx) return;
  destroyChart("riskCapital");

  const books = visibleBooks();
  const portfolio = STATE.status?.portfolio;

  const equityBars = books.map((b) => {
    const v = bookEquityUsdForDisplay(b, STATE.status);
    return v !== null ? v : 0;
  });

  const totEq = num(portfolio?.total_equity_usdc);
  const sumBars = equityBars.reduce((a, b) => a + b, 0);
  let meta = i18n(`Total ${fmtUsd(totEq)}`, `合計 ${fmtUsd(totEq)}`);
  if (totEq !== null && sumBars > 0 && Math.abs(sumBars - totEq) > 1) {
    meta += i18n(" · bars sum may differ from headline", " · 各帳加總可能與總覽略有差異");
  } else if (!STATE.status) {
    meta = i18n("Awaiting live snapshot", "等待即時快照");
  }

  setText("risk-capital-meta", meta);
  setText(
    "risk-capital-hint",
    i18n(
      "Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).",
      "各帳本權益以 USDC 約當顯示，來自即時或最近快照。"
    )
  );

  const barColors = books.map((b) => BOOK_COLORS[b] || "#94a3b8");
  const baseOpts = riskBarChartBaseOptions();
  setChartPanelEmpty("chart-risk-capital", { empty: false });

  STATE.charts.riskCapital = new globalThis.Chart(ctx, {
    type: "bar",
    data: {
      labels: books,
      datasets: [
        {
          label: i18n("Book equity (USDC eq.)", "帳本權益（USDC 約當）"),
          data: equityBars,
          backgroundColor: barColors.map((c) => c + "cc"),
          borderColor: barColors,
          borderWidth: 1,
        },
      ],
    },
    options: {
      ...baseOpts,
      plugins: {
        ...baseOpts.plugins,
        tooltip: {
          ...baseOpts.plugins.tooltip,
          callbacks: {
            afterBody(items) {
              if (!items?.length) return "";
              const i = items[0].dataIndex;
              if (i === undefined) return "";
              const eq = equityBars[i] ?? 0;
              const share = totEq > 0 ? eq / totEq : null;
              const lines = [
                `${i18n("Share of total: ", "佔總權益：")}${fmtPct(share, 2)}`,
              ];
              return lines;
            },
          },
        },
      },
    },
  });
}

const MS_PER_DAY = 86400000;

export function dateToMs(dateStr) {
  const dt = globalThis.luxon.DateTime.fromISO(String(dateStr || "").trim(), { zone: "utc" });
  if (!dt.isValid) return NaN;
  return dt.toMillis();
}

/**
 * Cumulative PnL is bucketed by UTC day; a single day yields one xy pair. Chart.js time scale
 * then zooms to a ~1ms window, and stepped lines with pointRadius 0 draw nothing. Pad with a
 * zero baseline and a trailing flat point so the axis and polylines render.
 */
export function finalizeCumulativeLineData(rawPoints) {
  const pts = rawPoints
    .filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y))
    .sort((a, b) => a.x - b.x);
  if (pts.length === 0) return [];
  if (pts.length === 1) {
    const p = pts[0];
    return [
      { x: p.x - MS_PER_DAY, y: 0 },
      { x: p.x, y: p.y },
      { x: p.x + MS_PER_DAY, y: p.y },
    ];
  }
  return pts;
}

export function filterValidTimePoints(rawPoints) {
  return rawPoints
    .filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y))
    .sort((a, b) => a.x - b.x);
}

/** APR / moving-average lines: one point does not draw a segment with pointRadius 0. */
export function finalizeSimpleLineData(rawPoints) {
  const pts = filterValidTimePoints(rawPoints);
  if (pts.length === 0) return [];
  if (pts.length === 1) {
    const p = pts[0];
    return [p, { x: p.x + MS_PER_DAY, y: p.y }];
  }
  return pts;
}

/** Single bucket or one timestamp → Chart.js time scale collapses to ms-wide window (bars + lines). */
export function suggestTimeScaleMinMax(flatPoints) {
  const xs = (flatPoints || []).map((p) => p.x).filter(Number.isFinite);
  if (!xs.length) return {};
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  const span = hi - lo;
  const pad = MS_PER_DAY;
  if (xs.length === 1 || span < pad * 0.25) {
    return { min: lo - pad, max: hi + pad };
  }
  return {};
}

export function renderCumulativePnlChart() {
  const ctx = chartCanvasContext("chart-cum-pnl");
  if (!ctx) return;
  destroyChart("cumPnl");
  const series = STATE.cumulativePnl;
  const closedMeta = series?.realized_count
    ? `${series.realized_count} closed groups`
    : i18n("no closed groups", "尚無已平倉組");
  setText("cum-pnl-meta", closedMeta);
  if (!series) {
    mountEmptyTimeSeriesChart("chart-cum-pnl", "cumPnl");
    return;
  }
  const datasets = [];
  const books = visibleBooks();
  for (const book of books) {
    const rows = series.cumulative_by_book?.[book] || [];
    if (rows.length) {
      const data = finalizeCumulativeLineData(
        rows.map((r) => ({ x: dateToMs(r.date), y: num(r.pnl_usdc) }))
      );
      if (data.length) {
        datasets.push({
          label: `${book} cum. PnL`,
          data,
          borderColor: BOOK_COLORS[book],
          backgroundColor: BOOK_COLORS[book] + "22",
          stepped: true,
          pointRadius: 0,
          borderWidth: 2,
        });
      }
    }
  }
  if (STATE.bookFilter === "ALL" && series.cumulative_total?.length) {
    const data = finalizeCumulativeLineData(
      series.cumulative_total.map((r) => ({
        x: dateToMs(r.date),
        y: num(r.pnl_usdc),
      }))
    );
    if (data.length) {
      datasets.push({
        label: "Total cum. PnL",
        data,
        borderColor: BOOK_COLORS.TOTAL,
        backgroundColor: BOOK_COLORS.TOTAL + "22",
        stepped: true,
        pointRadius: 0,
        borderWidth: 2,
        borderDash: [4, 4],
      });
    }
  }
  if (!datasets.length) {
    mountEmptyTimeSeriesChart("chart-cum-pnl", "cumPnl");
    return;
  }
  setChartPanelEmpty("chart-cum-pnl", { empty: false });
  STATE.charts.cumPnl = new globalThis.Chart(ctx, {
    type: "line",
    data: { datasets },
    options: chartCommonOptions(),
  });
}

/** Optional: drop zero-height bars (per-book series has many explicit zeros). */
export function compactNonZeroDailyBars(points) {
  return points.filter((p) => Math.abs(p.y) > 1e-12);
}

const DAILY_PNL_PROFIT_FILL = "rgba(52, 211, 153, 0.67)";
const DAILY_PNL_PROFIT_BORDER = "#34d399";
const DAILY_PNL_LOSS_FILL = "rgba(251, 113, 133, 0.67)";
const DAILY_PNL_LOSS_BORDER = "#fb7185";

export function dailyPnlBarFillColors(points) {
  return points.map((p) => {
    const y = num(p.y) ?? 0;
    if (y > 0) return DAILY_PNL_PROFIT_FILL;
    if (y < 0) return DAILY_PNL_LOSS_FILL;
    return "rgba(148, 163, 184, 0.4)";
  });
}

export function dailyPnlBarBorderColors(points) {
  return points.map((p) => {
    const y = num(p.y) ?? 0;
    if (y > 0) return DAILY_PNL_PROFIT_BORDER;
    if (y < 0) return DAILY_PNL_LOSS_BORDER;
    return "#94a3b8";
  });
}

export function renderDailyPnlChart() {
  const ctx = chartCanvasContext("chart-daily-pnl");
  if (!ctx) return;
  destroyChart("dailyPnl");
  const MA_WINDOW = 30;
  const series = STATE.cumulativePnl;
  if (!series) {
    setText("daily-pnl-meta", i18n("no closed groups", "尚無已平倉組"));
    mountEmptyTimeSeriesChart("chart-daily-pnl", "dailyPnl", { chartType: "bar" });
    return;
  }
  const books = visibleBooks();
  const validDaily = (series.daily_total || []).filter((r) => Number.isFinite(dateToMs(r.date)));
  let meta = series?.daily_total?.length
    ? `${series.daily_total.length} ${i18n("active days", "個有效交易日")}`
    : i18n("no closed groups", "尚無已平倉組");
  if (STATE.bookFilter === "ALL" && validDaily.length >= MA_WINDOW) {
    meta += " · 30d SMA";
  }
  setText("daily-pnl-meta", meta);
  const mapDay = (r) => ({ x: dateToMs(r.date), y: num(r.pnl_usdc) });
  let datasets = [];
  if (STATE.bookFilter === "ALL") {
    const barData = filterValidTimePoints((series.daily_total || []).map(mapDay));
    if (barData.length) {
      datasets.push({
        type: "bar",
        label: i18n("Daily total", "每日合計"),
        data: barData,
        order: 1,
        backgroundColor: dailyPnlBarFillColors(barData),
        borderColor: dailyPnlBarBorderColors(barData),
        borderWidth: 1,
      });
    }
  } else {
    for (const book of books) {
      const rows = series.daily_by_book?.[book] || [];
      let barData = filterValidTimePoints(rows.map(mapDay));
      barData = compactNonZeroDailyBars(barData);
      if (barData.length) {
        datasets.push({
          type: "bar",
          label: `${book} ${i18n("daily", "每日")}`,
          data: barData,
          order: 1,
          backgroundColor: dailyPnlBarFillColors(barData),
          borderColor: dailyPnlBarBorderColors(barData),
          borderWidth: 1,
        });
      }
    }
  }
  if (STATE.bookFilter === "ALL" && validDaily.length >= MA_WINDOW) {
    const maPoints = [];
    for (let i = MA_WINDOW - 1; i < validDaily.length; i++) {
      let sum = 0;
      for (let j = i - MA_WINDOW + 1; j <= i; j++) {
        sum += num(validDaily[j].pnl_usdc) || 0;
      }
      maPoints.push({
        x: dateToMs(validDaily[i].date),
        y: sum / MA_WINDOW,
      });
    }
    const maData = finalizeSimpleLineData(filterValidTimePoints(maPoints));
    if (maData.length) {
      datasets.push({
        type: "line",
        label: `30d SMA (${MA_WINDOW}-day realized avg.)`,
        data: maData,
        order: 2,
        borderColor: "#f472b6",
        backgroundColor: "#f472b633",
        tension: 0.15,
        pointRadius: 0,
        borderWidth: 2,
      });
    }
  }
  if (!datasets.length) {
    mountEmptyTimeSeriesChart("chart-daily-pnl", "dailyPnl", { chartType: "bar" });
    return;
  }
  setChartPanelEmpty("chart-daily-pnl", { empty: false });
  const flatPoints = datasets.flatMap((d) => d.data || []);
  const xBounds = suggestTimeScaleMinMax(flatPoints);
  const base = chartCommonOptions();
  STATE.charts.dailyPnl = new globalThis.Chart(ctx, {
    type: "bar",
    data: { datasets },
    options: {
      ...base,
      scales: {
        x: {
          ...base.scales.x,
          ...xBounds,
          offset: true,
          time: { unit: "day", tooltipFormat: "yyyy-LL-dd" },
        },
        y: {
          ...base.scales.y,
          ticks: {
            ...base.scales.y.ticks,
            maxTicksLimit: 10,
          },
        },
      },
    },
  });
}

export function renderAprChart() {
  const ctx = chartCanvasContext("chart-apr");
  if (!ctx) return;
  destroyChart("apr");
  const rows = STATE.aprSeries?.rows || [];
  const data = finalizeSimpleLineData(
    filterValidTimePoints(rows.map((r) => ({ x: dateToMs(r.date), y: num(r.apr) })))
  );
  if (!data.length) {
    mountEmptyTimeSeriesChart("chart-apr", "apr", { yPercent: true, messageKind: "apr" });
    return;
  }
  setChartPanelEmpty("chart-apr", { empty: false });
  const xBounds = suggestTimeScaleMinMax(data);
  const base = chartCommonOptions();
  STATE.charts.apr = new globalThis.Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: `Rolling APR (${STATE.aprWindow}d)`,
          data,
          borderColor: "#facc15",
          backgroundColor: "rgba(250,204,21,0.15)",
          tension: 0.25,
          pointRadius: 0,
          borderWidth: 2,
          fill: true,
        },
      ],
    },
    options: {
      ...base,
      scales: {
        x: {
          ...base.scales.x,
          ...xBounds,
          time: { unit: "day", tooltipFormat: "yyyy-LL-dd" },
        },
        y: {
          ...base.scales.y,
          ticks: {
            ...base.scales.y.ticks,
            callback: (v) => fmtPct(v, 1),
          },
        },
      },
    },
  });
}
