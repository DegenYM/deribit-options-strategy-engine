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
import { alignProfitDispositionToUsdtWallet, bookEquityNative, bookEquityUsdForDisplay, dashboardStrategyIds, dedupeTradeGroups, emptyProfitDisposition, entryTimestampMs, fmtNativeBookAmount, fmtNum, fmtPct, fmtUsd, isDashboardStrategy, isDisplayableClosedTradeGroup, isMeaningfulNativeForBook, isPremiumProceedsPoolExcludedGroup, num, openRowEntryCreditUsd, pnlClass, profitDispositionForGroup, realizedPnlDisplayUsdc, realizedPnlInAprBookNative, resolvedPortfolio, setText, spotUsdForBook, strategyId, strategyInfo, strategyOrder, summarizeProfitDisposition, tradeGroupAprBook, closedTimestampMs, aprEffectiveCapitalUsdc } from "./domain.js";
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

const CHART_CANVAS_BY_KEY = {
  riskCapital: "chart-risk-capital",
  cumPnl: "chart-cum-pnl",
  dailyPnl: "chart-daily-pnl",
  apr: "chart-apr",
};

function resetCanvasElement(canvas) {
  if (!canvas) return;
  canvas.removeAttribute("width");
  canvas.removeAttribute("height");
  canvas.style.width = "";
  canvas.style.height = "";
}

export function destroyChart(key, canvasId = CHART_CANVAS_BY_KEY[key] || null) {
  const chart = STATE.charts[key];
  if (chart) {
    try {
      chart.destroy();
    } catch (_) {
      /* ignore */
    }
    STATE.charts[key] = null;
    resetCanvasElement(chart.canvas);
  }
  if (!canvasId) return;
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ChartApi = globalThis.Chart;
  const orphan = ChartApi?.getChart?.(canvas);
  if (orphan) {
    try {
      orphan.destroy();
    } catch (_) {
      /* ignore */
    }
    STATE.charts[key] = null;
    resetCanvasElement(canvas);
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
  destroyChart(key, canvasId);
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
    .filter((g) => String(g?.status || "").toLowerCase() === "closed")
    .filter((g) => closedTimestampMs(g) !== null)
    .filter((g) => num(g?.realized_pnl) !== null);
}

/** Per-book lifetime realized USD (same per-group rules as Total profit). */
export function sumLifetimeRealizedPnlUsdcByBook(report, groups, status) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  for (const g of lifetimeRealizedClosedRows(report, groups, status)) {
    const book = tradeGroupAprBook(g);
    if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
    const pnl = realizedPnlDisplayUsdc(g, status);
    if (pnl === null) continue;
    out[book] += pnl;
  }
  return out;
}

/** Per-book lifetime premium profit at live spot (total earned, before swap display split). */
export function sumLifetimeEarnedUsdByBook(report, groups, status) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  for (const g of lifetimeRealizedClosedRows(report, groups, status)) {
    const book = tradeGroupAprBook(g);
    if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
    const native = realizedPnlInAprBookNative(g, status);
    if (native === null) continue;
    if (book === "USDC") {
      out.USDC += native;
    } else {
      const spot = spotUsdForBook(status, book);
      if (spot !== null && spot > 0) out[book] += native * spot;
    }
  }
  return out;
}

/** Per-book profit composition: total earned at spot, unswept native, and swapped USDT. */
export function profitCompositionByBook(report, groups, status) {
  const earnedUsdByBook = sumLifetimeEarnedUsdByBook(report, groups, status);
  const earnedNativeByBook = sumLifetimeRealizedPnlNativeByBook(report, groups, status);
  const usdByBook = sumLifetimeRealizedPnlUsdcByBook(report, groups, status);
  const swappedUsdtByBook = { BTC: 0, ETH: 0, USDC: 0 };
  const swappedNativeByBook = { BTC: 0, ETH: 0, USDC: 0 };
  const nativeByBook = { BTC: 0, ETH: 0, USDC: 0 };
  const disposition = aggregateProfitDisposition(report, groups, status);
  if (disposition) {
    const summary = summarizeProfitDisposition(disposition, { status });
    if (summary) {
      for (const book of ["BTC", "ETH"]) {
        const held = num(summary.spotHeld?.[book]) ?? 0;
        const pending = num(summary.spotPending?.[book]) ?? 0;
        let unswept = held + pending;
        if (!isMeaningfulNativeForBook(unswept, book)) unswept = 0;
        nativeByBook[book] = unswept;
        swappedUsdtByBook[book] = num(summary.spotSoldQuote?.[book]) ?? 0;
        swappedNativeByBook[book] = num(summary.spotSold?.[book]) ?? 0;
      }
    }
    nativeByBook.USDC = num(disposition.heldNative?.USDC) ?? 0;
  }
  return {
    nativeByBook,
    earnedNativeByBook,
    earnedUsdByBook,
    swappedNativeByBook,
    swappedUsdtByBook,
    usdByBook,
  };
}

export function sumLifetimeRealizedPnlNativeByBook(report, groups, status) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  for (const g of lifetimeRealizedClosedRows(report, groups, status)) {
    const book = tradeGroupAprBook(g);
    if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
    const native = realizedPnlInAprBookNative(g, status);
    if (native === null) continue;
    out[book] += native;
  }
  return out;
}

function _aggregateProfitDispositionRows(rows, status) {
  const out = emptyProfitDisposition();
  let any = false;
  for (const g of rows) {
    const disp = profitDispositionForGroup(g, status);
    if (!disp) continue;
    if (disp.book === "USDC") {
      out.heldNative.USDC += disp.held;
    } else {
      out.heldNative[disp.book] += disp.held;
      out.pendingSweepNative[disp.book] += disp.pending;
      out.sweptNativeRef[disp.book] += disp.sweptNative;
      if (disp.sweptUsdt > 0) {
        if (isPremiumProceedsPoolExcludedGroup(g)) {
          out.excludedSweptQuoteProceedsByBook[disp.book] += disp.sweptUsdt;
          if (disp.sweptNative > 0) {
            out.excludedSweptNativeRefByBook[disp.book] += disp.sweptNative;
          }
        } else {
          out.sweptQuoteProceedsByBook[disp.book] += disp.sweptUsdt;
        }
      }
      out.sweptUsdt += disp.sweptUsdt;
    }
    any = true;
  }
  return any ? out : null;
}

/** Held spot profit vs USDT swapped (excludes USDT from held USDC profit line). */
export function aggregateProfitDisposition(report, groups, status, { windowDays = null } = {}) {
  let rows = lifetimeRealizedClosedRows(report, groups, status);
  if (windowDays != null) {
    const cutoffMs = Date.now() - windowDays * 24 * 3600 * 1000;
    rows = rows.filter((g) => {
      const closedMs = closedTimestampMs(g);
      return closedMs !== null && closedMs >= cutoffMs;
    });
  }
  const disposition = _aggregateProfitDispositionRows(rows, status);
  return disposition ? alignProfitDispositionToUsdtWallet(disposition, status) : null;
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

/** Match backend ``_annualize_apr``. */
export function annualizeRealizedApr(pnl, sampleDays, capital) {
  const p = num(pnl);
  const days = num(sampleDays);
  const cap = num(capital);
  if (p === null || days === null || cap === null) return null;
  if (p === 0 || days <= 0 || cap <= 0) return 0;
  return (p / cap) * (365 / days);
}

/** Match backend ``_realized_sample_days`` (earliest entry → latest close). */
export function realizedSampleDaysFromRows(rows) {
  let startMs = null;
  let endMs = null;
  for (const g of rows) {
    const closed = closedTimestampMs(g);
    const entry = entryTimestampMs(g);
    if (closed === null || entry === null || entry <= 0) continue;
    if (startMs === null || entry < startMs) startMs = entry;
    if (endMs === null || closed > endMs) endMs = closed;
  }
  if (startMs === null || endMs === null || endMs <= startMs) return null;
  return (endMs - startMs) / (24 * 3600 * 1000);
}

export function resolveAprEffectiveCapital(summary, status) {
  const fromSummary = num(summary?.effective_capital_usdc);
  if (fromSummary !== null && fromSummary > 0) return fromSummary;
  const live = aprEffectiveCapitalUsdc();
  if (live !== null && live > 0) return live;
  const eq = num(status?.portfolio?.total_equity_usdc);
  return eq !== null && eq > 0 ? eq : null;
}

/** Lifetime APR from frontend spot/swap PnL (same source as Total profit). */
export function computeLifetimeRealizedApr(report, groups, status, summary) {
  const rows = lifetimeRealizedClosedRows(report, groups, status);
  const pnl = sumLifetimeRealizedPnlUsdcAtSpot(report, groups, status);
  if (pnl === null) return null;
  const sampleDays = realizedSampleDaysFromRows(rows) ?? num(summary?.lifetime_sample_days);
  const capital = resolveAprEffectiveCapital(summary, status);
  return annualizeRealizedApr(pnl, sampleDays, capital);
}

/** Window APR from frontend spot/swap PnL (fixed calendar window, same as backend). */
export function computeWindowRealizedApr(report, groups, status, summary, windowDays) {
  const days = windowDays ?? num(summary?.window_days_used) ?? 30;
  const pnl = sumWindowRealizedPnlUsdcAtSpot(report, groups, status, days);
  if (pnl === null) return null;
  const capital = resolveAprEffectiveCapital(summary, status);
  return annualizeRealizedApr(pnl, days, capital);
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

const SPOT_PNL_BOOKS = ["BTC", "ETH"];
const SPOT_PNL_AXIS_IDS = { BTC: "y", ETH: "y1" };

function visibleSpotPnlBooks() {
  return visibleBooks().filter((book) => SPOT_PNL_BOOKS.includes(book));
}

function spotPnlUsesDualAxis(datasets) {
  return datasets.some((d) => d.book === "BTC") && datasets.some((d) => d.book === "ETH");
}

function spotPnlChartScales(base, { datasets, xBounds }) {
  const useDualAxis = spotPnlUsesDualAxis(datasets);
  const singleBook = datasets.length === 1 ? datasets[0].book : null;

  if (!useDualAxis) {
    const book = singleBook || datasets[0]?.book || "BTC";
    return {
      x: { ...base.scales.x, ...xBounds },
      y: {
        ...base.scales.y,
        ticks: {
          ...base.scales.y.ticks,
          color: BOOK_COLORS[book] || base.scales.y.ticks?.color,
          callback(value) {
            return fmtNativeBookAmount(value, book);
          },
        },
      },
    };
  }

  return {
    x: { ...base.scales.x, ...xBounds },
    y: {
      type: "linear",
      position: "left",
      display: true,
      grid: { color: "rgba(51,65,85,0.4)" },
      ticks: {
        color: BOOK_COLORS.BTC,
        callback(value) {
          return fmtNativeBookAmount(value, "BTC");
        },
      },
    },
    y1: {
      type: "linear",
      position: "right",
      display: true,
      grid: { drawOnChartArea: false },
      ticks: {
        color: BOOK_COLORS.ETH,
        callback(value) {
          return fmtNativeBookAmount(value, "ETH");
        },
      },
    },
  };
}

export function renderCumulativeSpotPnlChart() {
  const ctx = chartCanvasContext("chart-risk-capital");
  if (!ctx) return;
  destroyChart("riskCapital", "chart-risk-capital");

  const series = STATE.cumulativeSpotPnl;
  const closedMeta = series?.realized_count
    ? `${series.realized_count} ${i18n("closed groups", "已平倉組")}`
    : i18n("no closed groups", "尚無已平倉組");
  setText("risk-capital-meta", closedMeta);

  if (!series) {
    mountEmptyTimeSeriesChart("chart-risk-capital", "riskCapital");
    return;
  }

  const datasets = [];
  const books = visibleSpotPnlBooks();
  for (const book of books) {
    const rows = series.cumulative_by_book?.[book] || [];
    const data = finalizeCumulativeLineData(
      rows.map((r) => ({ x: dateToMs(r.date), y: num(r.pnl_native) }))
    );
    if (!data.length) continue;
    datasets.push({
      label: `${book} ${i18n("cum. spot PnL", "累積 spot 損益")}`,
      book,
      data,
      borderColor: BOOK_COLORS[book],
      backgroundColor: BOOK_COLORS[book] + "22",
      stepped: true,
      pointRadius: 0,
      borderWidth: 2,
    });
  }

  if (!datasets.length) {
    mountEmptyTimeSeriesChart("chart-risk-capital", "riskCapital");
    return;
  }

  const useDualAxis = spotPnlUsesDualAxis(datasets);
  if (useDualAxis) {
    for (const ds of datasets) {
      ds.yAxisID = SPOT_PNL_AXIS_IDS[ds.book];
    }
    setText(
      "risk-capital-hint",
      i18n(
        "BTC (left) and ETH (right) each use their own native scale; filter a book for a single axis.",
        "BTC（左軸）與 ETH（右軸）各自使用幣本位刻度；篩選單一帳本時改為單軸。"
      )
    );
  } else {
    setText(
      "risk-capital-hint",
      i18n(
        "Per-book cumulative realized PnL in native spot units (premium profit; BTC / ETH).",
        "各帳本累計已實現 spot 損益（幣本位 premium 獲利；BTC / ETH）。"
      )
    );
  }

  const base = chartCommonOptions();
  const flatPoints = datasets.flatMap((d) => d.data || []);
  const xBounds = suggestTimeScaleMinMax(flatPoints);
  setChartPanelEmpty("chart-risk-capital", { empty: false });
  try {
    STATE.charts.riskCapital = new globalThis.Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        ...base,
        scales: spotPnlChartScales(base, { datasets, xBounds }),
        plugins: {
          ...base.plugins,
          tooltip: {
            ...base.plugins.tooltip,
            callbacks: {
              label(context) {
                const book = context.dataset.book || "";
                const y = context.parsed?.y;
                return `${context.dataset.label}: ${fmtNativeBookAmount(y, book)}`;
              },
            },
          },
        },
      },
    });
  } catch (err) {
    console.error("spot pnl chart render failed", err);
    destroyChart("riskCapital", "chart-risk-capital");
    mountEmptyTimeSeriesChart("chart-risk-capital", "riskCapital");
  }
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
  destroyChart("cumPnl", "chart-cum-pnl");
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
          label: `${book} ${i18n("cum. stable PnL", "累積穩定幣損益")}`,
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
        label: i18n("Total cum. stable PnL", "累積穩定幣損益合計"),
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
  destroyChart("dailyPnl", "chart-daily-pnl");
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
  destroyChart("apr", "chart-apr");
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
